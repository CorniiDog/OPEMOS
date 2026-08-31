#!/usr/bin/env python3
"""Atomically enforce the project NVIDIA arguments in a mounted GRUB config."""

import argparse
import errno
import os
import re
import tempfile
from pathlib import Path


REQUIRED = (
    "rd.driver.blacklist=nouveau",
    "modprobe.blacklist=nouveau",
    "nvidia-drm.modeset=1",
    "nvidia-drm.fbdev=1",
)
REQUIRED_KEYS = {argument.split("=", 1)[0] for argument in REQUIRED}
LINUX_LINE = re.compile(r"^(\s*(?:linux|linuxefi|linux16)\s+\S+)(?:\s+(.*?))?\s*$")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grub-config", required=True, type=Path)
    return parser.parse_args()


def main():
    path = parse_args().grub_config
    if path.is_symlink() or not path.is_file():
        raise SystemExit("GRUB configuration must be a regular non-symlink file.")
    original = path.read_text(encoding="utf-8")
    output = []
    linux_entries = 0
    for line in original.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        content = line[:-1] if newline else line
        match = LINUX_LINE.match(content)
        if not match:
            output.append(line)
            continue
        linux_entries += 1
        arguments = (match.group(2) or "").split()
        comment = []
        for index, argument in enumerate(arguments):
            if argument.startswith("#"):
                comment = arguments[index:]
                arguments = arguments[:index]
                break
        arguments = [
            argument for argument in arguments
            if argument.split("=", 1)[0] not in REQUIRED_KEYS
        ]
        updated_arguments = arguments + list(REQUIRED) + comment
        output.append(f"{match.group(1)} {' '.join(updated_arguments)}{newline}")
    if linux_entries == 0:
        raise SystemExit("GRUB configuration contains no recognized Linux boot entries.")
    updated = "".join(output)
    if updated == original:
        return

    mode = path.stat().st_mode & 0o777
    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, mode)
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP):
                raise
        os.replace(temporary_name, path)
        temporary_name = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            try:
                os.fsync(directory)
            except OSError as error:
                if error.errno not in (errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP):
                    raise
        finally:
            os.close(directory)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
