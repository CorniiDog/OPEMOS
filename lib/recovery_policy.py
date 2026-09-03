#!/usr/bin/env python3
"""Read the persistent installed-device recovery policy without pathname trust."""

import argparse
import json
import os
import re
import stat
from pathlib import Path


MAX_VALUE_BYTES = 256
COMMIT = re.compile(rb"[0-9a-f]{40}\n")
VERSION = re.compile(rb"[0-9]+\.[0-9]+(?:\.[0-9]+)?\n")


def fail(message):
    raise ValueError(message)


def identity(info):
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_nlink,
    )


def read_value(parent_descriptor, name, pattern, expected_owner):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError:
        fail(f"persistent recovery {name} is unavailable or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != expected_owner
                or stat.S_IMODE(before.st_mode) != 0o644
                or not 1 <= before.st_size <= MAX_VALUE_BYTES):
            fail(f"persistent recovery {name} is unsafe")
        payload = os.read(descriptor, MAX_VALUE_BYTES + 1)
        if os.read(descriptor, 1):
            fail(f"persistent recovery {name} is excessive")
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (len(payload) != before.st_size or identity(before) != identity(after)
                or identity(after) != identity(current)):
            fail(f"persistent recovery {name} changed while read")
        if pattern.fullmatch(payload) is None:
            fail(f"persistent recovery {name} is malformed")
        return payload[:-1].decode("ascii"), identity(after)
    finally:
        os.close(descriptor)


def read_policy(root, test_owner=False):
    path = Path(os.path.abspath(os.fspath(root)))
    try:
        root_info = path.lstat()
    except OSError:
        fail("persistent recovery directory is unavailable")
    expected_owner = os.geteuid() if test_owner else 0
    if (not stat.S_ISDIR(root_info.st_mode) or path.is_symlink()
            or root_info.st_uid != expected_owner or root_info.st_mode & 0o022):
        fail("persistent recovery directory is unsafe")
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_DIRECTORY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if identity(opened) != identity(current):
            fail("persistent recovery directory changed while opened")
        revision, revision_identity = read_value(
            descriptor, "support-revision", COMMIT, expected_owner,
        )
        nvidia, nvidia_identity = read_value(
            descriptor, "nvidia-version", VERSION, expected_owner,
        )
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
            current_revision = os.stat(
                "support-revision", dir_fd=descriptor, follow_symlinks=False,
            )
            current_nvidia = os.stat(
                "nvidia-version", dir_fd=descriptor, follow_symlinks=False,
            )
        except OSError:
            fail("persistent recovery policy changed while read")
        if (identity(opened) != identity(after)
                or identity(after) != identity(current)
                or revision_identity != identity(current_revision)
                or nvidia_identity != identity(current_nvidia)):
            fail("persistent recovery directory changed while read")
    finally:
        os.close(descriptor)
    return {
        "schemaVersion": 1,
        "supportRevision": revision,
        "nvidiaVersion": nvidia,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--test-owner", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    document = read_policy(arguments.root, arguments.test_owner)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"recovery_policy.py: {error}") from None
