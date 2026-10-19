#!/usr/bin/env python3
"""Atomically remove NVIDIA-only kernel arguments for explicit Nouveau recovery."""

import argparse
import os
import re
import stat
import shlex
import tempfile
from pathlib import Path

REMOVE = re.compile(
    r"(?<!\S)(?:rd\.driver\.blacklist=nouveau|modprobe\.blacklist=nouveau|"
    r"nvidia-drm\.modeset=1|nvidia-drm\.fbdev=1)(?!\S)"
)


def update_current_entry(path):
    # Never regenerate boot entries using overlay/host block-device discovery.
    # Match only the current boot's literal root selector and kernel basename.
    cmdline=shlex.split(Path('/proc/cmdline').read_text())
    roots=[x for x in cmdline if x.startswith('root=')]
    if len(roots)!=1 or '$' in roots[0]:
        raise ValueError('current root selector is not unambiguous')
    images=[x.split('=',1)[1] for x in cmdline if x.startswith('BOOT_IMAGE=')]
    if len(images)==1:
        name=Path(images[0]).name
    else:
        pkgbase=(Path('/usr/lib/modules')/os.uname().release/'pkgbase').read_text().strip()
        if not re.fullmatch('[A-Za-z0-9_.+-]{1,80}',pkgbase):raise ValueError('kernel package identity unavailable')
        name='vmlinuz-'+pkgbase
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or info.st_size>1024*1024:
        raise ValueError('unsafe boot configuration')
    original=path.read_text()
    lines=[];matched=0
    for line in original.splitlines(keepends=True):
        if re.match(r'^\s*(?:linux|linuxefi)\s+',line):
            fields=shlex.split(line,comments=True)
            if len(fields)>=3 and Path(fields[1]).name==name and roots[0] in fields[2:]:
                if any('$' in field for field in fields[1:]):raise ValueError('dynamic current entry is ambiguous')
                matched+=1
                line=REMOVE.sub('',line)
        lines.append(line)
    if not 1<=matched<=4:raise ValueError('bounded current-root kernel entry not found')
    from atomic_output import atomic_write_bytes
    atomic_write_bytes(path,''.join(lines).encode(),mode=stat.S_IMODE(info.st_mode))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--boot-config", type=Path)
    args = parser.parse_args()
    if args.boot_config is not None:
        update_current_entry(args.boot_config)
    info = args.config.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        raise SystemExit("GRUB defaults are unsafe or excessive.")
    original = args.config.read_text(encoding="utf-8")
    lines=[]
    for line in original.splitlines(keepends=True):
        match=re.match(r"GRUB_CMDLINE_LINUX(?:_DEFAULT)?=([\"'])",line)
        if match:
            quote=match[1];begin=match.end();end=None;escaped=False
            for index in range(begin,len(line)):
                char=line[index]
                if char==quote and not escaped:end=index;break
                escaped=char=='\\' and not escaped
            if end is None:raise ValueError('unterminated GRUB command-line literal')
            value=line[begin:end]
            removed=REMOVE.sub('', ' '+value+' ')
            if removed!=' '+value+' ':line=line[:begin]+removed.strip()+line[end:]
        lines.append(line)
    updated=''.join(lines)
    if updated == original:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".opemos-grub.", dir=args.config.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, info.st_mode & 0o777)
        os.replace(temporary, args.config)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)


if __name__ == "__main__":
    main()
