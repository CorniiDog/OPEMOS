#!/usr/bin/env python3
"""Actual Git producer -> frozen guardian source consumer, OMEN VM only.

Does not install, replace methods or emulate permission/Git behavior. Every
failure and the private source clone remain under the requested output root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO=Path(__file__).resolve().parents[1]
CHILD='''
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from install_guardian_generation import freeze_source
frozen=freeze_source(Path(sys.argv[2]),sys.argv[3])
print(json.dumps({name:hashlib.sha256(data).hexdigest() for name,data in frozen.items()},sort_keys=True))
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert os.geteuid()==0
    assert any(x in Path('/sys/class/dmi/id/product_name').read_text().lower()
               for x in ('kvm','qemu','standard pc','virtual machine'))
    out=a.output;assert out.is_absolute() and out.resolve()==out and not out.exists()
    out.mkdir(mode=0o700);clone=out/'source'
    subprocess.run(['git','clone','--no-hardlinks',str(REPO),str(clone)],check=True,timeout=180)
    revision=subprocess.check_output(['git','-C',str(clone),'rev-parse','HEAD'],timeout=10).decode().strip()
    argv=[sys.executable,'-c',CHILD,str(REPO/'lib'),str(clone),revision]
    good=subprocess.run(argv,capture_output=True,timeout=90)
    (out/'normal.stdout').write_bytes(good.stdout);(out/'normal.stderr').write_bytes(good.stderr)
    assert good.returncode==0
    snapshots=json.loads(good.stdout)
    results=[]
    # Each real dirty privileged template and retained engine must be refused
    # although HEAD stays exact and the unrelated helper bytes stay unchanged.
    names=[name for name in snapshots if name.startswith('support/recovery/')]
    names+=['lib/installed_boot_guard.py','lib/installed_update_transaction.py','lib/atomic_output.py']
    for index,name in enumerate(names):
        path=clone/name;before=path.read_bytes()
        path.write_bytes(before+b'\n# deliberately changed VM test source\n')
        attempt=subprocess.run(argv,capture_output=True,timeout=90)
        (out/(str(index)+'.stderr')).write_bytes(attempt.stderr)
        assert attempt.returncode!=0 and path.read_bytes()!=before
        assert snapshots[name]==hashlib.sha256(before).hexdigest()
        path.write_bytes(before)
        results.append({'path':name,'exit':attempt.returncode})
    (out/'result.json').write_text(json.dumps({'normalExit':good.returncode,'refusals':results,
        'environment':'separately admitted OMEN VM required','VMClosureVerified':False},indent=2))


if __name__=='__main__':main()
