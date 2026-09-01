#!/usr/bin/env python3
"""Remove old abandoned target-build sessions without touching active work."""

import argparse
import fcntl
import os
import re
import shutil
import time
from pathlib import Path


SESSION = re.compile(r"target-build\.[A-Za-z0-9]{6,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--minimum-age-seconds", type=int, default=24 * 60 * 60)
    args = parser.parse_args()
    if (not args.root.is_absolute() or args.root.is_symlink()
            or not args.root.is_dir()
            or not 60 <= args.minimum_age_seconds <= 30 * 24 * 60 * 60):
        raise SystemExit("build session retention arguments are unsafe")
    root = args.root.resolve(strict=True)
    cutoff = time.time_ns() - args.minimum_age_seconds * 1_000_000_000

    for session in args.root.iterdir():
        if (SESSION.fullmatch(session.name) is None or session.is_symlink()):
            continue
        try:
            resolved = session.resolve(strict=True)
            resolved.relative_to(root)
            metadata = session.stat()
        except (FileNotFoundError, RuntimeError, ValueError):
            continue
        if not session.is_dir() or metadata.st_mtime_ns >= cutoff:
            continue
        lock_path = session / ".active.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            lock_fd = os.open(lock_path, flags, 0o600)
        except OSError:
            continue
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            shutil.rmtree(session)
        finally:
            os.close(lock_fd)


if __name__ == "__main__":
    main()
