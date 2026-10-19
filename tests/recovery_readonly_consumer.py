#!/usr/bin/env python3
"""Real retry producer -> read-only mounted consumer, OMEN VM only.

No fake filesystem/UID/mount adapters or method substitutions. Retain the
entire output directory on success/failure; do not run on a physical host.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO=Path(__file__).resolve().parents[1]
TOOL=REPO/'lib/recovery_transaction.py'
CHILD='''
import json,subprocess,sys
from pathlib import Path
state=Path(sys.argv[1]);tool=Path(sys.argv[2]);out=Path(sys.argv[3])
subprocess.run(['mount','--make-rprivate','/'],check=True)
subprocess.run(['mount','--bind',str(state.parent),str(state.parent)],check=True)
subprocess.run(['mount','-o','remount,bind,ro',str(state.parent)],check=True)
rows=[x.split(' - ',1)[0].split() for x in Path('/proc/self/mountinfo').read_text().splitlines()]
assert any(x[4]==str(state.parent) and 'ro' in x[5].split(',') for x in rows)
before=state.read_bytes()
show=subprocess.run([sys.executable,str(tool),'show','--state',str(state)],capture_output=True,timeout=10)
(out/'readonly-show.stdout').write_bytes(show.stdout);(out/'readonly-show.stderr').write_bytes(show.stderr)
assert show.returncode==0 and json.loads(show.stdout)['phase']=='offline_waiting'
write=subprocess.run([sys.executable,str(tool),'set','--state',str(state),'--phase','failed','--reason','readonly_refusal'],capture_output=True,timeout=10)
(out/'readonly-set.stderr').write_bytes(write.stderr)
assert write.returncode!=0 and state.read_bytes()==before
print(json.dumps({'showExit':show.returncode,'setExit':write.returncode,'actualReadonlyMountVerified':True}))
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert os.geteuid()==0
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('kvm','qemu','standard pc','virtual machine'))
    out=a.output
    assert out.is_absolute() and out.resolve()==out and not out.exists()
    out.mkdir(mode=0o700);directory=out/'state';directory.mkdir(mode=0o700)
    state=directory/'transaction.json'
    begin=subprocess.run([sys.executable,str(TOOL),'begin','--state',str(state),
        '--kernel',os.uname().release,'--nvidia','575.64.05','--support-revision','1'*40,
        '--phase','offline_waiting','--reason','readonly_consumer_regression'],capture_output=True,timeout=10)
    (out/'actual-begin.stdout').write_bytes(begin.stdout);(out/'actual-begin.stderr').write_bytes(begin.stderr)
    assert begin.returncode==0
    result=subprocess.run(['unshare','--mount','--fork',sys.executable,'-c',CHILD,str(state),str(TOOL),str(out)],
                          capture_output=True,timeout=30)
    (out/'actual-namespace.stdout').write_bytes(result.stdout);(out/'actual-namespace.stderr').write_bytes(result.stderr)
    assert result.returncode==0
    (out/'result.json').write_text(json.dumps({'producerExit':begin.returncode,'namespaceExit':result.returncode,
        'environment':'separately-admitted-OMEN-VM-required','VMClosureVerifiedByThisProgram':False}))


if __name__=='__main__':main()
