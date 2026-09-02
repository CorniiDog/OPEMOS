#!/usr/bin/env python3
"""Atomically remove NVIDIA-only kernel arguments for explicit Nouveau recovery."""

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path

REMOVE = re.compile(
    r"(?<!\S)(?:rd\.driver\.blacklist=nouveau|modprobe\.blacklist=nouveau|"
    r"nvidia-drm\.modeset=1|nvidia-drm\.fbdev=1)(?!\S)"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    info = args.config.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        raise SystemExit("GRUB defaults are unsafe or excessive.")
    original = args.config.read_text(encoding="utf-8")
    updated = REMOVE.sub("", original)
    updated = re.sub(r"[ \t]{2,}", " ", updated)
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
