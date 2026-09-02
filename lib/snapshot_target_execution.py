#!/usr/bin/env python3
"""Snapshot and revalidate target-owned pacman/mkinitcpio execution inputs."""

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
from pathlib import Path

from atomic_output import atomic_create_bytes


MAX_FILES = 4096
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 16 * 1024
SAFE_RELATIVE = re.compile(r"[A-Za-z0-9._+~/-]{1,512}")
HOOK_EXEC = re.compile(r"^[ \t]*Exec[ \t]*=[ \t]*(.+?)\s*$")
SCAN_PATHS = (
    "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
    "etc/mkinitcpio.conf", "etc/mkinitcpio.conf.d", "etc/mkinitcpio.d",
    "usr/lib/initcpio", "usr/share/libalpm/hooks",
)


class SnapshotFailure(Exception):
    def __init__(self, message, condition="invalid_execution_input", relative=None):
        super().__init__(message)
        self.message = message
        self.condition = condition
        self.relative = relative


def fail(message, condition="invalid_execution_input", relative=None):
    raise SnapshotFailure(message, condition, relative)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--diagnostic", type=Path)
    args = parser.parse_args()
    if (args.output is None) == (args.verify is None):
        parser.error("exactly one of --output or --verify is required")
    return args


def safe_root(path):
    if not path.is_absolute() or path.is_symlink():
        fail("target root is unsafe")
    try:
        root = path.resolve(strict=True)
    except OSError:
        fail("target root is unavailable")
    metadata = root.stat()
    if not root.is_dir() or metadata.st_mode & 0o022:
        fail("target root is not a directory")
    return root


def inspect(root):
    owner = root.stat().st_uid
    records = []
    recorded = set()
    total = 0
    executors = set()

    local_hooks = root / "etc/pacman.d/hooks"
    if local_hooks.exists() or local_hooks.is_symlink():
        fail("local pacman hook overrides are not permitted",
             "local_hook_override", "etc/pacman.d/hooks")

    def require_safe_directory(path, relative):
        """Validate lexical and resolved parent chains without following escapes."""
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            fail(f"execution input has an escaping parent: {relative}",
                 "parent_symlink_escape", relative)

        # A target may legitimately use a relative, root-owned directory alias
        # such as /bin -> usr/bin.  Trust the alias only when every lexical
        # component and every component of its canonical target is confined,
        # root-owned, a directory, and not group/world writable.
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            try:
                metadata = current.lstat()
            except OSError:
                fail(f"execution input has an unavailable parent: {relative}",
                     "parent_unavailable", relative)
            if stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != owner:
                    fail(f"execution input has an unsafe parent: {relative}",
                         "parent_symlink_ownership", relative)
            elif (not stat.S_ISDIR(metadata.st_mode)
                  or metadata.st_uid != owner
                  or metadata.st_mode & 0o022):
                fail(f"execution input has an unsafe parent: {relative}",
                     "unsafe_parent", relative)

        current = root
        for part in resolved.relative_to(root).parts:
            current = current / part
            try:
                metadata = current.lstat()
            except OSError:
                fail(f"execution input has an unavailable parent: {relative}",
                     "parent_unavailable", relative)
            if (stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != owner
                    or metadata.st_mode & 0o022):
                fail(f"execution input has an unsafe resolved parent: {relative}",
                     "unsafe_resolved_parent", relative)

    def record(path, required=False):
        nonlocal total
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            fail("execution input path is outside the target",
                 "input_escape")
        try:
            logical_metadata = path.lstat()
        except OSError:
            if required:
                fail("required execution input is absent",
                     "required_input_missing", relative)
            return
        if relative in recorded:
            return
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(root)
        except (OSError, ValueError):
            fail(f"execution input escapes the target: {relative}",
                 "input_escape", relative)
        require_safe_directory(path.parent, relative)
        if stat.S_ISLNK(logical_metadata.st_mode):
            fail(f"execution input is a symlink: {relative}",
                 "input_symlink", relative)
        try:
            metadata = resolved_path.lstat()
        except OSError:
            fail(f"execution input is unavailable: {relative}",
                 "input_unavailable", relative)
        if metadata.st_uid != owner or metadata.st_mode & 0o022:
            fail(f"execution input has unsafe ownership or mode: {relative}",
                 "unsafe_input_metadata", relative)
        recorded.add(relative)
        if stat.S_ISDIR(metadata.st_mode):
            records.append({"path": relative, "kind": "directory",
                            "mode": stat.S_IMODE(metadata.st_mode)})
            try:
                children = sorted(resolved_path.iterdir(), key=lambda item: item.name)
            except OSError:
                fail(f"execution directory is unreadable: {relative}")
            for child in children:
                record(child)
            return
        if not stat.S_ISREG(metadata.st_mode):
            fail(f"execution input is not a regular file: {relative}")
        if metadata.st_size > MAX_FILE_BYTES:
            fail(f"execution input is excessive: {relative}")
        total += metadata.st_size
        if total > MAX_TOTAL_BYTES:
            fail("execution inputs exceed their total byte limit")
        descriptor = None
        try:
            descriptor = os.open(resolved_path,
                                 os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if ((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid, opened.st_size)
                    != (metadata.st_dev, metadata.st_ino, metadata.st_mode,
                        metadata.st_uid, metadata.st_size)):
                fail(f"execution input was replaced while being opened: {relative}")
            payload = b""
            while len(payload) <= MAX_FILE_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload += chunk
        except OSError:
            fail(f"execution input is unreadable: {relative}")
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(payload) != metadata.st_size:
            fail(f"execution input changed while being read: {relative}")
        try:
            current_metadata = path.lstat()
            current_resolved = path.resolve(strict=True)
        except OSError:
            fail(f"execution input changed while being read: {relative}",
                 "input_changed", relative)
        if (current_resolved != resolved_path
                or (current_metadata.st_dev, current_metadata.st_ino,
                    current_metadata.st_mode, current_metadata.st_uid,
                    current_metadata.st_size)
                != (metadata.st_dev, metadata.st_ino, metadata.st_mode,
                    metadata.st_uid, metadata.st_size)):
            fail(f"execution input changed while being read: {relative}",
                 "input_changed", relative)
        records.append({"path": relative, "kind": "file",
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()})
        if relative.endswith(".hook"):
            try:
                text = payload.decode("utf-8")
            except UnicodeError:
                fail(f"pacman hook is not UTF-8: {relative}")
            matches = [HOOK_EXEC.fullmatch(line) for line in text.splitlines()]
            commands = [match.group(1) for match in matches if match]
            if len(commands) != 1:
                fail(f"pacman hook must define exactly one executor: {relative}")
            try:
                words = shlex.split(commands[0])
            except ValueError:
                fail(f"pacman hook executor is malformed: {relative}")
            if not words or not words[0].startswith("/") or ".." in Path(words[0]).parts:
                fail(f"pacman hook executor is unsafe: {relative}")
            executors.add(words[0].lstrip("/"))

    for tool_name in ("mkinitcpio", "lsinitcpio"):
        tool = root / "usr/bin" / tool_name
        record(tool, required=True)
        if not os.access(tool, os.X_OK):
            fail(f"target {tool_name} is not executable")
    for relative in SCAN_PATHS:
        record(root / relative)
    for relative in sorted(executors):
        executor = root / relative
        record(executor, required=True)
        try:
            resolved = executor.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            fail(f"pacman hook executor escapes the target: {relative}")
        if not os.access(executor, os.X_OK):
            fail(f"pacman hook executor is not executable: {relative}")
    if len(records) > MAX_FILES:
        fail("too many target execution inputs")
    records.sort(key=lambda item: (item["path"], item["kind"]))
    return {"schemaVersion": 1, "status": "verified", "files": records}


def load_manifest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOTAL_BYTES:
        fail("execution manifest is unsafe")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("execution manifest is invalid")
    if (not isinstance(document, dict) or document.get("schemaVersion") != 1
            or document.get("status") != "verified" or not isinstance(document.get("files"), list)):
        fail("execution manifest schema is invalid")
    return document


def main():
    args = arguments()
    try:
        root = safe_root(args.root)
        current = inspect(root)
        if args.verify is not None:
            if current != load_manifest(args.verify):
                fail("target execution inputs changed after validation",
                     "execution_inputs_changed")
            print(json.dumps({"schemaVersion": 1, "status": "verified",
                              "files": len(current["files"])}, sort_keys=True,
                             separators=(",", ":")))
            return
        try:
            atomic_create_bytes(
                args.output,
                (json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                mode=0o600,
            )
        except FileExistsError:
            fail("refusing to overwrite an execution manifest",
                 "manifest_exists")
        except OSError:
            fail("execution manifest could not be created",
                 "manifest_create_failed")
    except SnapshotFailure as error:
        if args.diagnostic is not None:
            relative = error.relative
            if (relative is not None
                    and (SAFE_RELATIVE.fullmatch(relative) is None
                         or Path(relative).is_absolute()
                         or ".." in Path(relative).parts)):
                relative = None
            diagnostic = {
                "schemaVersion": 1,
                "status": "failed",
                "reason": "target_execution_trust_failed",
                "condition": error.condition,
                "message": error.message[:512],
                "targetRelativePath": relative,
            }
            payload = (json.dumps(diagnostic, sort_keys=True,
                                  separators=(",", ":")) + "\n").encode()
            if len(payload) <= MAX_DIAGNOSTIC_BYTES:
                try:
                    atomic_create_bytes(args.diagnostic, payload, mode=0o600)
                except (FileExistsError, OSError):
                    pass
        raise SystemExit(f"snapshot_target_execution.py: {error.message}")


if __name__ == "__main__":
    main()
