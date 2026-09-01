#!/usr/bin/env python3
"""Bound project backup generations without following or deleting unknown entries."""

import argparse
import re
import shutil
import time
from pathlib import Path


GENERATION = re.compile(r"(?:uninstall-)?[0-9]{8}-[0-9]{6}\.[A-Za-z0-9]{6,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--protect", required=True)
    parser.add_argument("--keep", type=int, default=10)
    parser.add_argument("--max-age-days", type=int, default=90)
    args = parser.parse_args()
    if (not args.root.is_absolute() or args.root.is_symlink()
            or not args.root.is_dir() or not 1 <= args.keep <= 100
            or not 1 <= args.max_age_days <= 3650
            or GENERATION.fullmatch(args.protect) is None):
        raise SystemExit("backup retention arguments are unsafe")

    root = args.root.resolve(strict=True)
    generations = []
    for entry in args.root.iterdir():
        if (GENERATION.fullmatch(entry.name) is None or entry.is_symlink()):
            continue
        try:
            resolved = entry.resolve(strict=True)
            resolved.relative_to(root)
            metadata = entry.stat()
        except (FileNotFoundError, RuntimeError, ValueError):
            continue
        if entry.is_dir():
            generations.append((metadata.st_mtime_ns, entry.name, entry))

    generations.sort(reverse=True)
    cutoff = time.time_ns() - args.max_age_days * 24 * 60 * 60 * 1_000_000_000
    retained = 0
    for modified, name, path in generations:
        if name == args.protect:
            retained += 1
            continue
        if retained < args.keep and modified >= cutoff:
            retained += 1
            continue
        shutil.rmtree(path)


if __name__ == "__main__":
    main()
