#!/usr/bin/env python3
"""Confined installed-update artifact intent; staged plans only, no opaque writers.

The owning authenticated producer must stage *all* files before calling this
module. Unknown/torn plans refuse. Nothing here chooses a SteamOS OS slot.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from atomic_output import atomic_write_bytes

MAX_FILE = 128 * 1024**2
MAX_FILES = 256
MAX_BYTES = 8 * 1024**3
MAX_STATE = 256 * 1024
MODULES = {'nvidia', 'nvidia-drm', 'nvidia-modeset', 'nvidia-peermem', 'nvidia-uvm'}


def need(value, message):
    if not value:
        raise ValueError(message)


def read(path, cap):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and before.st_uid == os.geteuid() and before.st_size <= cap,
             'unsafe transaction artifact')
        data = bytearray()
        while True:
            part = os.read(fd, 1024 * 1024)
            if not part:
                break
            data.extend(part)
            need(len(data) <= cap, 'artifact overflow')
        after = os.fstat(fd)
        current = Path(path).lstat()
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        need(len(data) == before.st_size and all(
            getattr(before, k) == getattr(after, k) == getattr(current, k)
            for k in keys), 'artifact changed')
        return bytes(data)
    finally:
        os.close(fd)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'duplicate transaction field')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def durable(path, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    need(len(data) <= MAX_STATE, 'excessive transaction state')
    atomic_write_bytes(path, data, mode=0o600)


def target(root, relative, kernel):
    need(type(relative) is str and not relative.startswith('/')
         and '..' not in Path(relative).parts, 'unsafe transaction path')
    module_prefix = f'usr/lib/modules/{kernel}/updates/open-gpu-kernel-modules-steamos/'
    allowed = relative in {
        'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf',
        'etc/modprobe.d/98-opemos-recovery.conf',
        'etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf',
        'etc/default/grub', 'boot/grub/grub.cfg', 'efi/EFI/steamos/grub.cfg',
    }
    allowed |= relative.startswith('var/lib/open-gpu-kernel-modules-steamos-support/')
    allowed |= bool(re.fullmatch(r'boot/initramfs-[A-Za-z0-9_.+-]+\.img', relative))
    if relative.startswith(module_prefix):
        allowed |= relative[len(module_prefix):] in {name + '.ko.zst' for name in MODULES}
    need(allowed, 'artifact outside installed driver/boot/receipt scope')
    path = root / relative
    for parent in [path, *path.parents]:
        need(not parent.is_symlink(), 'target alias refused')
        if parent == root:
            break
    need(path.parent.is_dir(), 'target parent must already exist')
    return path


def root_identity(root):
    need(root.is_absolute() and root.resolve() == root, 'exact root required')
    info = root.stat()
    return [info.st_dev, info.st_ino, digest(read(root / 'etc/os-release', 65536))]


def guarded_store(store):
    need(store.is_absolute() and store.resolve() == store, 'exact store required')
    info = store.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, 'private transaction store required')
    fd = os.open(store / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
         and info.st_uid == os.geteuid(), 'transaction lock unsafe')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def checkpoint(phase):
    # Development-only observation hooks, never a production fault injector.
    if (os.environ.get('PROJECT_TEST_MODE') == '1'
            and os.environ.get('OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE') == phase):
        time.sleep(30)


def recover(root, store):
    """Idempotent rollback only when every current artifact has exact custody."""
    journal = store / 'intent.json'
    if not journal.exists():
        return {'state': 'no-pending-intent'}
    intent = parse(read(journal, MAX_STATE))
    need(type(intent) is dict and set(intent) == {
        'schemaVersion', 'kernel', 'rootIdentity', 'phase', 'files'}
        and intent['schemaVersion'] == 1, 'closed intent required')
    need(type(intent['kernel']) is str
         and re.fullmatch(r'[A-Za-z0-9_.+-]{1,192}', intent['kernel']),
         'intent kernel invalid')
    need(type(intent['files']) is list and 0 < len(intent['files']) <= MAX_FILES,
         'intent artifact count invalid')
    need(len({r['path'] for r in intent['files']}) == len(intent['files']),
         'intent duplicate artifact')
    for index, row in enumerate(intent['files']):
        need(set(row) == {'path', 'backup', 'candidate', 'oldSHA256',
                          'newSHA256', 'oldMode'}, 'closed intent artifact required')
        need(row['backup'] == f'previous-{index}'
             and row['candidate'] == f'candidate-{index}'
             and type(row['oldMode']) is int and 0 <= row['oldMode'] <= 0o777,
             'intent reference or mode invalid')
        need(all(type(row[k]) is str and re.fullmatch(r'[0-9a-f]{64}', row[k])
                 for k in ('oldSHA256', 'newSHA256')), 'intent hash invalid')
    need(intent['rootIdentity'] == root_identity(root), 'root/slot changed')
    need(intent['phase'] in ('prepared', 'activating', 'pending-health', 'rolling-back', 'restored'),
         'transaction phase unknown')
    if intent['phase'] == 'restored':
        for row in intent['files']:
            path = target(root, row['path'], intent['kernel'])
            need(path.exists() and digest(read(path, MAX_FILE)) == row['oldSHA256'],
                 'restored generation changed')
        return {'state': 'restored', 'rebootHealthVerified': False}
    # Validate the entire set and all backups BEFORE restoring any file. A
    # foreign modification/torn backup retains evidence, never blind replay.
    for row in intent['files']:
        path = target(root, row['path'], intent['kernel'])
        need(path.exists() and digest(read(path, MAX_FILE)) in
             (row['oldSHA256'], row['newSHA256']), 'unknown installed artifact; hold')
        need(digest(read(store / row['backup'], MAX_FILE)) == row['oldSHA256'],
             'rollback artifact corrupted')
    intent['phase'] = 'rolling-back'
    durable(journal, intent)
    for index, row in enumerate(reversed(intent['files'])):
        path = target(root, row['path'], intent['kernel'])
        old = read(store / row['backup'], MAX_FILE)
        atomic_write_bytes(path, old, mode=row['oldMode'])
        checkpoint(f'restore:{index}')
    intent['phase'] = 'restored'
    durable(journal, intent)
    return {'state': 'restored', 'rebootHealthVerified': False}


def activate(root, store, plan_path):
    need(set(os.listdir(store)) <= {'lock'}, 'existing store/evidence must be preserved; use a new store')
    plan = parse(read(plan_path, MAX_STATE))
    need(set(plan) == {'schemaVersion', 'kernel', 'rootIdentity', 'completeWriterSet', 'files'}
         and plan['schemaVersion'] == 1 and plan['completeWriterSet'] is True,
         'closed complete staged writer plan required')
    need(re.fullmatch(r'[A-Za-z0-9_.+-]{1,192}', plan['kernel'])
         and plan['rootIdentity'] == root_identity(root), 'exact kernel/root required')
    rows = plan['files']
    need(type(rows) is list and 0 < len(rows) <= MAX_FILES, 'bounded artifact set required')
    need(len({r['path'] for r in rows}) == len(rows), 'duplicate artifact target')
    saved = []
    total = 0
    for index, row in enumerate(rows):
        need(set(row) == {'path', 'source', 'sha256'}, 'closed artifact row required')
        destination = target(root, row['path'], plan['kernel'])
        need(not destination.is_relative_to(store), 'transaction cannot replace its own evidence')
        # New/absent files are intentionally refused: restore must not delete
        # unknown data or invent the pre-update boot path.
        old = read(destination, MAX_FILE)
        new = read(Path(row['source']), MAX_FILE)
        need(digest(new) == row['sha256'], 'candidate differs from staged plan')
        total += len(old) + len(new)
        need(total <= MAX_BYTES, 'complete candidate/rollback budget exceeded')
        backup = f'previous-{index}'
        atomic_write_bytes(store / backup, old, mode=0o600)
        saved.append({'path': row['path'], 'backup': backup,
                      'oldSHA256': digest(old), 'newSHA256': digest(new),
                      'oldMode': stat.S_IMODE(destination.stat().st_mode)})
        atomic_write_bytes(store / f'candidate-{index}', new, mode=0o600)
        saved[-1]['candidate'] = f'candidate-{index}'
    intent = {'schemaVersion': 1, 'kernel': plan['kernel'],
              'rootIdentity': plan['rootIdentity'], 'phase': 'prepared', 'files': saved}
    durable(store / 'intent.json', intent)
    checkpoint('prepared')
    intent['phase'] = 'activating'
    durable(store / 'intent.json', intent)
    for index, row in enumerate(saved):
        new = read(store / row['candidate'], MAX_FILE)
        need(digest(new) == row['newSHA256'], 'staged candidate changed')
        path = target(root, row['path'], plan['kernel'])
        need(digest(read(path, MAX_FILE)) == row['oldSHA256'], 'prestate changed')
        atomic_write_bytes(path, new, mode=row['oldMode'])
        checkpoint(f'activated:{index}')
    intent['phase'] = 'pending-health'
    durable(store / 'intent.json', intent)
    checkpoint('pending-health')
    return {'state': 'pending-health', 'rebootHealthVerified': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('activate', 'recover', 'require-plan'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--store', type=Path)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.command == 'require-plan':
        # Existing opaque in-place writers must never bypass staged activation.
        raise ValueError('installed update requires complete staged artifact plan; in-place writer refused')
    need(os.environ.get('PROJECT_TEST_MODE') == '1' and args.root != Path('/'),
         'installed transaction prototype is disabled outside disposable test roots')
    need(args.store is not None, 'transaction store required')
    fd = guarded_store(args.store)
    try:
        result = (recover(args.root, args.store) if args.command == 'recover' else
                  activate(args.root, args.store, args.plan))
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
