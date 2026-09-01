#!/usr/bin/env python3
"""Validate the private appliance-backed /var/tmp workspace contract."""

import argparse
import json
import os
import stat
from pathlib import Path

from atomic_output import atomic_write_bytes


MAX_BYTES = 2**63 - 1
MAX_INODES = 2**63 - 1


class WorkspaceFailure(Exception):
    def __init__(self, phase, condition, message, **details):
        super().__init__(message)
        self.phase = phase
        self.condition = condition
        self.message = message
        self.details = details


def bounded_nonnegative(value):
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("capacity must be an integer") from error
    if not 0 <= parsed <= MAX_BYTES:
        raise argparse.ArgumentTypeError("capacity is outside its supported range")
    return parsed


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--backing", required=True, type=Path)
    parser.add_argument("--required-bytes", required=True, type=bounded_nonnegative)
    parser.add_argument("--required-inodes", required=True, type=bounded_nonnegative)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mounted", action="store_true")
    return parser.parse_args()


def publish(path, document):
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 16 * 1024:
        raise SystemExit("Workspace result exceeds its size limit.")
    atomic_write_bytes(path, payload)


def directory_metadata(path, phase, *, expected_mode=None):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError as error:
        raise WorkspaceFailure(
            phase, "missing_directory",
            "The initramfs workspace directory is missing.",
        ) from error
    except OSError as error:
        raise WorkspaceFailure(
            phase, "invalid_type",
            "The initramfs workspace directory could not be inspected.",
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise WorkspaceFailure(
            phase, "invalid_type",
            "The initramfs workspace is not a confined directory.",
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if expected_mode is not None and mode != expected_mode:
        raise WorkspaceFailure(
            phase, "permissions",
            "The initramfs workspace has unsafe permissions.",
            expectedMode=f"{expected_mode:04o}", actualMode=f"{mode:04o}",
        )
    return metadata


def confined_target(root):
    if not root.is_absolute():
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The target root identity is invalid.",
        )
    directory_metadata(root, "target_directory")
    current = root
    for component in ("var", "tmp"):
        current = current / component
        directory_metadata(
            current,
            "target_directory",
            expected_mode=0o1777 if component == "tmp" else None,
        )
    try:
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The initramfs workspace escapes the target root.",
        ) from error
    return current


def available_capacity(path):
    try:
        filesystem = os.statvfs(path)
    except OSError as error:
        raise WorkspaceFailure(
            "backing_capacity", "invalid_type",
            "The initramfs workspace filesystem could not be inspected.",
        ) from error
    available_bytes = filesystem.f_bavail * filesystem.f_frsize
    available_inodes = filesystem.f_favail
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        available_bytes = int(
            os.environ.get("PROJECT_TEST_WORKSPACE_AVAILABLE_BYTES", available_bytes)
        )
        available_inodes = int(
            os.environ.get("PROJECT_TEST_WORKSPACE_AVAILABLE_INODES", available_inodes)
        )
    if not 0 <= available_bytes <= MAX_BYTES or not 0 <= available_inodes <= MAX_INODES:
        raise WorkspaceFailure(
            "backing_capacity", "invalid_type",
            "The initramfs workspace capacity is invalid.",
        )
    return available_bytes, available_inodes


def main():
    args = arguments()
    base = {
        "schemaVersion": 1,
        "requiredBytes": args.required_bytes,
        "requiredInodes": args.required_inodes,
    }
    try:
        target = confined_target(args.root)
        parent_metadata = directory_metadata(args.backing.parent, "backing_directory")
        if stat.S_IMODE(parent_metadata.st_mode) != 0o700:
            raise WorkspaceFailure(
                "backing_directory", "permissions",
                "The private initramfs workspace parent has unsafe permissions.",
                expectedMode="0700",
                actualMode=f"{stat.S_IMODE(parent_metadata.st_mode):04o}",
            )
        backing_metadata = directory_metadata(
            args.backing, "backing_directory", expected_mode=0o1777
        )
        available_bytes, available_inodes = available_capacity(args.backing)
        base.update({
            "availableBytes": available_bytes,
            "availableInodes": available_inodes,
        })
        if available_bytes < args.required_bytes:
            raise WorkspaceFailure(
                "backing_capacity", "insufficient_bytes",
                "The initramfs workspace lacks sufficient bytes.",
                availableBytes=available_bytes,
            )
        if available_inodes < args.required_inodes:
            raise WorkspaceFailure(
                "backing_capacity", "insufficient_inodes",
                "The initramfs workspace lacks sufficient inodes.",
                availableInodes=available_inodes,
            )
        if args.mounted and os.environ.get("PROJECT_TEST_MODE") != "1":
            try:
                target_metadata = os.stat(target)
            except OSError as error:
                raise WorkspaceFailure(
                    "mounted_workspace", "invalid_type",
                    "The mounted initramfs workspace could not be inspected.",
                ) from error
            if ((target_metadata.st_dev, target_metadata.st_ino)
                    != (backing_metadata.st_dev, backing_metadata.st_ino)):
                raise WorkspaceFailure(
                    "mounted_workspace", "invalid_type",
                    "The target initramfs workspace is not the validated backing directory.",
                )
        publish(args.output, {
            **base,
            "status": "verified",
            "reason": "initramfs_workspace_available",
            "phase": "mounted_workspace" if args.mounted else "backing_capacity",
            "condition": "available",
            "mode": "1777",
        })
        return 0
    except (ValueError, WorkspaceFailure) as error:
        if isinstance(error, WorkspaceFailure):
            failure = error
        else:
            failure = WorkspaceFailure(
                "backing_capacity", "invalid_type",
                "The initramfs workspace capacity override is invalid.",
            )
        publish(args.output, {
            **base,
            **failure.details,
            "status": "failed",
            "reason": "initramfs_workspace_unavailable",
            "message": failure.message,
            "phase": failure.phase,
            "condition": failure.condition,
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
