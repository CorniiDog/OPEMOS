#!/usr/bin/env python3
"""Remove safely identified abandoned Core target-build sessions."""

import argparse
import fcntl
import os
import re
import stat
import time
from pathlib import Path

SESSION = re.compile(r"target-build\.[A-Za-z0-9]{6,}")
MAX_DEPTH = 64
MAX_NODES = 200_000
MAX_BYTES = 32 * 1024 * 1024 * 1024


def fail(message):
    raise ValueError(message)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode,
            info.st_nlink, info.st_size)


def inventory(descriptor, depth, budget):
    if depth > MAX_DEPTH:
        fail("build session is too deep")
    result = []
    with os.scandir(descriptor) as entries:
        snapshot = [(entry.name, entry.stat(follow_symlinks=False)) for entry in entries]
    for name, info in snapshot:
        budget[0] += 1
        if budget[0] > MAX_NODES:
            fail("build session has too many nodes")
        if info.st_uid != os.geteuid():
            fail("build session contains an unowned entry")
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                fail("build session contains a linked file")
            budget[1] += info.st_size
            if budget[1] > MAX_BYTES:
                fail("build session is too large")
            result.append((name, identity(info), None))
        elif stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if identity(opened) != identity(info):
                    fail("build session directory changed")
                children = inventory(child, depth + 1, budget)
            finally:
                os.close(child)
            result.append((name, identity(info), children))
        else:
            fail("build session contains an unsafe entry")
    return result


def remove_inventory(descriptor, records):
    if set(os.listdir(descriptor)) != {record[0] for record in records}:
        fail("build session contents changed")
    for name, expected, children in records:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if identity(current) != expected:
            fail("build session entry changed")
        if children is None:
            os.unlink(name, dir_fd=descriptor)
            continue
        child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
        try:
            if identity(os.fstat(child)) != expected:
                fail("build session directory changed")
            remove_inventory(child, children)
            os.fsync(child)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=descriptor)


def prune(root, minimum_age):
    root_info = root.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid()
            or root_info.st_mode & 0o022):
        fail("build session root is unsafe")
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                      | getattr(os, "O_NOFOLLOW", 0))
    try:
        cutoff = time.time_ns() - minimum_age * 1_000_000_000
        for name in os.listdir(root_fd):
            if SESSION.fullmatch(name) is None:
                continue
            try:
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mtime_ns >= cutoff):
                continue
            session_fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
            lock_fd = None
            try:
                if identity(os.fstat(session_fd)) != identity(info):
                    continue
                lock_fd = os.open(".active.lock", os.O_RDWR | os.O_CREAT
                                  | getattr(os, "O_NOFOLLOW", 0), 0o600,
                                  dir_fd=session_fd)
                os.fchmod(lock_fd, 0o600)
                lock_info = os.fstat(lock_fd)
                if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.geteuid()
                        or lock_info.st_nlink != 1):
                    continue
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                if identity(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) != identity(info):
                    continue
                records = inventory(session_fd, 0, [0, 0])
                remove_inventory(session_fd, records)
                os.fsync(session_fd)
                os.rmdir(name, dir_fd=root_fd)
                os.fsync(root_fd)
            except (OSError, ValueError):
                continue
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
                os.close(session_fd)
    finally:
        os.close(root_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--minimum-age-seconds", type=int, default=24 * 60 * 60)
    args = parser.parse_args()
    if (not args.root.is_absolute() or args.root.is_symlink()
            or not args.root.is_dir()
            or not 60 <= args.minimum_age_seconds <= 30 * 24 * 60 * 60):
        raise SystemExit("build session retention arguments are unsafe")
    try:
        prune(args.root, args.minimum_age_seconds)
    except (OSError, ValueError):
        raise SystemExit("build session retention arguments are unsafe") from None


if __name__ == "__main__":
    main()
