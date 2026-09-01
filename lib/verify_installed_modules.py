#!/usr/bin/env python3
"""Verify the exact installed NVIDIA module payload after mutation."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path


EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
}
MAX_VALIDATION_BYTES = 16 * 1024 * 1024
MAX_MODULE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_SECONDS = 300


def fail(message):
    raise SystemExit(message)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--validation", required=True, type=Path)
    return parser.parse_args()


def safe_directory(root, relative):
    candidate = root
    for component in relative.parts:
        candidate = candidate / component
        try:
            mode = os.lstat(candidate).st_mode
        except OSError:
            fail("Installed module directory is missing.")
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            fail("Installed module directory is unsafe.")
    return candidate


def load_expected(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or path.stat().st_size > MAX_VALIDATION_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("Verified module metadata is unavailable.")
    records = document.get("modules") if isinstance(document, dict) else None
    if (not isinstance(records, list) or len(records) != len(EXPECTED_MODULES)):
        fail("Verified module metadata is malformed.")
    result = {}
    for record in records:
        if (not isinstance(record, dict)
                or set(record) != {"name", "payloadSha256"}
                or record.get("name") not in EXPECTED_MODULES
                or record["name"] in result
                or not isinstance(record.get("payloadSha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", record["payloadSha256"]) is None):
            fail("Verified module metadata is malformed.")
        result[record["name"]] = record["payloadSha256"]
    return result


def payload_sha256(path, deadline):
    process = None
    try:
        if path.name.endswith(".zst"):
            process = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stream = process.stdout
        else:
            stream = path.open("rb")
        digest = hashlib.sha256()
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                if time.monotonic() >= deadline:
                    fail("Installed module verification exceeded its time limit.")
                digest.update(chunk)
        if process is not None and process.wait() != 0:
            fail("An installed module is unreadable.")
        return digest.hexdigest()
    except OSError:
        fail("Installed module verification could not complete.")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def main():
    args = arguments()
    if (not args.root.is_absolute() or args.root.is_symlink()
            or not args.root.is_dir()
            or re.fullmatch(r"[A-Za-z0-9._+~-]{1,255}", args.kernel) is None):
        fail("Installed module target is unsafe.")
    expected = load_expected(args.validation)
    destination = safe_directory(
        args.root,
        Path("usr/lib/modules") / args.kernel
        / "updates/open-gpu-kernel-modules-steamos",
    )
    try:
        entries = list(destination.iterdir())
    except OSError:
        fail("Installed module set is unreadable.")
    try:
        unsafe_entry = any(
            path.is_symlink() or not path.is_file()
            or path.stat().st_size > MAX_MODULE_BYTES
            for path in entries
        )
    except OSError:
        fail("Installed module set is unreadable.")
    if len(entries) != len(EXPECTED_MODULES) or unsafe_entry:
        fail("Installed module set is incomplete or unsafe.")
    normalized = {path.name.removesuffix(".zst"): path for path in entries}
    if set(normalized) != EXPECTED_MODULES or len(normalized) != len(entries):
        fail("Installed module set is incomplete or ambiguous.")
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    test_mode = os.environ.get("PROJECT_TEST_MODE") == "1"
    for name in sorted(EXPECTED_MODULES):
        path = normalized[name]
        metadata = os.lstat(path)
        if (stat.S_IMODE(metadata.st_mode) != 0o644
                or (not test_mode and (metadata.st_uid != 0 or metadata.st_gid != 0))
                or payload_sha256(path, deadline) != expected[name]):
            fail("An installed module differs from its verified payload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
