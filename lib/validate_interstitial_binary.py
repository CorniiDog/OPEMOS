#!/usr/bin/env python3
"""Validate a bounded x86_64 Linux ELF OPEMOS interstitial executable."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024


def inspect(path: Path, expected_sha256: str):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("interstitial binary is not a confined regular file")
    if info.st_size <= 0 or info.st_size > MAX_BYTES:
        raise ValueError("interstitial binary size is invalid")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    try:
        header = os.read(descriptor, 64)
        if len(header) < 20 or header[:4] != b"\x7fELF":
            raise ValueError("interstitial binary is not ELF")
        if header[4:7] != b"\x02\x01\x01":
            raise ValueError("interstitial binary is not 64-bit little-endian ELF")
        if header[7] not in (0, 3):
            raise ValueError("interstitial binary has an unsupported ELF ABI")
        if int.from_bytes(header[16:18], "little") not in (2, 3):
            raise ValueError("interstitial ELF is not executable or position-independent executable")
        if int.from_bytes(header[18:20], "little") != 62:
            raise ValueError("interstitial binary is not x86_64")
        if int.from_bytes(header[20:24], "little") != 1 or int.from_bytes(header[52:54], "little") != 64:
            raise ValueError("interstitial ELF header is inconsistent")
        program_offset = int.from_bytes(header[32:40], "little")
        program_size = int.from_bytes(header[54:56], "little")
        program_count = int.from_bytes(header[56:58], "little")
        if program_size != 56 or not 1 <= program_count <= 256:
            raise ValueError("interstitial ELF program table is invalid")
        if program_offset < 64 or program_offset + program_size * program_count > info.st_size:
            raise ValueError("interstitial ELF program table is out of bounds")
        digest.update(header)
        total = len(header)
        while total <= MAX_BYTES:
            block = os.read(descriptor, min(1024 * 1024, MAX_BYTES + 1 - total))
            if not block:
                break
            digest.update(block)
            total += len(block)
        final = os.fstat(descriptor)
        if total != info.st_size or final.st_dev != info.st_dev or final.st_ino != info.st_ino:
            raise ValueError("interstitial binary changed while it was validated")
    finally:
        os.close(descriptor)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError("interstitial binary SHA-256 does not match")
    return {"schemaVersion": 1, "status": "verified", "architecture": "x86_64",
            "size": info.st_size, "sha256": actual}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    if len(args.sha256) != 64 or any(character not in "0123456789abcdef" for character in args.sha256):
        raise ValueError("interstitial binary SHA-256 is malformed")
    print(json.dumps(inspect(args.binary, args.sha256), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"validate_interstitial_binary.py: {error}") from None
