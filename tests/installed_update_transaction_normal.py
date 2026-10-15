#!/usr/bin/env python3
"""Authored real CLI regression; execute only in separately admitted OMEN VM.

This exercises real files, fsync, subprocess/schema and UID boundaries. It does
not prove the production namespace producer, boot health, or abrupt VM recovery.
Historical SIGKILL tests are retained separately and are not this acceptance.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import argparse
from contextlib import nullcontext
import uuid

EVIDENCE=None
TOOL = Path(__file__).resolve().parents[1] / 'lib/installed_update_transaction.py'
sys.path.insert(0, str(TOOL.parent))
from installed_update_transaction import root_identity


def run(root, store, action, plan=None, *, uid=None, success=True):
    args = [sys.executable, str(TOOL), action, '--root', str(root), '--store', str(store)]
    if plan is not None:
        args += ['--plan', str(plan)]
    options = {} if uid is None else {'user': uid, 'group': uid, 'extra_groups': []}
    result = subprocess.run(args, env={**os.environ, 'PROJECT_TEST_MODE': '1'},
                            capture_output=True, timeout=15, **options)
    identity=uuid.uuid4().hex
    (EVIDENCE/(identity+'.stdout')).write_bytes(result.stdout)
    (EVIDENCE/(identity+'.stderr')).write_bytes(result.stderr)
    (EVIDENCE/(identity+'.json')).write_text(json.dumps({'argv':args,'uid':uid,'exit':result.returncode,
        'environment':'admitted OMEN guest required','stdoutSHA256':hashlib.sha256(result.stdout).hexdigest(),
        'stderrSHA256':hashlib.sha256(result.stderr).hexdigest(),'VMClosureVerified':False}))
    assert (result.returncode == 0) == success, (args, result.returncode, result.stderr)
    return json.loads(result.stdout) if success else result


def main():
    assert os.geteuid() == 0, 'actual root/other-UID cases require the admitted guest root'
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('kvm','qemu','standard pc','virtual machine')), 'VM-only test'
    global TOOL,EVIDENCE
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    output=parser.parse_args().output
    assert output.is_absolute() and output.resolve()==output and not output.exists()
    output.mkdir(mode=0o700);EVIDENCE=output/'evidence';EVIDENCE.mkdir(mode=0o700)
    with nullcontext(str(output)) as directory:
        base = Path(directory)
        base.chmod(0o755)
        executable = base / 'tool'
        executable.mkdir(mode=0o755)
        for name in ('installed_update_transaction.py', 'atomic_output.py'):
            shutil.copyfile(TOOL.parent / name, executable / name)
            (executable / name).chmod(0o644)
        TOOL = executable / TOOL.name
        root = base / 'root'
        (root / 'etc/modprobe.d').mkdir(parents=True)
        (root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\n')
        before = root / 'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf'
        added = root / 'etc/modprobe.d/98-opemos-recovery.conf'
        before.write_bytes(b'original\n')
        before.chmod(0o644)
        candidate = base / 'candidate'
        candidate.write_bytes(b'candidate\n')
        candidate.chmod(0o644)
        plan = base / 'plan.json'
        document = {'schemaVersion': 1, 'kernel': '6.18.50-test',
                    'rootIdentity': root_identity(root), 'completeWriterSet': True,
                    'files': [{'path': str(path.relative_to(root)), 'source': str(candidate),
                               'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()}
                              for path in (before, added)]}
        plan.write_text(json.dumps(document))
        store = base / 'transaction'
        store.mkdir(mode=0o700)
        # Genuine other-UID OS refusal; no permission adapter or patched method.
        run(root, store, 'activate', plan, uid=65534, success=False)
        assert before.read_bytes() == b'original\n' and not added.exists()
        assert not (store / 'intent.json').exists()
        assert run(root, store, 'activate', plan)['state'] == 'pending-health'
        assert all(path.read_bytes() == b'candidate\n' for path in (before, added))
        assert run(root, store, 'startup')['rebootRequired'] is True
        run(root, store, 'health', success=False)  # Same boot is never health.
        assert run(root, store, 'recover')['state'] == 'restored'
        assert run(root, store, 'recover')['state'] == 'restored'
        assert before.read_bytes() == b'original\n' and not added.exists()
        assert (before.stat().st_mode & 0o777) == 0o644
        before.write_bytes(b'foreign\n')
        run(root, store, 'recover', success=False)
        assert before.read_bytes() == b'foreign\n'
        # A bad candidate is rejected before any intent or target mutation.
        rejected = base / 'rejected'
        rejected.mkdir(mode=0o700)
        document['files'][0]['sha256'] = '0' * 64
        plan.write_text(json.dumps(document))
        run(root, rejected, 'activate', plan, success=False)
        assert not (rejected / 'intent.json').exists() and before.read_bytes() == b'foreign\n'
        # Unknown and excessive schema never become permission to write.
        document['unexpected'] = True
        plan.write_text(json.dumps(document))
        run(root, rejected, 'activate', plan, success=False)
        assert not (rejected / 'intent.json').exists()
        # Driver plans cannot replace the recovery entry/dispatcher/app, and
        # absent parent directories are never silently created outside intent.
        (root/'etc/systemd/system').mkdir(parents=True)
        (root/'etc/NetworkManager/dispatcher.d').mkdir(parents=True)
        for relative in ('etc/systemd/system/opemos-nvidia-guardian.service',
                         'etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair'):
            row={'path':relative,'source':str(candidate),
                 'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest()}
            bad={'schemaVersion':1,'kernel':'6.18.50-test','rootIdentity':root_identity(root),
                 'completeWriterSet':True,'files':[row]}
            plan.write_text(json.dumps(bad))
            run(root,rejected,'activate',plan,success=False)
            assert not (rejected/'intent.json').exists() and not (root/relative).exists()
        relative='var/lib/open-gpu-kernel-modules-steamos-support/recovery/state.json'
        bad['files']=[{'path':relative,'source':str(candidate),'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest()}]
        plan.write_text(json.dumps(bad))
        assert run(root,rejected,'activate',plan)['state']=='pending-health'
        assert (root/relative).read_bytes()==candidate.read_bytes()
        assert run(root,rejected,'recover')['state']=='restored'
        assert run(root,rejected,'recover')['state']=='restored'
        assert not (root/'var').exists(),'exact journaled directories restored to absence'
        interrupted=base/'foreign-directory';interrupted.mkdir(mode=0o700)
        run(root,interrupted,'activate',plan)
        foreign=(root/relative).parent/'exact-test-owned-foreign'
        foreign.write_bytes(b'owned regression evidence');foreign.chmod(0o600)
        run(root,interrupted,'recover',success=False)
        assert foreign.read_bytes()==b'owned regression evidence'
        assert json.loads((interrupted/'intent.json').read_bytes())['phase']=='rolling-back'
        # The test owns this exact file; the updater correctly preserved it.
        assert foreign.stat().st_uid==0 and foreign.stat().st_nlink==1
        foreign.unlink()
        assert run(root,interrupted,'recover')['state']=='restored'
        assert run(root,interrupted,'recover')['state']=='restored'
        assert not (root/'var').exists()


    print('ordinary CLI transaction/UID/schema/foreign-data cases passed')


if __name__ == '__main__':
    main()
