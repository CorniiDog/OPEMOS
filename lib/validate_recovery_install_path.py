#!/usr/bin/env python3
"""Reject symlink and writable-ancestor redirection of recovery assets."""

import argparse
import os
import stat
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--expected-symlink", action="append", default=[])
    parser.add_argument("--test-owner", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("recovery installation root is unsafe")
    expected_uid = os.getuid() if args.test_owner else 0
    def validate_parents(raw):
        relative = Path(raw.lstrip("/"))
        if ".." in relative.parts:
            raise SystemExit("recovery installation path is unconfined")
        current = root
        for component in relative.parts[:-1]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SystemExit(f"recovery installation ancestor is unsafe: {raw}")
            if info.st_uid != expected_uid or info.st_mode & 0o022:
                raise SystemExit(f"recovery installation ancestor is not confined: {raw}")
        return relative

    for raw in args.path:
        relative = validate_parents(raw)
        final = root / relative
        try:
            info = final.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SystemExit(f"recovery installation destination is unsafe: {raw}")
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            raise SystemExit(f"recovery installation destination is not confined: {raw}")
    for specification in args.expected_symlink:
        raw, separator, expected = specification.partition("=")
        if not separator or not expected or expected.startswith("/"):
            raise SystemExit("expected recovery symlink specification is malformed")
        relative = validate_parents(raw)
        final = root / relative
        try:
            info = final.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISLNK(info.st_mode) or os.readlink(final) != expected:
            raise SystemExit(f"recovery installation symlink is unsafe: {raw}")


if __name__ == "__main__":
    main()
