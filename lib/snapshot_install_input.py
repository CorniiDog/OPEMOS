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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--max-bytes", required=True, type=int)
    args = parser.parse_args()

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(args.source, flags)
    try:
        before = os.fstat(source_fd)
        if (not stat.S_ISREG(before.st_mode)
                or args.max_bytes <= 0
                or not 0 < before.st_size <= args.max_bytes):
            raise OSError("installer input is not a regular file")
        destination_fd = os.open(
            args.destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.fchmod(destination_fd, 0o600)
            total = 0
            while True:
                chunk = os.read(source_fd, CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > args.max_bytes:
                    raise OSError("installer input exceeds its snapshot limit")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("installer input snapshot write failed")
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        path_after = os.stat(args.source, follow_symlinks=False)
        if identity(before) != identity(after) or identity(after) != identity(path_after):
            raise OSError("installer input changed during snapshot creation")
    except BaseException:
        try:
            args.destination.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(source_fd)


if __name__ == "__main__":
    try:
        main()
    except OSError:
        raise SystemExit("installer input snapshot failed") from None
