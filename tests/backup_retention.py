#!/usr/bin/env python3
"""Portable regression tests for backup generation retention."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib/prune_backup_generations.py"


with tempfile.TemporaryDirectory(prefix="backup-retention-") as temporary:
    backup_root = Path(temporary) / "backups/kernel"
    backup_root.mkdir(parents=True)
    names = [f"202601{i + 1:02d}-010203.abcdef" for i in range(12)]
    old = "uninstall-20240101-010203.abcdef"
    names.append(old)
    now = time.time()
    for index, name in enumerate(names):
        generation = backup_root / name
        generation.mkdir()
        (generation / "state").write_text(name, encoding="utf-8")
        timestamp = now - index * 60
        if name == old:
            timestamp = now - 120 * 24 * 60 * 60
        os.utime(generation, (timestamp, timestamp))
    unknown = backup_root / "maintainer-notes"
    unknown.mkdir()
    outside = Path(temporary) / "outside"
    outside.mkdir()
    symlink = backup_root / "20200101-010203.abcdef"
    symlink.symlink_to(outside, target_is_directory=True)

    subprocess.run([
        sys.executable, str(HELPER), "--root", str(backup_root),
        "--protect", names[0], "--keep", "10", "--max-age-days", "90",
    ], check=True)

    retained = sorted(
        path.name for path in backup_root.iterdir()
        if path.is_dir() and not path.is_symlink()
        and path.name != unknown.name
    )
    assert len(retained) == 10
    assert names[0] in retained
    assert old not in retained
    assert unknown.is_dir()
    assert symlink.is_symlink() and outside.is_dir()

    rejected = subprocess.run([
        sys.executable, str(HELPER), "--root", str(backup_root),
        "--protect", "../../unsafe", "--keep", "10", "--max-age-days", "90",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert rejected.returncode != 0
    assert sorted(
        path.name for path in backup_root.iterdir()
        if path.is_dir() and not path.is_symlink()
        and path.name != unknown.name
    ) == retained
    assert unknown.is_dir() and symlink.is_symlink() and outside.is_dir()
