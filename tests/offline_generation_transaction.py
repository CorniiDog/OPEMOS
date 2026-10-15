#!/usr/bin/env python3
"""Real producer/independent consumer barrier cases, admitted OMEN VM only.

No syscalls, clocks, PIDs or methods are replaced. Full authenticated image
installation remains the existing install_to_root/receipt/export workflow.
All artifacts (including a failed producer's marker) are retained at --output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

LIB=Path(__file__).resolve().parents[1]/'lib'
PRODUCER='''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from offline_generation import begin,seal
root=Path(sys.argv[2]);begin(root,'barrier-fixture',os.getpid())
print('READY',flush=True)
if sys.stdin.readline().strip()=='seal':seal(root,root/'efi')
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output
    assert out.is_absolute() and out.resolve()==out and not out.exists()
    assert os.geteuid()==0
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('kvm','qemu','standard pc','virtual machine'))
    out.mkdir(mode=0o700)
    for case in ('normal','producer-exited'):
        root=out/case;root.mkdir();(root/'efi').mkdir()
        child=subprocess.Popen([sys.executable,'-c',PRODUCER,str(LIB),str(root)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(out/(case+'.stderr')).open('xb'),text=True)
        assert child.stdout.readline().strip()=='READY'
        marker=root/'var/lib/open-gpu-kernel-modules-steamos-support/offline-generation-pending.json'
        before=marker.read_bytes()
        # This consumer is a sibling, not a producer descendant. Even while the
        # owner is genuinely alive it cannot publish the incomplete receipt.
        check=subprocess.run([sys.executable,str(LIB/'payload_receipt.py'),'verify',
            '--root',str(root),'--output',str(out/(case+'.receipt.json'))],capture_output=True,timeout=10)
        (out/(case+'.consumer.stderr')).write_bytes(check.stderr)
        assert check.returncode!=0 and b'not this producer' in check.stderr
        assert marker.read_bytes()==before
        child.stdin.write('seal\n' if case=='normal' else 'ordinary-exit\n');child.stdin.flush()
        numeric=child.wait(timeout=20)
        assert numeric==0
        if case=='normal':
            assert not marker.exists()
        else:
            assert marker.read_bytes()==before
            # The real owner exited without publication. A new producer may
            # not rearm/replace the old marker or silently reuse its overlay.
            retry=subprocess.run([sys.executable,str(LIB/'offline_generation.py'),'begin',
                '--root',str(root),'--kernel','barrier-fixture','--owner',str(os.getpid())],
                capture_output=True,timeout=10)
            (out/(case+'.retry.stderr')).write_bytes(retry.stderr)
            assert retry.returncode!=0 and marker.read_bytes()==before
        (out/(case+'.result.json')).write_text(json.dumps({'producerExit':numeric,
            'consumerExit':check.returncode,'markerSHA256':hashlib.sha256(before).hexdigest(),
            'normalBoundarySealed':case=='normal','projectImageInstallOrVMClosureProven':False}))


if __name__=='__main__':main()
