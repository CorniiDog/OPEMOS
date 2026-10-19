#!/usr/bin/env python3
"""Bounded read-only status bridge; never an update authorization or journal.

The privileged ordinary consumer publishes its observation. Unprivileged UI
checks the actual boot and public artifact bytes. Any mutation invalidates this
observation first. Private rollback/source evidence remains root-only.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

DIRECTORY=Path('/run/opemos')
PATH=DIRECTORY/'recovery-status.json'
MAXIMUM=256*1024


def need(value):
    if not value:raise ValueError('privileged recovery observation unavailable or stale; run the owning guardian')


def read(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        first=os.fstat(fd)
        need(stat.S_ISREG(first.st_mode) and first.st_uid==0 and first.st_nlink==1
             and not first.st_mode&0o022 and first.st_size<=limit)
        data=bytearray()
        while True:
            chunk=os.read(fd,min(1024**2,limit+1-len(data)))
            if not chunk:break
            data.extend(chunk);need(len(data)<=limit)
        last=os.fstat(fd);current=path.lstat()
        need(len(data)==first.st_size and all(getattr(first,k)==getattr(last,k)==getattr(current,k)
             for k in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns','st_uid','st_nlink')))
        return bytes(data)
    finally:os.close(fd)


def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def directory():
    DIRECTORY.mkdir(mode=0o755,exist_ok=True)
    info=DIRECTORY.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)


def invalidate():
    need(os.geteuid()==0);directory()
    if os.path.lexists(PATH):
        read(PATH,MAXIMUM)
        PATH.unlink()
        fd=os.open(DIRECTORY,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)


def publish(document):
    need(os.geteuid()==0);directory()
    # Reuse the actual module identities already inspected by the ordinary
    # status tool, but observe file bytes independently at publication time.
    kernel=document['target']['kernelVersion']
    import re
    need(type(kernel) is str and re.fullmatch('[A-Za-z0-9_.+-]{1,192}',kernel))
    prefix=Path('/usr/lib/modules')/kernel/'updates/open-gpu-kernel-modules-steamos'
    paths=[prefix/(name+'.ko.zst') for name in ('nvidia','nvidia-drm','nvidia-modeset','nvidia-uvm','nvidia-peermem')]
    paths += [Path('/etc/modprobe.d')/name for name in ('98-opemos-recovery.conf','99-open-gpu-kernel-modules-steamos.conf')]
    records={str(p):hashlib.sha256(read(p,128*1024**2)).hexdigest() if os.path.lexists(p) else None for p in paths}
    # No private journal, cache path, intent, credential or corruption contents
    # is exported. Existing schema-1 fields remain the public status contract.
    public={k:v for k,v in document.items() if k not in ('transaction','installedGeneration')}
    value={'schemaVersion':1,'bootId':boot(),'kernel':os.uname().release,'files':records,'status':public}
    from atomic_output import atomic_write_bytes
    data=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    need(len(data)<=MAXIMUM)
    atomic_write_bytes(PATH,data,mode=0o644)
    fd=os.open(DIRECTORY,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def consume():
    info=DIRECTORY.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    doc=json.loads(read(PATH,MAXIMUM))
    need(set(doc)=={'schemaVersion','bootId','kernel','files','status'} and doc['schemaVersion']==1
         and doc['bootId']==boot() and doc['kernel']==os.uname().release)
    need(type(doc['files']) is dict and len(doc['files'])==7)
    for name,expected in doc['files'].items():
        path=Path(name)
        need(name.startswith('/usr/lib/modules/'+doc['kernel']+'/updates/open-gpu-kernel-modules-steamos/') or
             name in ('/etc/modprobe.d/98-opemos-recovery.conf','/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf'))
        actual=hashlib.sha256(read(path,128*1024**2)).hexdigest() if os.path.lexists(path) else None
        need(actual==expected)
    print(json.dumps(doc['status'],sort_keys=True,separators=(',',':')))


if __name__=='__main__':
    need(len(sys.argv)==2)
    if sys.argv[1]=='read':consume()
    elif sys.argv[1]=='publish':publish(json.load(sys.stdin))
    else:raise ValueError('unsupported public status operation')
