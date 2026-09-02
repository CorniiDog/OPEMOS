#!/usr/bin/env python3
"""Focused regressions for dynamic initramfs-workspace inode admission."""

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import check_initramfs_workspace as workspace  # noqa: E402


def invoke(root, backing, output, *, target_only=False, environment=None,
           required_inodes=64):
    command = [
        sys.executable, str(ROOT / "lib/check_initramfs_workspace.py"),
        "--root", str(root), "--required-bytes", "1",
        "--required-inodes", str(required_inodes), "--output", str(output),
    ]
    if target_only:
        command.append("--target-only")
    else:
        command.extend(("--backing", str(backing)))
    return subprocess.run(
        command,
        env={**os.environ, "PROJECT_TEST_MODE": "1", **(environment or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def fixture(base):
    root = base / "target"
    (root / "var/tmp").mkdir(parents=True)
    (root / "var/tmp").chmod(0o1777)
    private = base / "private"
    private.mkdir(mode=0o700)
    backing = private / "workspace"
    backing.mkdir(mode=0o1777)
    backing.chmod(0o1777)
    return root, backing


def main():
    with tempfile.TemporaryDirectory(prefix="initramfs-workspace-") as temporary:
        base = Path(temporary)
        root, backing = fixture(base)

        workspace.probe_dynamic_inode_capacity(backing, 64)
        assert not list(backing.glob(".inode-capacity-*"))
        original_open = os.open

        def deny_file_creation(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is not None:
                raise OSError(errno.EACCES, "fixture permission failure")
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(workspace.os, "open", side_effect=deny_file_creation):
            try:
                workspace.probe_dynamic_inode_capacity(backing, 1)
                raise AssertionError("an unwritable inode probe was accepted")
            except workspace.WorkspaceFailure as error:
                assert error.condition == "permissions"
        assert not list(backing.glob(".inode-capacity-*"))

        created = 0

        def fail_after_three(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal created
            if dir_fd is not None:
                if created == 3:
                    raise OSError(errno.ENOSPC, "fixture inode exhaustion")
                created += 1
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(workspace.os, "open", side_effect=fail_after_three):
            try:
                workspace.probe_dynamic_inode_capacity(backing, 64)
                raise AssertionError("partial inode exhaustion was accepted")
            except workspace.WorkspaceFailure as error:
                assert error.condition == "insufficient_inodes"
        assert not list(backing.glob(".inode-capacity-*"))

        dynamic_output = base / "dynamic.json"
        dynamic = invoke(
            root, backing, dynamic_output,
            environment={"PROJECT_TEST_WORKSPACE_DYNAMIC_INODES": "1"},
        )
        assert dynamic.returncode == 0, (
            dynamic.stderr, dynamic_output.read_text() if dynamic_output.exists() else None
        )
        dynamic_document = json.loads(dynamic_output.read_text())
        assert dynamic_document["status"] == "verified"
        assert dynamic_document["inodeCapacityMode"] == "dynamic-probed"
        assert dynamic_document["availableInodes"] is None
        assert not list(backing.glob(".inode-capacity-*"))

        target_output = base / "target.json"
        target = invoke(
            root, backing, target_output, target_only=True,
            environment={"PROJECT_TEST_TARGET_WORKSPACE_DYNAMIC_INODES": "1"},
        )
        assert target.returncode == 0, target.stderr
        target_document = json.loads(target_output.read_text())
        assert target_document["inodeCapacityMode"] == (
            "not-applicable-bind-target"
        )
        assert target_document["availableInodes"] is None
        assert not list((root / "var/tmp").glob(".inode-capacity-*"))

        finite_output = base / "finite.json"
        finite = invoke(
            root, backing, finite_output,
            environment={
                "PROJECT_TEST_WORKSPACE_AVAILABLE_BYTES": str(2**30),
                "PROJECT_TEST_WORKSPACE_AVAILABLE_INODES": "1",
            },
        )
        assert finite.returncode != 0
        finite_document = json.loads(finite_output.read_text())
        assert finite_document["condition"] == "insufficient_inodes"
        assert finite_document["inodeCapacityMode"] == "finite-statvfs"

        excessive = invoke(
            root, backing, base / "excessive.json", required_inodes=65537
        )
        assert excessive.returncode == 2
        assert not (base / "excessive.json").exists()

        original_unlink = os.unlink

        def fail_probe_cleanup(path, *, dir_fd=None):
            if dir_fd is not None and path == "inode-0":
                raise OSError(errno.EIO, "fixture cleanup failure")
            return original_unlink(path, dir_fd=dir_fd)

        with mock.patch.object(workspace.os, "unlink", side_effect=fail_probe_cleanup):
            try:
                workspace.probe_dynamic_inode_capacity(backing, 2)
                raise AssertionError("an inode probe cleanup failure was accepted")
            except workspace.WorkspaceFailure as error:
                assert error.condition == "invalid_type"
                assert "cleaned" in error.message
        leftovers = list(backing.glob(".inode-capacity-*"))
        assert len(leftovers) == 1
        shutil.rmtree(leftovers[0])


if __name__ == "__main__":
    main()
