#!/usr/bin/env python3
"""Portable tests for abandoned build-session cleanup."""

import fcntl
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib/prune_build_sessions.py"


with tempfile.TemporaryDirectory(prefix="build-session-retention-") as temporary:
    cache = Path(temporary) / "cache"
    cache.mkdir()
    active = cache / "target-build.active1"
    abandoned = cache / "target-build.oldold"
    recent = cache / "target-build.recent"
    unknown = cache / "other-build.oldold"
    outside = Path(temporary) / "outside"
    for path in (active, abandoned, recent, unknown, outside):
        path.mkdir()
    old = time.time() - 2 * 24 * 60 * 60
    for path in (active, abandoned, unknown):
        os.utime(path, (old, old))
    alias = cache / "target-build.symlink"
    alias.symlink_to(outside, target_is_directory=True)

    lock_fd = os.open(active / ".active.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.utime(active, (old, old))
    try:
        subprocess.run([
            sys.executable, str(HELPER), "--root", str(cache),
            "--minimum-age-seconds", "86400",
        ], check=True)
        assert active.is_dir()
        assert not abandoned.exists()
        assert recent.is_dir()
        assert unknown.is_dir()
        assert alias.is_symlink() and outside.is_dir()
    finally:
        os.close(lock_fd)
