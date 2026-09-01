#!/usr/bin/env python3
"""Stream a reviewed bzip2 disk image into a new bounded output file."""

import argparse
import bz2
import os
import stat
from pathlib import Path


def fail(message):
    raise SystemExit(f"decompress_bzip2_image.py: {message}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-bytes", required=True, type=int)
    args = parser.parse_args()
    if not 0 < args.expected_bytes <= 32 * 1024 * 1024 * 1024:
        parser.error("expected size is outside the supported bound")
    temporary = args.output.with_name(args.output.name + ".partial")
    input_fd = output_fd = None
    try:
        metadata = args.input.lstat()
        if args.input.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        if args.output.exists() or args.output.is_symlink() or temporary.exists():
            fail("refusing to overwrite image output")
        input_fd = os.open(args.input, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(input_fd)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
                metadata.st_dev, metadata.st_ino, metadata.st_size):
            raise OSError
        output_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        decompressor = bz2.BZ2Decompressor()
        written = 0
        while True:
            compressed = os.read(input_fd, 1024 * 1024)
            if not compressed:
                break
            while True:
                remaining = args.expected_bytes + 1 - written
                if remaining <= 0:
                    fail("decompressed image exceeds reviewed size")
                data = decompressor.decompress(compressed, max_length=min(1024 * 1024, remaining))
                compressed = b""
                written += len(data)
                if written > args.expected_bytes:
                    fail("decompressed image exceeds reviewed size")
                view = memoryview(data)
                while view:
                    count = os.write(output_fd, view)
                    view = view[count:]
                if decompressor.needs_input or decompressor.eof:
                    break
        if not decompressor.eof or decompressor.unused_data or written != args.expected_bytes:
            fail("decompressed image is partial, concatenated, or has the wrong size")
        os.fsync(output_fd)
        os.close(output_fd)
        output_fd = None
        os.link(temporary, args.output)
        temporary.unlink()
    except (OSError, EOFError):
        fail("reviewed image decompression failed")
    finally:
        if input_fd is not None:
            os.close(input_fd)
        if output_fd is not None:
            os.close(output_fd)
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
