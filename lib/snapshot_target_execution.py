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
HOOK_EXEC = re.compile(r"^[ \t]*Exec[ \t]*=[ \t]*(.+?)\s*$")
SCAN_PATHS = (
    "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
    "etc/mkinitcpio.conf", "etc/mkinitcpio.conf.d", "etc/mkinitcpio.d",
    "usr/lib/initcpio", "usr/share/libalpm/hooks",
)


def fail(message):
    raise SystemExit(f"snapshot_target_execution.py: {message}")


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
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
        fail("local pacman hook overrides are not permitted")

    def record(path, required=False):
        nonlocal total
        try:
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
        except (OSError, ValueError):
            if required:
                fail("required execution input is absent")
            return
        if relative in recorded:
            return
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            fail(f"execution input escapes the target: {relative}")
        parent = path.parent
        while parent != root:
            parent_metadata = parent.lstat()
            if (stat.S_ISLNK(parent_metadata.st_mode)
                    or not stat.S_ISDIR(parent_metadata.st_mode)
                    or parent_metadata.st_uid != owner
                    or parent_metadata.st_mode & 0o022):
                fail(f"execution input has an unsafe parent: {relative}")
            parent = parent.parent
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"execution input is a symlink: {relative}")
        if metadata.st_uid != owner or metadata.st_mode & 0o022:
            fail(f"execution input has unsafe ownership or mode: {relative}")
        recorded.add(relative)
        if stat.S_ISDIR(metadata.st_mode):
            records.append({"path": relative, "kind": "directory",
                            "mode": stat.S_IMODE(metadata.st_mode)})
            try:
                children = sorted(path.iterdir(), key=lambda item: item.name)
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
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
    root = safe_root(args.root)
    current = inspect(root)
    if args.verify is not None:
        if current != load_manifest(args.verify):
            fail("target execution inputs changed after validation")
        print(json.dumps({"schemaVersion": 1, "status": "verified",
                          "files": len(current["files"])}, sort_keys=True,
                         separators=(",", ":")))
        return
    try:
        atomic_create_bytes(args.output,
                            (json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                            mode=0o600)
    except FileExistsError:
        fail("refusing to overwrite an execution manifest")
    except OSError:
        fail("execution manifest could not be created")


if __name__ == "__main__":
    main()
