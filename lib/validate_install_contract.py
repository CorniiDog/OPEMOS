#!/usr/bin/env python3
"""Validate the stable consumer-facing installer result and progress envelope."""

import argparse
import json
import re
from pathlib import Path


MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_PROGRESS_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_LINE = 4096
TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
PREFIX = "STEAMOS_NVIDIA_PROGRESS "
INITRAMFS_REQUIRED_MODULES = (
    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_document(path, maximum):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("input is missing, linked, empty, or excessive")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def validate_result(path):
    document = load_document(path, MAX_RESULT_BYTES)
    required = {"schemaVersion", "status", "reason", "message", "phase", "trust",
                "target", "inputs", "cleanup"}
    if not isinstance(document, dict) or not required <= set(document):
        raise ValueError("result envelope is incomplete")
    if document["schemaVersion"] != 1 or document["status"] not in {
            "success", "failed", "cancelled", "validated"}:
        raise ValueError("result schema or status is unsupported")
    for field in ("reason", "phase"):
        if not isinstance(document[field], str) or TOKEN.fullmatch(document[field]) is None:
            raise ValueError("result token is malformed")
    if not isinstance(document["message"], str) or not 0 < len(document["message"]) <= 2048:
        raise ValueError("result message is malformed")
    target = document["target"]
    if not isinstance(target, dict) or target.get("architecture") != "x86_64":
        raise ValueError("result target is malformed")
    cleanup = document["cleanup"]
    cleanup_required = {"mountsReleased", "runtimeMountsExpected",
                        "runtimeMountsReleased", "compressionPolicyRestored"}
    if not isinstance(cleanup, dict) or not cleanup_required <= set(cleanup):
        raise ValueError("cleanup envelope is incomplete")
    for field in ("mountsReleased", "compressionPolicyRestored"):
        if not isinstance(cleanup[field], bool):
            raise ValueError("cleanup state is malformed")
    for field in ("runtimeMountsExpected", "runtimeMountsReleased"):
        if (not isinstance(cleanup[field], int) or isinstance(cleanup[field], bool)
                or not 0 <= cleanup[field] <= 64):
            raise ValueError("cleanup count is malformed")
    if document["status"] == "success":
        if (not cleanup["mountsReleased"] or not cleanup["compressionPolicyRestored"]
                or cleanup["runtimeMountsExpected"] != cleanup["runtimeMountsReleased"]):
            raise ValueError("success reports incomplete cleanup")
        if not isinstance(document.get("validation"), dict) or not document["validation"]:
            raise ValueError("success lacks validation")
        mandatory = {
            "moduleVerification": "verified",
            "userspaceVerification": "verified", "initramfsWorkspace": "verified",
            "initramfsVerification": "verified",
        }
        for field, status in mandatory.items():
            value = document.get(field)
            if not isinstance(value, dict) or value.get("status") != status:
                raise ValueError(f"success lacks {field}")
        if document["initramfsWorkspace"].get("phase") != "mounted_workspace":
            raise ValueError("success workspace phase is invalid")
        initramfs = document["initramfsVerification"]
        if (initramfs.get("requiredModules") != list(INITRAMFS_REQUIRED_MODULES)
                or initramfs.get("rootfsOnlyModules") != ["nvidia-peermem.ko"]
                or not isinstance(initramfs.get("images"), list)
                or not initramfs["images"]
                or any(not isinstance(image, dict)
                       or not isinstance(image.get("modules"), dict)
                       or set(image["modules"]) != set(INITRAMFS_REQUIRED_MODULES)
                       for image in initramfs["images"])):
            raise ValueError("success initramfs module contract is invalid")
    return document


def validate_progress(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PROGRESS_BYTES:
        raise ValueError("progress stream is linked or excessive")
    previous = {}
    count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.startswith(PREFIX):
            continue
        if len(raw_line.encode()) > MAX_PROGRESS_LINE:
            raise ValueError("progress record is excessive")
        record = json.loads(raw_line[len(PREFIX):], object_pairs_hook=unique_object)
        required = {"schemaVersion", "attempt", "phase", "indeterminate"}
        if not isinstance(record, dict) or not required <= set(record):
            raise ValueError("progress record is incomplete")
        if (record["schemaVersion"] != 1 or not isinstance(record["attempt"], int)
                or isinstance(record["attempt"], bool) or not 0 <= record["attempt"] <= 1_000_000
                or not isinstance(record["phase"], str)
                or TOKEN.fullmatch(record["phase"]) is None
                or not isinstance(record["indeterminate"], bool)):
            raise ValueError("progress envelope is malformed")
        if not record["indeterminate"]:
            for field in ("completed", "total"):
                if (not isinstance(record.get(field), int) or isinstance(record.get(field), bool)
                        or not 0 <= record[field] <= 2**63 - 1):
                    raise ValueError("progress count is malformed")
            if record.get("unit") not in {"bytes", "items"} or record["completed"] > record["total"]:
                raise ValueError("progress range is malformed")
            key = (record["attempt"], record["phase"])
            if key in previous:
                old_completed, old_total, old_unit = previous[key]
                if (record["completed"] < old_completed or record["total"] != old_total
                        or record["unit"] != old_unit):
                    raise ValueError("progress record regressed")
            previous[key] = (record["completed"], record["total"], record["unit"])
        count += 1
    if count == 0:
        raise ValueError("progress stream contains no records")
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--progress", required=True, type=Path)
    options = parser.parse_args()
    try:
        result = validate_result(options.result)
        records = validate_progress(options.progress)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"installer contract rejected: {error}")
    print(json.dumps({"schemaVersion": 1, "status": "verified",
                      "terminalStatus": result["status"], "progressRecords": records},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
