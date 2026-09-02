#!/usr/bin/env python3
"""Copy one installer input while rejecting replacement or concurrent mutation."""

import argparse
import os
import stat
from pathlib import Path


CHUNK_SIZE = 1024 * 1024


def identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def unlink_if_unchanged(path, expected):
    """Remove only the path created by this process, never a replacement."""
    try:
        current = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
        path.unlink()


def snapshot(source, destination, max_bytes):
    if max_bytes <= 0:
        raise OSError("installer input snapshot limit is invalid")
    # O_NONBLOCK prevents a hostile FIFO from hanging before fstat can reject it.
    # It does not change regular-file reads on supported Unix platforms.
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(source, flags)
    destination_fd = None
    destination_created = None
    try:
        before = os.fstat(source_fd)
        if (not stat.S_ISREG(before.st_mode)
                or not 0 < before.st_size <= max_bytes):
            raise OSError("installer input is not a regular file")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.fchmod(destination_fd, 0o600)
        destination_created = os.fstat(destination_fd)
        total = 0
        while True:
            chunk = os.read(source_fd, CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("installer input exceeds its snapshot limit")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError("installer input snapshot write failed")
                view = view[written:]
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        path_after = os.stat(source, follow_symlinks=False)
        if identity(before) != identity(after) or identity(after) != identity(path_after):
            raise OSError("installer input changed during snapshot creation")
        destination_after = os.fstat(destination_fd)
        destination_path = os.stat(destination, follow_symlinks=False)
        if ((destination_created.st_dev, destination_created.st_ino)
                != (destination_after.st_dev, destination_after.st_ino)
                or identity(destination_after) != identity(destination_path)
                or not stat.S_ISREG(destination_after.st_mode)
                or stat.S_IMODE(destination_after.st_mode) != 0o600
                or destination_after.st_nlink != 1
                or destination_after.st_size != total):
            raise OSError("installer input snapshot destination changed")
    except BaseException:
        if destination_created is not None:
            unlink_if_unchanged(destination, destination_created)
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--max-bytes", required=True, type=int)
    args = parser.parse_args()
    snapshot(args.source, args.destination, args.max_bytes)


if __name__ == "__main__":
    try:
        main()
    except OSError:
        raise SystemExit("installer input snapshot failed") from None
