#!/usr/bin/env python3
"""Durable unpublished-image completion barrier for the existing installer.

A failed build is never rearmed or repaired here; its creator retains/discards
its disposable overlay under the existing contract. No disk ownership inferred.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import stat
import uuid
from atomic_output import atomic_create_bytes

RELATIVE=Path('var/lib/open-gpu-kernel-modules-steamos-support/offline-generation-pending.json')


def start(pid):
    text=Path('/proc')/str(pid)/'stat'
    fields=text.read_text().rsplit(')',1)[1].split()
    return int(fields[1]),int(fields[19])


def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def marker(root):
    root=root.resolve(strict=True)
    if root==Path('/'):
        raise ValueError('offline generation cannot target the running root')
    node=root
    for part in RELATIVE.parts:
        node=node/part
        if node.is_symlink():raise ValueError('image generation path alias')
    return node


def read_pending(root):
    path=marker(root)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                info.st_nlink!=1 or info.st_mode&0o022 or not 0<info.st_size<=4096):
            raise ValueError('unsafe pending generation')
        data=os.read(fd,4097)
        now=os.fstat(fd)
        if len(data)!=info.st_size or (info.st_ino,info.st_mtime_ns,info.st_ctime_ns,info.st_size)!=(now.st_ino,now.st_mtime_ns,now.st_ctime_ns,now.st_size):
            raise ValueError('pending generation changed')
        document=json.loads(data)
        if set(document)!={'schemaVersion','rootIdentity','kernel','ownerPID','ownerStart','bootId','nonce'} or document['schemaVersion']!=1:
            raise ValueError('pending generation schema')
        if document['rootIdentity']!=[root.stat().st_dev,root.stat().st_ino]:
            raise ValueError('pending image root changed')
        return document
    finally:os.close(fd)


def require_producer(root):
    document=read_pending(root)
    if document['bootId']!=boot():raise ValueError('interrupted image generation; discard owned overlay')
    pid=os.getpid()
    for _ in range(64):
        parent,ticks=start(pid)
        if pid==document['ownerPID'] and ticks==document['ownerStart']:
            return document
        if pid==1 or parent<=0 or parent==pid:break
        pid=parent
    raise ValueError('unpublished image generation is not this producer; discard interrupted overlay')


def flush_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def begin(root,kernel,owner):
    path=marker(root)
    # An existing marker is never cleared, relabelled or retried.
    if os.path.lexists(path):raise ValueError('prior unpublished generation exists; preserve/discard its overlay')
    parent=root
    for part in RELATIVE.parts[:-1]:
        child=parent/part
        if not child.exists():child.mkdir(mode=0o755);flush_directory(parent)
        info=child.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
            raise ValueError('unsafe image generation parent')
        parent=child
    _,ticks=start(owner)
    doc={'schemaVersion':1,'rootIdentity':[root.stat().st_dev,root.stat().st_ino],
         'kernel':kernel,'ownerPID':owner,'ownerStart':ticks,'bootId':boot(),'nonce':uuid.uuid4().hex}
    atomic_create_bytes(path,(json.dumps(doc,sort_keys=True)+'\n').encode(),mode=0o644)
    flush_directory(path.parent)
    require_producer(root)


def seal(root,efi,extra=()):
    require_producer(root)
    libc=ctypes.CDLL(None,use_errno=True)
    for path in (root,efi,*extra):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            if libc.syncfs(fd)!=0:raise OSError(ctypes.get_errno(),'target syncfs failed')
        finally:os.close(fd)
    marker(root).unlink()
    flush_directory(marker(root).parent)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('begin','seal'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--kernel');p.add_argument('--owner',type=int)
    p.add_argument('--efi',type=Path);p.add_argument('--extra',type=Path,action='append',default=[]);a=p.parse_args()
    if a.action=='begin':begin(a.root,a.kernel,a.owner)
    else:seal(a.root,a.efi,a.extra)


if __name__=='__main__':main()
