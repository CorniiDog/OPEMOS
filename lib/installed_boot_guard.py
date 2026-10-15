#!/usr/bin/env python3
"""Small persistent guardian bootstrap, independent of mutable guardian imports.

Installed before changing the guardian unit. It opens only exact registered
transaction engines, then runs ordinary guardian code after reconciliation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

HOME = Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery-bootstrap')
GUARDIAN = Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')


def read(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or before.st_mode & 0o022 or not 0 < before.st_size <= maximum):
            raise ValueError('unsafe registered bootstrap input')
        data = bytearray()
        while len(data) <= maximum:
            part = os.read(descriptor, min(65536, maximum + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        after = os.fstat(descriptor)
        current = path.lstat()
        keys = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_uid','st_mode','st_nlink')
        if len(data) != before.st_size or any(getattr(before,k) != getattr(after,k) or
                                              getattr(after,k) != getattr(current,k) for k in keys):
            raise ValueError('registered input changed')
        return bytes(data)
    finally:
        os.close(descriptor)


def engine(record):
    if set(record) != {'store', 'engine'}:
        raise ValueError('invalid registered engine')
    store = Path(record['store'])
    # Exact created app or installed-driver stores only. Never a caller path.
    import re
    if not (re.fullmatch(re.escape(str(HOME)) + r'/app-[0-9a-f]{32}', str(store)) or
            re.fullmatch(re.escape(str(GUARDIAN)) + r'/installed-updates/txn-[0-9a-f]{32}', str(store))):
        raise ValueError('unowned bootstrap store')
    for parent in (HOME, store, store / 'engine'):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('bootstrap directory unsafe')
    if set(record['engine']) != {'installed_update_transaction.py', 'atomic_output.py'}:
        raise ValueError('incomplete recovery engine')
    for name, expected in record['engine'].items():
        data = read(store / 'engine' / name, 256 * 1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('registered recovery engine changed')
    # These files live in a root-only directory and are unchanged by the
    # mutable guardian transaction. No import from partially replaced code.
    sys.path.insert(0, str(store / 'engine'))
    sys.modules.pop('atomic_output', None)
    spec = importlib.util.spec_from_file_location('registered_transaction', store / 'engine/installed_update_transaction.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, store


def reconcile():
    if not (HOME / 'current.json').exists():
        raise ValueError('bootstrap registry missing; preserve source/driver operations')
    document = json.loads(read(HOME / 'current.json', 16384))
    if set(document) != {'schemaVersion','app','driver'} or document['schemaVersion'] != 1:
        raise ValueError('invalid bootstrap registry')
    # Restore mixed source files before importing guardian policy/helpers.
    # The independently retained driver engine does not depend on app imports.
    custody_hold=False
    for name in ('app','driver'):
        if document[name] is None:
            continue
        module, store = engine(document[name])
        lock = module.guarded_store(store)
        try:
            raw = module.parse(module.read(store / 'intent.json', module.MAX_STATE))
            if name == 'driver' and raw['rootIdentity'] != module.root_identity(Path('/')):
                if raw['phase'] not in ('healthy','restored'):
                    raise ValueError('pending driver belongs to another OS root; preserve and hold')
                print(json.dumps({'component':name,'state':'retarget-required'}),flush=True)
                continue
            intent = module.load_intent(Path('/'), store)
            import subprocess
            status=subprocess.run(['/usr/bin/steamos-readonly','status'],check=True,
                                  stdout=subprocess.PIPE,timeout=10)
            if len(status.stdout)>4096:raise ValueError('readonly status excessive')
            readonly = intent['readonlyBefore'] or b'enabled' in status.stdout.lower()
            if readonly:
                subprocess.run(['/usr/bin/steamos-readonly','disable'],check=True,timeout=30)
            try:
                result = module.startup(Path('/'), store)
                if name=='app' and os.path.lexists(GUARDIAN/'pending-install.json'):
                    reconciled=module.load_intent(Path('/'),store)
                    # Still reconcile the independent driver engine below, but
                    # never hand pending new-product success to rolled-back old
                    # mutable source that does not know its custody contract.
                    custody_hold=reconciled['phase']=='restored'
                print(json.dumps({'component':name,**result}), flush=True)
            finally:
                if readonly:
                    subprocess.run(['/usr/bin/steamos-readonly','enable'],check=True,timeout=30)
        finally:
            os.close(lock)

    if custody_hold:
        raise ValueError('app rolled back with retained install; prior boot preserved, exact source recovery required')


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise ValueError('owning boot guardian requires root and no arguments')
    import fcntl
    lockpath=Path('/run/lock/open-gpu-kernel-modules-steamos-support.lock')
    descriptor=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o666)
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise ValueError('unsafe lifecycle lock')
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reconcile()
    finally:
        os.close(descriptor)
    script = GUARDIAN / 'bootstrap/run_guardian_with_interstitial.sh'
    os.execv('/bin/bash', ['/bin/bash',str(script)])


if __name__ == '__main__':
    main()
