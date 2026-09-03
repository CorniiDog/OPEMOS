#!/usr/bin/env python3
"""Verify and report the exact installed NVIDIA module payload after mutation."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from atomic_output import atomic_write_bytes

EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
}
MAX_VALIDATION_BYTES = 16 * 1024 * 1024
MAX_MODULE_BYTES = 1024 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_TOTAL_SECONDS = 300
MAX_PROGRESS_ATTEMPT = 1_000_000
INVALID_FIELD_ORDER = (
    "presence", "representation", "payloadSha256", "mode", "uid", "gid",
    "decompression",
)


def fail(message):
    raise SystemExit(message)


def progress_attempt(value):
    if re.fullmatch(r"[0-9]{1,7}", value) is None:
        raise argparse.ArgumentTypeError("progress attempt must be an integer")
    attempt = int(value, 10)
    if not 0 <= attempt <= MAX_PROGRESS_ATTEMPT:
        raise argparse.ArgumentTypeError("progress attempt is outside its supported range")
    return attempt


def emit_progress(attempt, completed, total):
    if attempt is None:
        return
    record = {
        "schemaVersion": 1,
        "attempt": attempt,
        "phase": "module_verification",
        "indeterminate": False,
        "unit": "items",
        "completed": completed,
        "total": total,
    }
    print(
        "STEAMOS_NVIDIA_PROGRESS "
        + json.dumps(record, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--progress-attempt", type=progress_attempt)
    return parser.parse_args()


def publish(path, document):
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_RESULT_BYTES:
        fail("Installed module verification result exceeds its size limit.")
    atomic_write_bytes(path, payload)


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
                or not 0 < path.stat().st_size <= MAX_VALIDATION_BYTES):
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


def payload_sha256(path, representation, deadline):
    process = None
    digest = hashlib.sha256()
    expanded = 0
    try:
        if representation == ".ko.zst":
            process = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stream = process.stdout
        else:
            stream = path.open("rb")
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                if time.monotonic() >= deadline:
                    if process is not None:
                        process.kill()
                    return None, "timeout"
                expanded += len(chunk)
                if expanded > MAX_MODULE_BYTES:
                    if process is not None:
                        process.kill()
                    return None, "size-limit"
                digest.update(chunk)
        if process is not None and process.wait() != 0:
            return None, "failed"
        if expanded == 0:
            return None, "empty"
        return digest.hexdigest(), (
            "verified" if representation == ".ko.zst" else "not-required"
        )
    except OSError:
        return None, "failed"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def missing_record(name, expected_hash, relative, status):
    return {
        "moduleName": name,
        "targetRelativePath": str(relative / f"{name}.zst"),
        "representation": ".ko.zst",
        "expectedPayloadSha256": expected_hash,
        "actualPayloadSha256": None,
        "expectedMode": "0644",
        "actualMode": None,
        "expectedUid": 0,
        "actualUid": None,
        "expectedGid": 0,
        "actualGid": None,
        "compressedSizeBytes": None,
        "decompressionStatus": status,
        "invalidFields": ["presence"],
    }


def inspect_module(name, expected_hash, relative, candidates, deadline, test_mode):
    if len(candidates) != 1:
        return missing_record(
            name, expected_hash, relative,
            "missing" if not candidates else "ambiguous",
        )
    path = candidates[0]
    representation = ".ko.zst" if path.name.endswith(".ko.zst") else ".ko"
    try:
        metadata = os.lstat(path)
    except OSError:
        return missing_record(name, expected_hash, relative, "unreadable")
    regular = stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
    bounded = regular and 0 < metadata.st_size <= MAX_MODULE_BYTES
    actual_hash, decompression = (
        payload_sha256(path, representation, deadline)
        if bounded else (None, "not-attempted")
    )
    actual_mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
    invalid = {
        "presence": not bounded,
        "representation": representation != ".ko.zst",
        "payloadSha256": actual_hash != expected_hash,
        "mode": actual_mode != "0644",
        "uid": not test_mode and metadata.st_uid != 0,
        "gid": not test_mode and metadata.st_gid != 0,
        "decompression": decompression != "verified",
    }
    return {
        "moduleName": name,
        "targetRelativePath": str(relative / path.name),
        "representation": representation,
        "expectedPayloadSha256": expected_hash,
        "actualPayloadSha256": actual_hash,
        "expectedMode": "0644",
        "actualMode": actual_mode,
        "expectedUid": 0,
        "actualUid": 0 if test_mode else metadata.st_uid,
        "expectedGid": 0,
        "actualGid": 0 if test_mode else metadata.st_gid,
        "compressedSizeBytes": metadata.st_size if representation == ".ko.zst" else None,
        "decompressionStatus": decompression,
        "invalidFields": [field for field in INVALID_FIELD_ORDER if invalid[field]],
    }


def main():
    args = arguments()
    if (not args.root.is_absolute() or args.root.is_symlink()
            or not args.root.is_dir()
            or re.fullmatch(r"[A-Za-z0-9._+~-]{1,255}", args.kernel) is None):
        fail("Installed module target is unsafe.")
    expected = load_expected(args.validation)
    relative = (
        Path("usr/lib/modules") / args.kernel
        / "updates/open-gpu-kernel-modules-steamos"
    )
    parent = safe_directory(args.root, relative.parent)
    destination = parent / relative.name
    if not destination.exists():
        entries = []
    else:
        try:
            mode = os.lstat(destination).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                fail("Installed module directory is unsafe.")
            entries = list(destination.iterdir())
        except OSError:
            fail("Installed module set is unreadable.")
    by_name = {name: [] for name in EXPECTED_MODULES}
    unexpected = []
    for path in entries:
        base = path.name.removesuffix(".zst")
        if base in by_name and path.name in (base, f"{base}.zst"):
            by_name[base].append(path)
        elif len(unexpected) < 16 and re.fullmatch(r"[A-Za-z0-9._+-]{1,255}", path.name):
            unexpected.append(path.name)
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    test_mode = os.environ.get("PROJECT_TEST_MODE") == "1"
    records = []
    for completed, name in enumerate(sorted(EXPECTED_MODULES), start=1):
        records.append(
            inspect_module(name, expected[name], relative, by_name[name], deadline, test_mode)
        )
        emit_progress(args.progress_attempt, completed, len(EXPECTED_MODULES))
    mismatches = [record for record in records if record["invalidFields"]]
    if unexpected:
        mismatches.append({
            "moduleName": "unexpected",
            "targetRelativePath": str(relative),
            "representation": None,
            "expectedPayloadSha256": None,
            "actualPayloadSha256": None,
            "expectedMode": None,
            "actualMode": None,
            "expectedUid": 0,
            "actualUid": None,
            "expectedGid": 0,
            "actualGid": None,
            "compressedSizeBytes": None,
            "decompressionStatus": "not-attempted",
            "invalidFields": ["presence"],
            "unexpectedEntries": sorted(unexpected),
        })
    if mismatches:
        publish(args.output, {
            "schemaVersion": 1,
            "status": "failed",
            "reason": "installed_module_mismatch",
            "message": (
                f"Installed module verification found {len(mismatches)} "
                "mismatched module records."
            ),
            "moduleMismatches": mismatches,
        })
        print(
            f"verify_installed_modules.py: {len(mismatches)} module mismatches",
            file=sys.stderr,
        )
        return 1
    publish(args.output, {
        "schemaVersion": 1,
        "status": "verified",
        "reason": "installed_modules_verified",
        "modules": records,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
