#!/usr/bin/env python3
"""Real producer/consumer hard-stop regressions; run ONLY in admitted OMEN VM."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'lib'
TOOL = LIB / 'installed_update_transaction.py'
sys.path.insert(0, str(LIB))
from installed_update_transaction import root_identity


def invoke(root, store, plan=None, pause=None):
    argv = [sys.executable, str(TOOL), 'activate' if plan else 'recover',
            '--root', str(root), '--store', str(store)]
    if plan:
        argv += ['--plan', str(plan)]
    env = {**os.environ, 'PROJECT_TEST_MODE': '1'}
    if pause:
        env['OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE'] = pause
    return subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    for phase in ('prepared', 'activated:0', 'pending-health'):
        with tempfile.TemporaryDirectory(prefix='installed-update-') as directory:
            base = Path(directory)
            root = base / 'root'
            (root / 'etc/modprobe.d').mkdir(parents=True)
            (root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\n')
            names = ['etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf',
                     'etc/modprobe.d/98-opemos-recovery.conf']
            for name in names:
                (root / name).write_bytes(b'previous\n')
            candidate = base / 'candidate'
            candidate.write_bytes(b'candidate\n')
            store = base / 'transaction'
            store.mkdir(mode=0o700)
            plan = base / 'plan.json'
            plan.write_text(json.dumps({'schemaVersion': 1, 'kernel': '6.18.50-test',
                'rootIdentity': root_identity(root), 'completeWriterSet': True,
                'files': [{'path': name, 'source': str(candidate),
                           'sha256': hashlib.sha256(b'candidate\n').hexdigest()}
                          for name in names]}))
            child = invoke(root, store, plan, phase)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    intent = store / 'intent.json'
                    if intent.exists():
                        document = json.loads(intent.read_bytes())
                        reached = (document['phase'] == 'prepared' if phase == 'prepared'
                                   else document['phase'] == 'pending-health' if phase == 'pending-health'
                                   else (root / names[0]).read_bytes() == b'candidate\n')
                        if reached:
                            break
                    assert child.poll() is None
                    time.sleep(.02)
                else:
                    raise AssertionError('durable phase not reached')
                child.kill()
                child.communicate(timeout=5)
                assert child.returncode == -signal.SIGKILL
                # Interrupt the real restart consumer, then recover again twice.
                recovering = invoke(root, store, pause='restore:0')
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if json.loads((store / 'intent.json').read_bytes())['phase'] == 'rolling-back':
                            break
                        assert recovering.poll() is None
                        time.sleep(.02)
                    else:
                        raise AssertionError('recovery intent not reached')
                    recovering.kill()
                    recovering.communicate(timeout=5)
                    assert recovering.returncode == -signal.SIGKILL
                finally:
                    if recovering.poll() is None:
                        recovering.kill()
                    recovering.communicate(timeout=5)
                for _ in range(2):
                    current = invoke(root, store)
                    out, error = current.communicate(timeout=5)
                    assert current.returncode == 0, error
                    assert json.loads(out)['state'] == 'restored'
                    assert all((root / name).read_bytes() == b'previous\n' for name in names)
                # An unknown post-recovery modification is never overwritten.
                (root / names[0]).write_bytes(b'foreign\n')
                refused = invoke(root, store)
                refused.communicate(timeout=5)
                assert refused.returncode != 0
                assert (root / names[0]).read_bytes() == b'foreign\n'
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)


if __name__ == '__main__':
    main()
