#!/usr/bin/env python3
"""Validate the complete NVIDIA module set produced for an offline target."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


EXPECTED_MODULES = {
    "nvidia.ko",
    "nvidia-drm.ko",
    "nvidia-modeset.ko",
    "nvidia-peermem.ko",
    "nvidia-uvm.ko",
}
MAX_MODULE_BYTES = 1024 * 1024 * 1024


class ValidationFailure(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_output(arguments):
    try:
        return subprocess.run(
            arguments,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise ValidationFailure(
            "module_metadata_invalid", "A module's metadata could not be read."
        ) from error


def validate(args):
    modules = {path.name: path for path in args.modules}
    if len(modules) != len(args.modules) or set(modules) != EXPECTED_MODULES:
        raise ValidationFailure(
            "module_set_incomplete",
            "The build did not produce exactly the five expected NVIDIA modules.",
        )

    results = []
    for name in sorted(modules):
        path = modules[name]
        if not path.is_file() or path.is_symlink():
            raise ValidationFailure(
                "module_set_incomplete", "A required NVIDIA module is missing or unsafe."
            )
        try:
            if path.stat().st_size > MAX_MODULE_BYTES:
                raise ValidationFailure(
                    "module_too_large", "A required NVIDIA module exceeds the size limit."
                )
        except OSError as error:
            raise ValidationFailure(
                "module_set_incomplete", "A required NVIDIA module cannot be inspected."
            ) from error
        version = command_output(["modinfo", "-F", "version", str(path)])
        if version != args.nvidia:
            raise ValidationFailure(
                "module_version_mismatch",
                "A module does not match the requested NVIDIA userspace version.",
            )
        vermagic = command_output(["modinfo", "-F", "vermagic", str(path)])
        if vermagic.split(maxsplit=1)[0] != args.kernel:
            raise ValidationFailure(
                "vermagic_mismatch", "A module does not match the exact target kernel."
            )
        elf_header = command_output(["readelf", "-h", str(path)])
        if args.architecture == "x86_64":
            machine_matches = "Advanced Micro Devices X86-64" in elf_header
        else:
            machine_matches = False
        if not machine_matches:
            raise ValidationFailure(
                "module_architecture_mismatch",
                "A module does not match the target ELF architecture.",
            )
        results.append(
            {
                "name": name,
                "sha256": sha256(path),
                "version": version,
                "architecture": args.architecture,
                "vermagic": vermagic,
            }
        )
    return results


def write_result(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("modules", nargs="*", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        modules = validate(args)
    except ValidationFailure as failure:
        write_result(
            args.output,
            {
                "schemaVersion": 1,
                "status": "failed",
                "reason": failure.reason,
                "message": failure.message,
            },
        )
        raise SystemExit(1)
    write_result(
        args.output,
        {"schemaVersion": 1, "status": "verified", "modules": modules},
    )


if __name__ == "__main__":
    main()
