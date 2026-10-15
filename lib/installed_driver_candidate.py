#!/usr/bin/env python3
"""Installed NVIDIA candidate producer. All opaque commands run in an overlay.

No downloads, OS-slot selection, or package-upgrade policy is defined here.
The existing authenticated caller supplies the exact driver archive/cache.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import installed_update_transaction as tx
from payload_receipt import verify_receipt_evidence
from recovery_cached_receipt import commit as cached_commit
from recovery_cached_product import load as cached_load, read_regular

PROJECT = 'open-gpu-kernel-modules-steamos-support'
MAX_INPUT = 512 * 1024**2
CACHE_NAMES = {'modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin',
               'modules.softdep', 'modules.symbols', 'modules.symbols.bin',
               'modules.builtin.bin', 'modules.builtin.alias.bin', 'modules.devname',
               'modules.weakdep'}


def command(argv, timeout=600):
    # Retain existing process-group wrapper; a candidate PID namespace has
    # --kill-child and terminates all of its processes when PID1 exits.
    import signal
    wrapper = Path(__file__).with_name('run_in_process_group.py')
    process = subprocess.Popen([sys.executable, str(wrapper), *map(str, argv)])
    try:
        numeric = process.wait(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise
    tx.need(numeric == 0, 'candidate command failed: ' + str(argv[0]))


def writable_root(enabled):
    if enabled:
        command(['/usr/bin/steamos-readonly', 'disable'], 30)


def readonly_enabled():
    p = subprocess.run(['/usr/bin/steamos-readonly', 'status'], check=True,
                       stdout=subprocess.PIPE, timeout=10)
    tx.need(len(p.stdout) <= 4096, 'readonly status excessive')
    return b'enabled' in p.stdout.lower()


def stage_allowed(root):
    """Verify the producer-created overlay context, never an environment bool."""
    root = root.resolve()
    permit = tx.parse(tx.read(root / '.opemos-candidate-context.json', 16384))
    tx.need(set(permit) == {'schemaVersion', 'root', 'upper', 'work', 'producerSHA256'}
            and permit['schemaVersion'] == 1, 'invalid staging context')
    # Inside the candidate chroot the mounted root is /; outside it the exact
    # merged path must be used. In either case demand current kernel mount data.
    wanted = '/' if root == Path('/') else str(root)
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        if fields[4] == wanted:
            rows.append(right.split())
    tx.need(len(rows) == 1 and rows[0][0] == 'overlay', 'not a candidate overlay')
    options = rows[0][2].split(',')
    tx.need('upperdir=' + permit['upper'] in options
            and 'workdir=' + permit['work'] in options, 'overlay context differs')
    tx.need(permit['producerSHA256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'staging producer source changed')
    return permit


def old_boot_evidence(root, store):
    # A previous acknowledged transaction is an exact retained generation.
    intent = store / 'intent.json'
    if intent.exists():
        old = tx.load_intent(root, store)
        tx.need(old['phase'] in ('healthy', 'restored'), 'unreconciled prior update')
        tx.verify_set(root, old, 'newSHA256' if old['phase'] == 'healthy' else 'oldSHA256')
        return old['bootImages'], old['target']
    receipt, documents = verify_receipt_evidence(root, allow_live_root=root == Path('/'))
    # Reuse the existing decompressed payload verifier, whose receipt schema
    # uses moduleName and actualPayloadSha256 (not compressed archive hashes).
    from verify_installed_modules import inspect_module
    import time
    expected = documents['moduleVerification']['modules']
    prefix = Path('usr/lib/modules') / receipt['target']['kernelVersion'] / 'updates/open-gpu-kernel-modules-steamos'
    names = {name + '.ko.zst' for name in tx.MODULES}
    tx.need({x.name for x in (root / prefix).iterdir()} == names,
            'initial installed module inventory differs')
    deadline = time.monotonic() + 300
    for item in expected:
        name = item['moduleName']
        actual = inspect_module(name, item['actualPayloadSha256'], prefix,
                                [root / prefix / (name + '.zst')], deadline, False)
        tx.need(not actual['invalidFields'], 'initial installed module differs: ' + name)
    images = documents['initramfsVerification']['images']
    tx.need(0 < len(images) <= 32, 'no compatible verified boot image set')
    for item in images:
        path = root / 'boot' / item['filename']
        tx.need(tx.digest(tx.read(path, tx.MAX_FILE)) == item['sha256'],
                'last known boot image differs from receipt')
    return [str(Path('boot') / x['filename']) for x in images], receipt['target']


# These are the current writer's concrete trees, not an extensible mount API.
# Independent overlays preserve actual /etc and /boot submount contents.
STAGE_TREES = ('usr', 'etc', 'boot', 'var', 'efi')


def mount_candidate(args):
    work = args.workspace.resolve()
    merged = work / 'merged'
    command(['/usr/bin/mount', '--make-rprivate', '/'], 20)
    command(['/usr/bin/mount', '-t', 'overlay', 'overlay', '-o',
             f'lowerdir={args.root},upperdir={work / "upper"},workdir={work / "work"}', merged], 30)
    for tree in STAGE_TREES:
        source = args.root / tree
        if not source.exists():
            continue
        tx.need(source.is_dir() and not source.is_symlink(), 'unsupported staged tree alias')
        command(['/usr/bin/mount', '-t', 'overlay', 'overlay', '-o',
                 f'lowerdir={source},upperdir={work / (tree + "-upper")},workdir={work / (tree + "-work")}',
                 merged / tree], 30)
    return work, merged


def stage_namespace(args):
    tx.need(os.geteuid() == 0, 'candidate namespace requires root')
    work, merged = mount_candidate(args)
    # No host devices, system bus, network or outside PID namespace is exposed.
    for directory in ('run', 'dev', 'proc'):
        (merged / directory).mkdir(exist_ok=True)
    command(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=0755,size=64m', 'tmpfs', merged / 'run'], 30)
    command(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=0755,size=1m', 'tmpfs', merged / 'dev'], 30)
    for name, minor in [('null', 3), ('zero', 5), ('random', 8), ('urandom', 9)]:
        os.mknod(merged / 'dev' / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
    for name, source in [('source', args.support), ('inputs', work / 'inputs'), ('policy', args.policy_root)]:
        destination = merged / 'run' / name
        destination.mkdir()
        command(['/usr/bin/mount', '--bind', source, destination], 30)
        command(['/usr/bin/mount', '-o', 'remount,bind,ro', destination], 30)
    command(['/usr/bin/mount', '-t', 'proc', '-o', 'ro,nosuid,nodev,noexec',
             'proc', merged / 'proc'], 30)
    command(['/usr/bin/mount', '--bind', '/sys', merged / 'sys'], 30)
    command(['/usr/bin/mount', '-o', 'remount,bind,ro', merged / 'sys'], 30)
    tx.durable(merged / '.opemos-candidate-context.json', {
        'schemaVersion': 1, 'root': str(args.root), 'upper': str(work / 'upper'),
        'work': str(work / 'work'),
        'producerSHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    # Keep only ordinary file ownership/mode and sudo credential operations.
    # In particular MKNOD and DAC_READ_SEARCH must not permit constructing a
    # raw device or opening original filesystem handles from this candidate.
    confine_capabilities({0, 1, 3, 4, 6, 7, 8, 18})
    os.chroot(merged)
    os.chdir('/')
    confine_capabilities({0, 1, 3, 4, 6, 7})
    if args.action == 'update':
        argv = ['/bin/bash', '/run/source/bootstrap/install.sh', '--archive',
                '/run/inputs/archive.tar.gz', '--checksum', '/run/inputs/archive.sha256', '-y']
    else:
        argv = ['/bin/bash', '/run/source/bootstrap/recoveryctl.sh', args.action, '-y']
        if args.action == 'enable-fallback':
            argv += ['--profile', args.profile]
    environment = {**os.environ, 'OPEMOS_PINNED_NVIDIA_VERSION': args.nvidia}
    # PID1 supervises each ordinary writer/verifier process. On any timeout or
    # exception PID1 exits, so the kernel closes the entire private PID tree;
    # the outer unshare process still has to be waited/reaped by its controller.
    initial = Path('/var/tmp') / ('opemos-before-' + work.name + '.json')
    tx.need(not os.path.lexists(initial), 'candidate preflight name already exists')
    subprocess.run(['/usr/bin/python3', '/run/source/lib/snapshot_target_execution.py',
                    '--root', '/', '--output', str(initial)], check=True, timeout=120)
    subprocess.run(argv, env=environment, check=True, timeout=900)
    # Existing target-owned execution inputs must remain exactly the ones
    # captured before invoking the writer, not merely a new post-hoc snapshot.
    subprocess.run(['/usr/bin/python3', '/run/source/lib/snapshot_target_execution.py',
                    '--root', '/', '--verify', str(initial)], check=True, timeout=120)
    if args.action != 'update':
        raise ValueError('fallback candidate verification is not implemented')
    evidence = Path('/var/tmp') / ('opemos-verify-' + work.name)
    tx.need(not os.path.lexists(evidence), 'candidate evidence name already exists')
    evidence.mkdir(mode=0o700)
    subprocess.run(['/usr/bin/python3', '/run/source/lib/snapshot_target_execution.py',
                    '--root', '/', '--output', str(evidence / 'execution.json')],
                   check=True, timeout=120)
    verification = ['/usr/bin/python3', '/run/source/lib/verify_initramfs.py',
        '--kernel', os.uname().release, '--execution-manifest', str(evidence / 'execution.json'),
        '--config', '/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf',
        '--output', str(evidence / 'initramfs-verification.json')]
    images = sorted(Path('/boot').glob('initramfs-*.img'))
    tx.need(0 < len(images) <= 32, 'bounded boot image set required')
    for index, image in enumerate(images):
        listing = evidence / ('listing-' + str(index))
        subprocess.run(['/usr/bin/python3', '/run/source/lib/capture_bounded_command.py',
            '--output', str(listing), '--max-bytes', '8388608', '--timeout', '120', '--',
            '/usr/bin/lsinitcpio', '-l', str(image)], check=True, timeout=130)
        verification += ['--image', str(image), '--listing', str(listing),
                         '--image-sha256', tx.digest(tx.read(image, tx.MAX_FILE))]
    subprocess.run(verification, check=True, timeout=120)


def confine_capabilities(retain):
    libc = ctypes.CDLL(None, use_errno=True)
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                    ('inheritable', ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    tx.need(libc.capget(ctypes.byref(header), data) == 0, 'capget failed')
    last = int(Path('/proc/sys/kernel/cap_last_cap').read_text())
    tx.need(0 <= last < 64, 'unsupported capability width')
    for cap in range(last + 1):
        if cap in retain:
            continue
        # Dropping an already absent bounding bit needs no SETPCAP privilege.
        if libc.prctl(23, cap, 0, 0, 0) == 1:
            tx.need(libc.prctl(24, cap, 0, 0, 0) == 0, 'cap bounding drop failed')
        for field in ('effective', 'permitted', 'inheritable'):
            setattr(data[cap // 32], field, getattr(data[cap // 32], field) & ~(1 << (cap % 32)))
    tx.need(libc.capset(ctypes.byref(header), data) == 0
            and libc.prctl(38, 1, 0, 0, 0) == 0, 'candidate privilege confinement failed')


def produce(args, store, boot_images, target):
    work = Path(tempfile.mkdtemp(prefix='candidate-', dir=store))
    for name in ('upper', 'work', 'merged', 'inputs', *[x + suffix for x in STAGE_TREES for suffix in ('-upper', '-work')]):
        (work / name).mkdir(mode=0o700)
    inputs = work / 'inputs'
    if args.action == 'update':
        expected = args.archive_sha256
        tx.need(re.fullmatch('[0-9a-f]{64}', expected or ''), 'exact archive digest required')
        # Snapshot supplied bytes before namespace execution; the signed cache
        # or authenticated online caller remains the input trust authority.
        data = read_regular(args.archive, MAX_INPUT, 'candidate archive')
        tx.need(0 < len(data) <= MAX_INPUT and tx.digest(data) == expected, 'archive changed')
        tx.replace_durable(inputs / 'archive.tar.gz', data, 0o400)
        tx.replace_durable(inputs / 'archive.sha256', (expected + '  archive.tar.gz\n').encode(), 0o400)
    if args.cache is not None:
        from recovery_cached_product import command_stage
        command_stage(SimpleNamespace(materialization=args.cache / 'materialization.json',
            input_dir=args.cache, destination=inputs / 'cache',
            steamos=target['steamosVersion'], kernel=args.kernel,
            nvidia=args.nvidia, support_revision=args.revision))
        for file in (inputs / 'cache').iterdir():
            tx.replace_durable(file, tx.read(file, MAX_INPUT), 0o400)
    command(['/usr/bin/unshare', '--mount', '--net', '--pid', '--fork', '--kill-child',
             sys.executable, str(Path(__file__).resolve()), '_stage', '--root', args.root,
             '--workspace', work, '--support', args.support, '--action', args.action,
             '--profile', args.profile, '--nvidia', args.nvidia, '--policy-root', args.policy_root], 1200)
    merged = work / 'merged'
    # Mount vanished with the private namespace. Verification must use a fresh
    # private namespace too; never treat the unmounted merged directory as data.
    command(['/usr/bin/unshare', '--mount', '--fork', sys.executable,
             str(Path(__file__).resolve()), '_collect', '--root', args.root,
             '--workspace', work, '--support', args.support, '--kernel', args.kernel,
             '--nvidia', args.nvidia, '--policy-root', args.policy_root,
             '--action', args.action, '--profile', args.profile, '--steamos', target['steamosVersion'],
             '--revision', args.revision], 600)
    plan = tx.parse(tx.read(work / 'plan.json', tx.MAX_STATE))
    tx.need(plan['kernel'] == args.kernel and plan['rootIdentity'] == tx.root_identity(args.root),
            'candidate target changed')
    tx.need(set(boot_images) <= {r['path'] for r in plan['files']}, 'candidate boot image set incomplete')
    return work / 'plan.json'


def collect(args):
    work, merged = mount_candidate(args)
    stage_allowed(merged)
    kernel = args.kernel
    if (work / 'inputs/cache').exists():
        cached_commit(SimpleNamespace(root=merged, cache=work / 'inputs/cache',
            steamos=args.steamos, kernel=kernel, nvidia=args.nvidia, support_revision=args.revision))
    # Target-owned executables ran only in the same confined writer namespace.
    # Collection consumes their retained bytes; it never chroots a target
    # executable with original-root capabilities or the outside PID namespace.
    evidence = merged / 'var/tmp' / ('opemos-verify-' + work.name)
    verified = tx.parse(tx.read(evidence / 'initramfs-verification.json', tx.MAX_STATE))
    tx.need(verified.get('status') == 'verified' and verified.get('kernelVersion') == kernel,
            'candidate boot verification missing or mismatched')
    images = verified.get('images')
    tx.need(type(images) is list and 0 < len(images) <= 32, 'verified boot inventory missing')
    for image in images:
        name = image.get('filename')
        tx.need(type(name) is str and re.fullmatch(r'initramfs-[A-Za-z0-9_.+-]+\.img', name),
                'unsafe verified image name')
        tx.need(tx.digest(tx.read(merged / 'boot' / name, tx.MAX_FILE)) == image.get('sha256'),
                'candidate boot image changed after confined verification')
    for item in sorted(evidence.iterdir()):
        tx.need(item.name in ('execution.json', 'initramfs-verification.json') or
                re.fullmatch(r'listing-[0-9]{1,2}', item.name), 'unexpected verification artifact')
        tx.replace_durable(work / ('verified-' + item.name), tx.read(item, 8 * 1024**2))
    rows = []
    # Every actual upper-layer effect must either be a closed transaction
    # artifact or a known private installer temporary/backup. Unknown effects
    # refuse before the original target has been changed.
    layers = [(work / 'upper', '')] + [(work / (name + '-upper'), name + '/') for name in STAGE_TREES]
    seen = set()
    for upper, prefix in layers:
        for path in sorted(upper.rglob('*')):
            relative = prefix + str(path.relative_to(upper))
            if path.is_dir():
                if 'trusted.overlay.opaque' in os.listxattr(path, follow_symlinks=False):
                    value = os.getxattr(path, 'trusted.overlay.opaque', follow_symlinks=False)
                    if value in (b'y', b'Y'):
                        module_dir = f'usr/lib/modules/{kernel}/updates/open-gpu-kernel-modules-steamos'
                        tx.need(relative == module_dir, 'opaque deletion is not an admitted file transaction')
                        names = {m + '.ko.zst' for m in tx.MODULES}
                        original = args.root / relative
                        tx.need(original.is_dir() and {x.name for x in original.iterdir()} == names
                                and {x.name for x in path.iterdir()} == names,
                                'module directory replacement would hide unplanned originals')
                continue
            if (relative == '.opemos-candidate-context.json'
                    or relative.startswith(('tmp/', 'var/tmp/', 'run/',
                        f'var/lib/{PROJECT}/backups/'))):
                continue
            tx.need(relative not in seen, 'duplicate cross-tree effect')
            seen.add(relative)
            tx.target(args.root, relative, kernel, allow_missing=True)
            info = path.lstat()
            if stat.S_ISCHR(info.st_mode) and info.st_rdev == 0:
                rows.append({'path': relative, 'source': None, 'sha256': None})
            else:
                tx.need(stat.S_ISREG(info.st_mode), 'unplanned symlink/special candidate effect')
                source = merged / relative
                data = tx.read(source, tx.MAX_FILE)
                snapshot = work / ('artifact-' + str(len(rows)))
                tx.replace_durable(snapshot, data, stat.S_IMODE(info.st_mode))
                rows.append({'path': relative, 'source': str(snapshot), 'sha256': tx.digest(data)})
    module_prefix = f'usr/lib/modules/{kernel}/updates/open-gpu-kernel-modules-steamos/'
    for module in tx.MODULES:
        tx.need((merged / module_prefix / (module + '.ko.zst')).is_file(), 'candidate module missing')
    # Reuse existing verifier on actual merged files, including exact compressed
    # module set and version/vermagic validation already performed by install.sh.
    tx.need(0 < len(rows) <= tx.MAX_FILES, 'candidate artifact set excessive/empty')
    rows.sort(key=lambda row: (row['path'].startswith(('boot/', 'efi/')), row['path']))
    tx.durable(work / 'plan.json', {'schemaVersion': 1, 'kernel': kernel,
               'rootIdentity': tx.root_identity(args.root), 'completeWriterSet': True, 'files': rows})


def manager_path(support):
    # Same root-owned persistent guardian directory and policy authority.
    from recovery_policy import read_policy
    policy = read_policy(support)
    manager = support / 'installed-updates'
    if not manager.exists():
        manager.mkdir(mode=0o700)
        tx.fsync_dir(manager.parent)
    return manager, policy


def selected_generation(manager):
    current = manager / 'current.json'
    if not current.exists():
        return None
    record = tx.parse(tx.read(current, 4096))
    tx.need(set(record) == {'generation'} and re.fullmatch('txn-[0-9a-f]{32}', record['generation']),
            'invalid current transaction pointer')
    return manager / record['generation']


def check_authority(intent, policy):
    tx.need(intent['authority']['policy'] == policy, 'current recovery authority changed; hold')


def update(args):
    import uuid
    tx.need(os.geteuid() == 0 and args.root == Path('/'), 'installed update requires owning root controller')
    tx.need(False, 'source candidate is incomplete: installed writer/guardian joins are not enabled')
    tx.need(args.support is not None and args.kernel == os.uname().release,
            'current exact kernel/support directory required')
    manager, policy = manager_path(args.policy_root)
    tx.need(args.nvidia == policy['nvidiaVersion'], 'current NVIDIA authority differs')
    args.revision = policy['supportRevision']
    lock = tx.guarded_store(manager)
    was_readonly = False
    try:
        prior = selected_generation(manager)
        if prior is not None:
            old = tx.load_intent(args.root, prior)
            check_authority(old, policy)
            tx.need(old['phase'] in ('healthy', 'restored'), 'pending transaction must reconcile before another update')
        # Old artifacts/target/boot provenance must be verified before staging
        # or changing any live target. No inference from a healthy boolean.
        boot_images, target = old_boot_evidence(args.root, prior or manager)
        tx.need(target['kernelVersion'] == args.kernel and target['nvidiaVersion'] == args.nvidia,
                'no verified compatible last-known-good target; preserve stock OS slot')
        if args.cache is not None:
            expected = {'steamos': target['steamosVersion'], 'kernel': args.kernel,
                        'nvidia': args.nvidia, 'revision': policy['supportRevision']}
            _document, outputs, directory = cached_load(args.cache, expected)
            tx.need(args.archive.resolve() == (directory / outputs['archive']['name']).resolve()
                    and args.archive_sha256 == outputs['archive']['sha256'], 'archive not the exact current cached product')
        tx.need(len(list(manager.iterdir())) <= 10, 'retained generation bound; no automatic deletion')
        available = os.statvfs(manager)
        tx.need(available.f_bavail * available.f_frsize >= 2 * tx.MAX_BYTES,
                'insufficient complete candidate/rollback storage envelope')
        generation = manager / ('txn-' + uuid.uuid4().hex)
        generation.mkdir(mode=0o700)
        tx.fsync_dir(manager)
        plan = produce(args, generation, boot_images, target)
        was_readonly = readonly_enabled()
        context = {'bootImages': boot_images, 'target': target,
                   'authority': {'policy': policy, 'archiveSHA256': args.archive_sha256},
                   'readonlyBefore': was_readonly}
        # All exact originals, candidates and the intent are flushed BEFORE
        # publishing the pointer or altering root read-only policy. A reboot
        # can therefore always discover enough evidence to restore both.
        tx.prepare(args.root, generation, plan, context=context)
        tx.durable(manager / 'current.json', {'generation': generation.name})
        try:
            writable_root(was_readonly)
            return tx.commit_prepared(args.root, generation)
        except BaseException:
            if (generation / 'intent.json').exists():
                tx.recover(args.root, generation)
            raise
    finally:
        try:
            if was_readonly:
                command(['/usr/bin/steamos-readonly', 'enable'], 30)
        finally:
            os.close(lock)


def reconcile_controller(root, support, health=False):
    tx.need(os.geteuid() == 0 and root == Path('/'), 'installed guardian root required')
    manager, policy = manager_path(support)
    lock = tx.guarded_store(manager)
    readonly = False
    try:
        generation = selected_generation(manager)
        if generation is None:
            return {'state': 'no-pending-intent'}
        intent = tx.load_intent(root, generation)
        check_authority(intent, policy)
        # An interrupted disable may leave the observed mode off. The durable
        # prestate, rather than today's mode alone, governs restoration.
        readonly = intent['readonlyBefore'] or readonly_enabled()
        writable_root(readonly)
        try:
            if health:
                return tx.acknowledge(root, generation)
            return tx.startup(root, generation)
        except BaseException:
            # A bad target/authority/unknown artifact was refused before any
            # recovery writes. Recovery itself never overwrites unknown bytes.
            if health:
                tx.recover(root, generation)
            raise
    finally:
        try:
            if readonly:
                command(['/usr/bin/steamos-readonly', 'enable'], 30)
        finally:
            os.close(lock)


def parser():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('_stage', '_collect'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--support', type=Path, required=True)
    p.add_argument('--action', default='update', choices=('update', 'enable-fallback', 'disable-fallback'))
    p.add_argument('--profile', default='console')
    p.add_argument('--kernel')
    p.add_argument('--policy-root', type=Path, required=True)
    p.add_argument('--steamos')
    p.add_argument('--revision')
    p.add_argument('--nvidia', required=True)
    return p


if __name__ == '__main__':
    args = parser().parse_args()
    stage_namespace(args) if args.command == '_stage' else collect(args)
