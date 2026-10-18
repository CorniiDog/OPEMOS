#!/usr/bin/env python3
"""Pinned transport snapshot, actual CLI/native file refusals; not package trust."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
TOOL=ROOT/'lib/recovery_cached_product.py'
NV='575.64.05'

def canonical(d):return (json.dumps(d,sort_keys=True,separators=(',',':'))+'\n').encode()
def sha(b):return hashlib.sha256(b).hexdigest()

with tempfile.TemporaryDirectory(prefix='local-userspace-') as temporary:
    base=Path(temporary);inputs=base/'inputs';inputs.mkdir()
    manifest={'schemaVersion':1,'nvidiaVersion':NV,'packages':[]}
    for name,release in [('nvidia-utils','2'),('lib32-nvidia-utils','1')]:
        filename=f'{name}-{NV}-{release}-x86_64.pkg.tar.zst'
        # Deliberately not valid Arch packages/signatures: transport copying
        # cannot establish native signature, package, dependency or OS health.
        data=b'unverified transport '+name.encode();signature=b'unverified signature '+name.encode()
        (inputs/filename).write_bytes(data);(inputs/(filename+'.sig')).write_bytes(signature)
        manifest['packages'].append(dict(name=name,filename=filename,bytes=len(data),sha256=sha(data),
            signatureFilename=filename+'.sig',signatureBytes=len(signature),signatureSha256=sha(signature)))
    path=inputs/'userspace-inputs.json';path.write_bytes(canonical(manifest))
    def snapshot(label,payload=None,expected_nv=NV,pin=None,success=False):
        if payload is not None:path.write_bytes(payload)
        command=['python3',str(TOOL),'snapshot-userspace','--manifest',str(path),
                 '--manifest-sha256',pin or sha(path.read_bytes()),'--nvidia',expected_nv,
                 '--destination',str(base/label)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert (result.returncode==0)==success,(command,result.stdout,result.stderr)
        if not success:assert not (base/label).exists()
        return result
    result=snapshot('normal',success=True)
    document=json.loads(result.stdout);assert document['status']=='snapshotted-unverified'
    for row in manifest['packages']:
        for key in ['filename','signatureFilename']:
            retained=base/'normal'/row[key]
            assert retained.read_bytes()==(inputs/row[key]).read_bytes()
            assert retained.stat().st_uid==os.geteuid() and retained.stat().st_mode&0o777==0o600
    assert (base/'normal').stat().st_mode&0o777==0o700
    snapshot('wrong-manifest-hash',pin='0'*64)
    snapshot('wrong-target',expected_nv='580.1.1')
    bad=json.loads(canonical(manifest));bad['packages'][0]['filename']='../escape.pkg.tar.zst'
    snapshot('traversal',canonical(bad))
    snapshot('duplicate-key',canonical(manifest).replace(b'"schemaVersion":1',b'"schemaVersion":1,"schemaVersion":1'))
    snapshot('boolean-size',canonical({**manifest,'packages':[{**manifest['packages'][0],'bytes':True},manifest['packages'][1]]}))
    snapshot('duplicate-role',canonical({**manifest,'packages':[manifest['packages'][0],manifest['packages'][0]]}))
    path.write_bytes(canonical(manifest));package=inputs/manifest['packages'][0]['filename'];original=package.read_bytes()
    package.write_bytes(original+b'corrupt');snapshot('corrupt-input');package.write_bytes(original)
    link=inputs/'extra-link';os.link(package,link);snapshot('hardlink-input');link.unlink()
    saved=inputs/'saved';package.rename(saved);package.symlink_to(saved)
    snapshot('symlink-input');package.unlink();saved.rename(package)
    existing=base/'existing';existing.mkdir();(existing/'sentinel').write_bytes(b'preserve')
    result=subprocess.run(['python3',str(TOOL),'snapshot-userspace','--manifest',str(path),
        '--manifest-sha256',sha(path.read_bytes()),'--nvidia',NV,'--destination',str(existing)],capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and list(existing.iterdir())==[existing/'sentinel']
    assert (existing/'sentinel').read_bytes()==b'preserve'
    for entry in ['online_install.sh','setup_nvidia.sh']:
        refused=subprocess.run(['bash',str(ROOT/'bootstrap'/entry),'--userspace-inputs',str(path)],capture_output=True,text=True,timeout=20)
        assert refused.returncode!=0 and 'paired SHA-256' in refused.stderr,(refused.stdout,refused.stderr)
print('Local userspace transport snapshots/refusals passed; native package trust is NOT established.')
