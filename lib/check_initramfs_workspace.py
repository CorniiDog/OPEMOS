#!/usr/bin/env python3
"""Validate the private appliance-backed /var/tmp workspace contract."""

import argparse
import errno
import json
import os
import stat
import tempfile
from pathlib import Path

from atomic_output import atomic_write_bytes


MAX_BYTES = 2**63 - 1
MAX_INODES = 2**63 - 1
MAX_REQUIRED_INODES = 65536


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


def bounded_inode_requirement(value):
    parsed = bounded_nonnegative(value)
    if parsed > MAX_REQUIRED_INODES:
        raise argparse.ArgumentTypeError("inode requirement exceeds its probe limit")
    return parsed


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--backing", type=Path)
    parser.add_argument("--required-bytes", required=True, type=bounded_nonnegative)
    parser.add_argument("--required-inodes", required=True,
                        type=bounded_inode_requirement)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mounted", action="store_true")
    parser.add_argument("--target-only", action="store_true")
    parser.add_argument("--create-missing-target", action="store_true")
    args = parser.parse_args()
    if args.target_only == (args.backing is not None):
        parser.error("select exactly one target-only or backing workspace check")
    if args.create_missing_target and not args.target_only:
        parser.error("target creation is valid only with --target-only")
    return args


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


def confined_target(root, *, create_missing=False):
    if not root.is_absolute():
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The target root identity is invalid.",
        )
    directory_metadata(root, "target_directory")
    current = root / "var"
    directory_metadata(current, "target_directory")
    target = current / "tmp"
    try:
        target_metadata = os.lstat(target)
    except FileNotFoundError:
        if not create_missing:
            return target, None
        parent_fd = None
        target_fd = None
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            parent_fd = os.open(current, directory_flags)
            os.mkdir("tmp", 0o1777, dir_fd=parent_fd)
            target_fd = os.open("tmp", directory_flags, dir_fd=parent_fd)
            os.fchmod(target_fd, 0o1777)
            if os.environ.get("PROJECT_TEST_MODE") != "1":
                os.fchown(target_fd, 0, 0)
            target_metadata = os.fstat(target_fd)
        except OSError as error:
            raise WorkspaceFailure(
                "target_directory", "missing_directory",
                "The missing target initramfs workspace could not be created.",
            ) from error
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if parent_fd is not None:
                os.close(parent_fd)
    except OSError as error:
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The target initramfs workspace could not be inspected.",
        ) from error
    if stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISDIR(target_metadata.st_mode):
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The target initramfs workspace is not a directory.",
        )
    mode = stat.S_IMODE(target_metadata.st_mode)
    if mode != 0o1777 or (
            os.environ.get("PROJECT_TEST_MODE") != "1"
            and (target_metadata.st_uid != 0 or target_metadata.st_gid != 0)):
        raise WorkspaceFailure(
            "target_directory", "permissions",
            "The target initramfs workspace has unsafe ownership or permissions.",
            expectedMode="1777", actualMode=f"{mode:04o}",
        )
    current = target
    try:
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceFailure(
            "target_directory", "invalid_type",
            "The initramfs workspace escapes the target root.",
        ) from error
    return current, target_metadata


def probe_dynamic_inode_capacity(path, required_inodes):
    probe = None
    descriptor = None
    created = 0
    failure = None
    try:
        probe = Path(tempfile.mkdtemp(prefix=".inode-capacity-", dir=path))
        descriptor = os.open(
            probe,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        for index in range(required_inodes):
            file_descriptor = os.open(
                f"inode-{index}",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=descriptor,
            )
            created += 1
            os.close(file_descriptor)
    except OSError as error:
        if error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
            failure = WorkspaceFailure(
                "backing_capacity", "insufficient_inodes",
                "The initramfs workspace could not allocate its required inodes.",
                availableInodes=None, inodeCapacityMode="dynamic-probe-failed",
            )
        elif error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            failure = WorkspaceFailure(
                "backing_capacity", "permissions",
                "The initramfs workspace inode-capacity probe was not writable.",
                availableInodes=None, inodeCapacityMode="dynamic-probe-failed",
            )
        else:
            failure = WorkspaceFailure(
                "backing_capacity", "invalid_type",
                "The initramfs workspace inode-capacity probe failed.",
                availableInodes=None, inodeCapacityMode="dynamic-probe-failed",
            )
    finally:
        cleanup_failed = False
        if descriptor is not None:
            for index in range(created):
                try:
                    os.unlink(f"inode-{index}", dir_fd=descriptor)
                except OSError:
                    cleanup_failed = True
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if probe is not None:
            try:
                probe.rmdir()
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            failure = WorkspaceFailure(
                "backing_capacity", "invalid_type",
                "The initramfs workspace inode-capacity probe could not be cleaned.",
                availableInodes=None, inodeCapacityMode="dynamic-probe-failed",
            )
    if failure is not None:
        raise failure


def available_capacity(path, *, target=False, required_inodes=0):
    try:
        filesystem = os.statvfs(path)
    except OSError as error:
        raise WorkspaceFailure(
            "backing_capacity", "invalid_type",
            "The initramfs workspace filesystem could not be inspected.",
        ) from error
    available_bytes = filesystem.f_bavail * filesystem.f_frsize
    available_inodes = filesystem.f_favail
    dynamic_inodes = (
        filesystem.f_files == 0
        and filesystem.f_ffree == 0
        and filesystem.f_favail == 0
    )
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        prefix = "PROJECT_TEST_TARGET_WORKSPACE" if target else "PROJECT_TEST_WORKSPACE"
        available_bytes = int(
            os.environ.get(f"{prefix}_AVAILABLE_BYTES", available_bytes)
        )
        inode_override = os.environ.get(f"{prefix}_AVAILABLE_INODES")
        if inode_override is not None:
            available_inodes = int(inode_override)
            dynamic_inodes = False
        if os.environ.get(f"{prefix}_DYNAMIC_INODES") == "1":
            available_inodes = 0
            dynamic_inodes = True
    if (not 0 <= available_bytes <= MAX_BYTES
            or not 0 <= available_inodes <= MAX_INODES):
        raise WorkspaceFailure(
            "backing_capacity", "invalid_type",
            "The initramfs workspace capacity is invalid.",
        )
    if dynamic_inodes:
        if target:
            return available_bytes, None, "not-applicable-bind-target"
        probe_dynamic_inode_capacity(path, required_inodes)
        return available_bytes, None, "dynamic-probed"
    return available_bytes, available_inodes, "finite-statvfs"


def main():
    args = arguments()
    base = {
        "schemaVersion": 1,
        "requiredBytes": args.required_bytes,
        "requiredInodes": args.required_inodes,
    }
    try:
        target, target_metadata = confined_target(
            args.root, create_missing=args.create_missing_target
        )
        if args.target_only:
            capacity_path = target if target_metadata is not None else target.parent
            available_bytes, available_inodes, inode_capacity_mode = available_capacity(
                capacity_path, target=True, required_inodes=args.required_inodes
            )
            base.update({
                "availableBytes": available_bytes,
                "availableInodes": available_inodes,
                "inodeCapacityMode": inode_capacity_mode,
            })
            if available_bytes < args.required_bytes:
                raise WorkspaceFailure(
                    "target_capacity", "insufficient_bytes",
                    "The target initramfs workspace lacks sufficient bytes.",
                    availableBytes=available_bytes,
                )
            if (available_inodes is not None
                    and available_inodes < args.required_inodes):
                raise WorkspaceFailure(
                    "target_capacity", "insufficient_inodes",
                    "The target initramfs workspace lacks sufficient inodes.",
                    availableInodes=available_inodes,
                )
            if target_metadata is None:
                publish(args.output, {
                    **base,
                    "status": "preparation-required",
                    "reason": "initramfs_workspace_target_missing",
                    "phase": "target_directory",
                    "condition": "missing_directory",
                    "mode": None,
                })
            else:
                publish(args.output, {
                    **base,
                    "status": "verified",
                    "reason": "initramfs_workspace_target_available",
                    "phase": "target_directory",
                    "condition": "available",
                    "mode": "1777",
                })
            return 0
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
        capacity_path = target if args.mounted else args.backing
        available_bytes, available_inodes, inode_capacity_mode = available_capacity(
            capacity_path, required_inodes=args.required_inodes
        )
        base.update({
            "availableBytes": available_bytes,
            "availableInodes": available_inodes,
            "inodeCapacityMode": inode_capacity_mode,
        })
        if available_bytes < args.required_bytes:
            raise WorkspaceFailure(
                "backing_capacity", "insufficient_bytes",
                "The initramfs workspace lacks sufficient bytes.",
                availableBytes=available_bytes,
            )
        if (available_inodes is not None
                and available_inodes < args.required_inodes):
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
