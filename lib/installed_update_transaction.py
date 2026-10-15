#!/usr/bin/env python3
"""Durable installed-driver artifact activation and boot-attempt reconciliation.

Only the candidate producer may activate a live target. The raw plan interface
is restricted to disposable test roots. SteamOS keeps OS-slot ownership.
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
CACHES = {'modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin',
          'modules.softdep', 'modules.symbols', 'modules.symbols.bin',
          'modules.builtin.bin', 'modules.builtin.alias.bin', 'modules.devname', 'modules.weakdep'}
PROJECT = 'open-gpu-kernel-modules-steamos-support'
GUARDIAN_FILES = ('lib/offline_generation.py', 'bootstrap/run_repair_with_interstitial.sh', 'lib/install_guardian_generation.py', 'lib/installed_boot_guard.py', 'bootstrap/install.sh', 'bootstrap/launch_desktop_companion.sh', 'bootstrap/launch_interstitial.sh', 'bootstrap/online_install.sh', 'bootstrap/recoveryctl.sh', 'bootstrap/run_guardian_with_interstitial.sh', 'bootstrap/show_recovery_console.sh', 'lib/atomic_output.py', 'lib/capture_bounded_command.py', 'lib/common.sh', 'lib/desktop_update_generations.py', 'lib/installed_driver_candidate.py', 'lib/installed_update_transaction.py', 'lib/interstitial_progress.py', 'lib/open_opemos_contract.py', 'lib/payload_receipt.py', 'lib/recovery_cached_product.py', 'lib/recovery_cached_receipt.py', 'lib/recovery_fallback_state.py', 'lib/recovery_policy.py', 'lib/recovery_release_plan.py', 'lib/recovery_status.py', 'lib/recovery_transaction.py', 'lib/run_in_process_group.py', 'lib/snapshot_install_input.py', 'lib/snapshot_target_execution.py', 'lib/update_recovery_grub_args.py', 'lib/validate_github_meta.py', 'lib/validate_interstitial_binary.py', 'lib/validate_recovery_install_path.py', 'lib/verify_initramfs.py', 'lib/verify_installed_modules.py', 'trust/desktop-update-signers.json')
GUARDIAN_HOME = 'home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/'
LINK_TARGETS = {'etc/systemd/system/default.target': ('/usr/lib/systemd/system/multi-user.target', '/usr/lib/systemd/system/graphical.target', '/lib/systemd/system/multi-user.target', '/lib/systemd/system/graphical.target'), 'etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service': ('../opemos-nvidia-guardian.service',), 'etc/systemd/system/multi-user.target.wants/opemos-interstitial.service': ('../opemos-interstitial.service',), 'etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer': ('../opemos-nvidia-repair.timer',), 'etc/systemd/system/timers.target.wants/opemos-nvidia-health.timer': ('../opemos-nvidia-health.timer',)}


def need(value, message):
    if not value:
        raise ValueError(message)


def read(path, cap):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and before.st_uid == os.geteuid() and before.st_size <= cap,
             'unsafe transaction artifact')
        data = bytearray()
        deadline = time.monotonic() + 120
        while True:
            need(time.monotonic() < deadline, 'artifact read deadline')
            part = os.read(fd, 1024 * 1024)
            if not part:
                break
            data.extend(part)
            need(len(data) <= cap, 'artifact overflow')
        after = os.fstat(fd)
        current = Path(path).lstat()
        keys = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                'st_size', 'st_mtime_ns', 'st_ctime_ns')
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


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_durable(path, data, mode=0o600):
    # atomic_output flushes the file, but deliberately does not fsync parents.
    # The installed transaction must persist the directory entry as well.
    atomic_write_bytes(path, data, mode=mode)
    fsync_dir(path.parent)


def durable(path, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    need(len(data) <= MAX_STATE, 'excessive transaction state')
    replace_durable(path, data)


def target(root, relative, kernel, allow_missing=False):
    need(type(relative) is str and relative == str(Path(relative))
         and not relative.startswith('/') and '..' not in Path(relative).parts,
         'unsafe transaction path')
    prefix = f'usr/lib/modules/{kernel}/'
    configs = {'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf',
               'etc/modprobe.d/98-opemos-recovery.conf',
               'etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf',
               'etc/default/grub', 'boot/grub/grub.cfg', 'efi/EFI/steamos/grub.cfg'}
    configs |= {x + '.opemos-disabled' for x in list(configs) if x.startswith('etc/')}
    states = {f'var/lib/{PROJECT}/' + x for x in (
        'installed-build-info.txt', 'installed-archive.txt', 'installed-kernel.txt',
        'installed-nvidia.txt', 'recovery/state.json', 'recovery/grub.before-fallback',
        'recovery/cached-repair-receipt.json')}
    guardian = {GUARDIAN_HOME + p for p in GUARDIAN_FILES}
    guardian |= {GUARDIAN_HOME + p for p in ('support-revision','nvidia-version','interstitial.sha256','bin/opemos-interstitial')}
    guardian |= {'etc/systemd/system/' + name for name in ('opemos-nvidia-guardian.service','opemos-nvidia-health.service','opemos-nvidia-health.timer','opemos-interstitial.service','opemos-nvidia-repair.service','opemos-nvidia-repair.timer')}
    guardian |= {'etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair', 'etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf'}
    allowed = relative in guardian or relative in configs | states or relative in LINK_TARGETS
    allowed |= bool(re.fullmatch(r'boot/initramfs-[A-Za-z0-9_.+-]+\.img', relative))
    allowed |= relative in {prefix + x for x in CACHES}
    allowed |= relative in {prefix + 'updates/open-gpu-kernel-modules-steamos/' + x + '.ko.zst'
                           for x in MODULES}
    need(allowed, 'unplanned installed driver/boot/receipt effect: ' + relative)
    path = root / relative
    if relative in LINK_TARGETS and path.is_symlink():
        link_data(path)
    for parent in ([path.parent, *path.parent.parents] if relative in LINK_TARGETS else [path, *path.parents]):
        need(not parent.is_symlink(), 'target alias refused')
        if parent == root:
            break
    need(allow_missing or path.parent.is_dir(), 'target parent missing')
    return path


def root_identity(root):
    need(root.is_absolute() and root.resolve() == root, 'exact root required')
    info = root.stat()
    release = root / 'etc/os-release'
    if release.is_symlink():
        need(os.readlink(release) in ('../usr/lib/os-release', '/usr/lib/os-release'),
             'unsupported os-release alias')
        release = root / 'usr/lib/os-release'
    return [info.st_dev, info.st_ino, digest(read(release, 65536))]


def guarded_store(store):
    need(store.is_absolute() and store.resolve() == store, 'exact store required')
    info = store.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, 'private transaction store required')
    fd = os.open(store / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
             'transaction lock unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need((info.st_dev, info.st_ino) == (os.lstat(store / 'lock').st_dev,
                                          os.lstat(store / 'lock').st_ino), 'lock changed')
        return fd
    except BaseException:
        os.close(fd)
        raise


def checkpoint(phase):
    # Source tests now request abrupt VM off/restart; this is an observation
    # pause only. No production shutdown, process-kill or host fault injector.
    if (os.environ.get('PROJECT_TEST_MODE') == '1'
            and os.environ.get('OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE') == phase):
        time.sleep(30)


def link_key(path):
    return next((name for name in LINK_TARGETS if path.parts[-len(Path(name).parts):] == Path(name).parts), None)


def link_data(path):
    value = os.readlink(path)
    need(link_key(path) is not None and value in LINK_TARGETS[link_key(path)], 'unsupported managed alias')
    return b'OP_DEFAULT_TARGET_LINK\0' + value.encode('ascii')


def artifact_data(path):
    if path.is_symlink():
        need(link_key(path) is not None, 'unplanned artifact symlink')
        return link_data(path)
    return read(path, MAX_FILE)


def current_hash(path):
    return digest(artifact_data(path)) if os.path.lexists(path) else None


def load_intent(root, store):
    intent = parse(read(store / 'intent.json', MAX_STATE))
    need(type(intent) is dict and intent.get('schemaVersion') == 2
         and set(intent) == {'schemaVersion', 'kernel', 'rootIdentity', 'phase', 'files',
                             'bootImages', 'target', 'preparedBootId', 'attemptBootId',
                             'authority', 'readonlyBefore', 'action', 'profile'}, 'closed v2 intent required')
    need(type(intent['kernel']) is str and re.fullmatch(r'[A-Za-z0-9_.+-]{1,192}', intent['kernel']),
         'invalid kernel')
    need(intent['rootIdentity'] == root_identity(root) or
         intent['action'] == 'guardian-update' and intent['phase'] == 'healthy',
         'root/slot changed; pending recovery cannot select an OS slot')
    need(intent['action'] in ('update', 'enable-fallback', 'disable-fallback', 'guardian-update')
         and intent['profile'] in ('console', 'igpu-desktop', 'nouveau-experimental'), 'invalid action/profile')
    need(type(intent['readonlyBefore']) is bool and type(intent['authority']) is dict
         and type(intent['target']) is dict and type(intent['bootImages']) is list
         and len(intent['bootImages']) <= 32, 'invalid reconciliation context')
    for key in ('preparedBootId', 'attemptBootId'):
        need((intent[key] is None and key == 'attemptBootId') or
             type(intent[key]) is str and re.fullmatch('[0-9a-f-]{36}', intent[key]),
             'invalid boot identity')
    need(all(type(x) is str and re.fullmatch(r'boot/initramfs-[A-Za-z0-9_.+-]+\.img', x)
             for x in intent['bootImages']), 'invalid retained boot image set')
    rows = intent['files']
    need(type(rows) is list and 0 < len(rows) <= MAX_FILES
         and len({r['path'] for r in rows}) == len(rows), 'invalid artifact set')
    for index, row in enumerate(rows):
        need(set(row) == {'path', 'backup', 'candidate', 'oldSHA256', 'newSHA256', 'oldMode', 'newMode'},
             'invalid artifact fields')
        target(root, row['path'], intent['kernel'], allow_missing=True)
        for key, prefix in [('backup', 'previous-'), ('candidate', 'candidate-')]:
            need(row[key] is None or row[key] == prefix + str(index), 'invalid retained reference')
        need((row['backup'] is None) == (row['oldSHA256'] is None)
             and (row['candidate'] is None) == (row['newSHA256'] is None),
             'retained reference/hash presence differs')
        for key in ('oldSHA256', 'newSHA256'):
            need(row[key] is None or type(row[key]) is str and re.fullmatch('[0-9a-f]{64}', row[key]),
                 'invalid retained hash')
        need(all(type(row[k]) is int and 0 <= row[k] <= 0o777 for k in ('oldMode', 'newMode')),
             'invalid retained mode')
    need(intent['phase'] in ('prepared', 'activating', 'pending-health', 'rolling-back', 'restored', 'healthy'),
         'unknown phase')
    return intent


def verify_set(root, intent, field):
    for row in intent['files']:
        path = target(root, row['path'], intent['kernel'], allow_missing=True)
        need(current_hash(path) == row[field], 'installed generation differs: ' + row['path'])
        if row[field] is not None:
            expected_mode = row['oldMode' if field == 'oldSHA256' else 'newMode']
            need(stat.S_IMODE(path.lstat().st_mode) == expected_mode,
                 'installed generation mode differs: ' + row['path'])


def place(path, data, mode, expected_hash):
    if data is None:
        # Only an exact current candidate/original can be removed by recovery.
        need(current_hash(path) == expected_hash, 'unknown file; deletion refused')
        if os.path.lexists(path):
            os.unlink(path)
            fsync_dir(path.parent)
        return
    missing = []
    node = path.parent
    while not node.exists():
        missing.append(node)
        node = node.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        fsync_dir(directory.parent)
    if link_key(path) is not None:
        prefix = b'OP_DEFAULT_TARGET_LINK\0'
        need(data.startswith(prefix), 'managed target must be a closed symlink')
        value = data[len(prefix):].decode('ascii')
        need(value in LINK_TARGETS[link_key(path)], 'unsafe managed target')
        import uuid
        temporary = path.parent / ('.opemos-default-' + uuid.uuid4().hex)
        os.symlink(value, temporary)
        try:
            os.replace(temporary, path)
            fsync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        replace_durable(path, data, mode)


def recover(root, store):
    if not (store / 'intent.json').exists():
        return {'state': 'no-pending-intent'}
    intent = load_intent(root, store)
    if intent['phase'] in ('restored', 'healthy'):
        verify_set(root, intent, 'oldSHA256' if intent['phase'] == 'restored' else 'newSHA256')
        return {'state': intent['phase'], 'rebootHealthVerified': intent['phase'] == 'healthy'}
    for row in intent['files']:
        path = target(root, row['path'], intent['kernel'], allow_missing=True)
        need(current_hash(path) in (row['oldSHA256'], row['newSHA256']), 'foreign artifact; hold')
        if os.path.lexists(path):
            need(stat.S_IMODE(path.lstat().st_mode) in (row['oldMode'], row['newMode']),
                 'foreign artifact mode; hold')
        if row['backup'] is not None:
            need(digest(read(store / row['backup'], MAX_FILE)) == row['oldSHA256'], 'rollback damaged')
    intent['phase'] = 'rolling-back'
    durable(store / 'intent.json', intent)
    for index, row in enumerate(reversed(intent['files'])):
        old = None if row['backup'] is None else read(store / row['backup'], MAX_FILE)
        path = target(root, row['path'], intent['kernel'], allow_missing=True)
        if (current_hash(path) != row['oldSHA256'] or
                os.path.lexists(path) and stat.S_IMODE(path.lstat().st_mode) != row['oldMode']):
            observed = current_hash(path)
            need(observed in (row['oldSHA256'], row['newSHA256']), 'artifact changed during recovery')
            place(path, old, row['oldMode'], observed)
        checkpoint(f'restore:{index}')
    verify_set(root, intent, 'oldSHA256')
    intent['phase'] = 'restored'
    durable(store / 'intent.json', intent)
    return {'state': 'restored', 'rebootHealthVerified': False, 'rebootRequired': True}


def heal_known_generation(root, store):
    """Redo exact owned healthy bytes; retain unknown corrupt contents first.

    This is not rollback to corrupt data. A separate durable redo intent lets
    interrupted repair converge to the already authenticated retained set.
    Aliases, unknown ownership/modes and unavailable good bytes still hold.
    """
    intent=load_intent(root,store)
    need(intent['phase'] in ('healthy','restored'),'self-heal requires a verified terminal generation')
    which='new' if intent['phase']=='healthy' else 'old'
    pointer=store/'heal.json'
    if pointer.exists():
        receipt=parse(read(pointer,MAX_STATE))
        need(set(receipt)=={'schemaVersion','state','generationPhase','files'} and
             receipt['schemaVersion']==1 and receipt['generationPhase']==intent['phase'],'invalid heal intent')
        if receipt['state']=='complete':
            try:
                verify_set(root,intent,which+'SHA256')
                return {'state':intent['phase'],'selfHeal':'already-complete'}
            except ValueError:
                import uuid
                need(len(list(store.glob('heal-history-*'))) < 8,'retained heal bound')
                os.rename(pointer,store/('heal-history-'+uuid.uuid4().hex))
                fsync_dir(store)
                receipt=None
        else:
            need(receipt['state']=='prepared','unknown heal phase')
    else:
        receipt=None
    if receipt is None:
        rows=[]
        total=0
        for index,row in enumerate(intent['files']):
            path=target(root,row['path'],intent['kernel'],allow_missing=True)
            expected=row[which+'SHA256']
            observed=current_hash(path)
            need(expected is not None or observed is None, 'new unknown file at an absent owned path; preserve it')
            if observed==expected:
                continue
            if os.path.lexists(path):
                need(stat.S_IMODE(path.lstat().st_mode)==row[which+'Mode'],
                     'unknown permissions; automatic repair refused')
                data=artifact_data(path)
                total+=len(data)
                need(total<=MAX_BYTES,'corruption evidence budget')
                name='foreign-'+str(index)+'-'+digest(data)
                if not (store/name).exists():replace_durable(store/name,data)
            else:
                name=None
            rows.append({'index':index,'observedSHA256':observed,'evidence':name})
        receipt={'schemaVersion':1,'state':'prepared','generationPhase':intent['phase'],'files':rows}
        durable(pointer,receipt)
    need(type(receipt['files']) is list and len(receipt['files'])<=MAX_FILES,'invalid heal rows')
    for entry in receipt['files']:
        need(set(entry)=={'index','observedSHA256','evidence'} and type(entry['index']) is int
             and 0<=entry['index']<len(intent['files']),'invalid heal index')
        row=intent['files'][entry['index']]
        if entry['evidence'] is not None:
            need(entry['evidence']=='foreign-'+str(entry['index'])+'-'+str(entry['observedSHA256']),
                 'invalid corruption reference')
            need(digest(read(store/entry['evidence'],MAX_FILE))==entry['observedSHA256'],
                 'corruption evidence changed')
        source=row['candidate' if which=='new' else 'backup']
        data=None if source is None else read(store/source,MAX_FILE)
        need((None if data is None else digest(data))==row[which+'SHA256'],'good repair bytes changed')
        path=target(root,row['path'],intent['kernel'],allow_missing=True)
        current=current_hash(path)
        need(current in (entry['observedSHA256'],row[which+'SHA256']),'new foreign bytes during repair')
        if current!=row[which+'SHA256']:
            place(path,data,row[which+'Mode'],current)
        checkpoint('heal:'+str(entry['index']))
    verify_set(root,intent,which+'SHA256')
    receipt['state']='complete'
    durable(pointer,receipt)
    return {'state':intent['phase'],'selfHeal':'restored-known-generation','rebootHealthVerified':False}


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def prepare(root, store, plan_path, *, context=None):
    need(not (store / 'intent.json').exists(), 'existing intent requires reconciliation')
    plan = parse(read(plan_path, MAX_STATE))
    need(set(plan) == {'schemaVersion', 'kernel', 'rootIdentity', 'completeWriterSet', 'files'}
         and plan['schemaVersion'] == 1 and plan['completeWriterSet'] is True,
         'complete producer plan required')
    need(type(plan['kernel']) is str and re.fullmatch(r'[A-Za-z0-9_.+-]{1,192}', plan['kernel']),
         'invalid plan kernel')
    need(plan['rootIdentity'] == root_identity(root), 'target changed')
    rows = plan['files']
    need(0 < len(rows) <= MAX_FILES and len({r['path'] for r in rows}) == len(rows), 'invalid plan')
    # Boot artifacts are always last; no caller-provided ordering override.
    rows = sorted(rows, key=lambda r: (r['path'] != 'etc/systemd/system/opemos-nvidia-guardian.service', r['path'].startswith(('boot/', 'efi/')), r['path']))
    total = 0
    saved = []
    for index, row in enumerate(rows):
        need(set(row) == {'path', 'source', 'sha256'}, 'closed plan row required')
        path = target(root, row['path'], plan['kernel'], allow_missing=True)
        need(not path.is_relative_to(store), 'cannot overwrite transaction evidence')
        old = artifact_data(path) if os.path.lexists(path) else None
        new = None if row['source'] is None else read(Path(row['source']), MAX_FILE)
        need((None if new is None else digest(new)) == row['sha256'], 'candidate changed')
        total += len(old or b'') + len(new or b'')
        need(total <= MAX_BYTES, 'complete retained artifact budget')
        old_mode = stat.S_IMODE(path.lstat().st_mode) if old is not None else 0o644
        new_mode = stat.S_IMODE(Path(row['source']).stat().st_mode) if new is not None else 0o644
        backup = None if old is None else f'previous-{index}'
        candidate = None if new is None else f'candidate-{index}'
        if old is not None:
            replace_durable(store / backup, old)
        if new is not None:
            replace_durable(store / candidate, new)
        if row['path'] in LINK_TARGETS and new is not None:
            new_mode = 0o777
        saved.append({'path': row['path'], 'backup': backup, 'candidate': candidate,
                      'oldSHA256': None if old is None else digest(old),
                      'newSHA256': row['sha256'], 'oldMode': old_mode, 'newMode': new_mode})
    context = context or {'bootImages': [], 'target': {}, 'authority': {}, 'readonlyBefore': False, 'action': 'update', 'profile': 'console'}
    intent = {'schemaVersion': 2, 'kernel': plan['kernel'], 'rootIdentity': plan['rootIdentity'],
              'phase': 'prepared', 'files': saved, 'preparedBootId': boot_id(), 'attemptBootId': None,
              **context}
    durable(store / 'intent.json', intent)
    checkpoint('prepared')
    return intent


def commit_prepared(root, store):
    intent = load_intent(root, store)
    need(intent['phase'] == 'prepared', 'only a fresh prepared transaction may activate')
    verify_set(root, intent, 'oldSHA256')
    intent['phase'] = 'activating'
    durable(store / 'intent.json', intent)
    for index, row in enumerate(intent['files']):
        path = target(root, row['path'], intent['kernel'], allow_missing=True)
        need(current_hash(path) == row['oldSHA256'], 'prestate changed')
        new = None if row['candidate'] is None else read(store / row['candidate'], MAX_FILE)
        need(new is None or digest(new) == row['newSHA256'], 'retained candidate changed')
        place(path, new, row['newMode'], row['oldSHA256'])
        checkpoint(f'activated:{index}')
    verify_set(root, intent, 'newSHA256')
    intent['phase'] = 'pending-health'
    durable(store / 'intent.json', intent)
    checkpoint('pending-health')
    return {'state': 'pending-health', 'rebootHealthVerified': False, 'rebootRequired': True}


def activate(root, store, plan_path, *, context=None):
    prepare(root, store, plan_path, context=context)
    return commit_prepared(root, store)


def startup(root, store):
    if not (store / 'intent.json').exists():
        return {'state': 'no-pending-intent'}
    intent = load_intent(root, store)
    if intent['phase'] in ('healthy','restored'):
        return heal_known_generation(root,store)
    if intent['phase'] != 'pending-health':
        return recover(root, store)
    verify_set(root, intent, 'newSHA256')
    now = boot_id()
    if now == intent['preparedBootId']:
        return {'state': 'pending-health', 'rebootRequired': True}
    if intent['attemptBootId'] is None:
        intent['attemptBootId'] = now
        durable(store / 'intent.json', intent)
        return {'state': 'pending-health', 'firstBootAttempt': True}
    if intent['attemptBootId'] != now:
        return recover(root, store)
    return {'state': 'pending-health', 'firstBootAttempt': True}


def require_loaded_identity(root, kernel, nvidia):
    # Loaded NVIDIA evidence is distinct from file verification and cold boot.
    version = Path('/sys/module/nvidia/version').read_text().strip()
    need(version == nvidia, 'loaded NVIDIA health unavailable/mismatched')
    # Compare loaded identities to the actual selected on-disk generation. A
    # matching marketing version alone cannot distinguish two 575 builds.
    import subprocess
    for name in ('nvidia', 'nvidia-modeset', 'nvidia-drm'):
        disk = root / f'usr/lib/modules/{kernel}/updates/open-gpu-kernel-modules-steamos/{name}.ko.zst'
        measured = subprocess.run(['/usr/bin/modinfo', '-F', 'srcversion', str(disk)],
                                  check=True, stdout=subprocess.PIPE, timeout=10).stdout
        need(0 < len(measured) <= 256, 'module source identity unavailable')
        loaded = Path('/sys/module') / name.replace('-', '_') / 'srcversion'
        need(loaded.read_text().strip() == measured.decode('ascii').strip(),
             'loaded module differs from activated generation: ' + name)
    return {'kernelVersion': kernel, 'nvidiaVersion': nvidia, 'bootId': boot_id(),
            'loadedModules': ['nvidia','nvidia-modeset','nvidia-drm'], 'GPUAcceptance': False}


def acknowledge(root, store):
    intent = load_intent(root, store)
    need(intent['phase'] == 'pending-health' and intent['attemptBootId'] == boot_id()
         and intent['preparedBootId'] != boot_id(), 'health requires this actual new boot attempt')
    need(os.uname().release == intent['kernel'], 'running kernel differs from candidate')
    verify_set(root, intent, 'newSHA256')
    if intent['action'] == 'guardian-update':
        # Called only after ordinary guardian/driver health succeeds. Source
        # integrity and actual boot registration are still verified above.
        pass
    elif intent['action'] == 'enable-fallback':
        need(not Path('/sys/module/nvidia').exists(), 'fallback boot still loaded NVIDIA')
        if intent['profile'] == 'console':
            need(link_data(root / 'etc/systemd/system/default.target').endswith(b'/multi-user.target'),
                 'console fallback boot target differs')
    else:
        require_loaded_identity(root, intent['kernel'], intent['target']['nvidiaVersion'])
    intent['phase'] = 'healthy'
    durable(store / 'intent.json', intent)
    return {'state': 'healthy', 'rebootHealthVerified': True,
            'GPUDisplayOrHardwareAcceptance': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('activate', 'recover', 'require-plan', 'stage-check',
                                           'update', 'startup', 'health', 'inspect'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--store', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--archive-sha256')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--support', type=Path)
    parser.add_argument('--policy-root', type=Path)
    parser.add_argument('--action', default='update', choices=('update', 'enable-fallback', 'disable-fallback'))
    parser.add_argument('--profile', default='console')
    parser.add_argument('--kernel')
    parser.add_argument('--nvidia')
    args = parser.parse_args()
    if args.command in ('require-plan', 'stage-check'):
        from installed_driver_candidate import stage_allowed
        stage_allowed(args.root)
        return
    if args.command in ('startup', 'health', 'inspect') and args.root == Path('/'):
        from installed_driver_candidate import reconcile_controller
        need(args.policy_root is not None, 'installed policy root required')
        print(json.dumps(reconcile_controller(args.root, args.policy_root, args.command == 'health', args.command == 'inspect'), sort_keys=True))
        return
    if args.command == 'update':
        from installed_driver_candidate import update
        print(json.dumps(update(args), sort_keys=True))
        return
    need(args.store is not None, 'transaction store required')
    need(args.root != Path('/') or args.command in ('startup', 'health'),
         'raw installed activation/recovery must use owning controller')
    fd = guarded_store(args.store)
    try:
        if args.command == 'startup':
            answer = startup(args.root, args.store)
        elif args.command == 'health':
            answer = acknowledge(args.root, args.store)
        else:
            need(os.environ.get('PROJECT_TEST_MODE') == '1', 'raw plan is test-only')
            answer = recover(args.root, args.store) if args.command == 'recover' else activate(args.root, args.store, args.plan)
        print(json.dumps(answer, sort_keys=True))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
