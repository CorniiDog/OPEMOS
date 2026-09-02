#!/usr/bin/env python3
"""Validate the stable consumer-facing installer result and progress envelope."""

import argparse
import json
import os
import re
import stat
from pathlib import Path


MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_PROGRESS_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_LINE = 4096
TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
PREFIX = "STEAMOS_NVIDIA_PROGRESS "
INITRAMFS_REQUIRED_MODULES = (
    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
)
TRUST_VALUES = {
    "pending-validation", "development-unverified",
    "locally-built-verified", "certified-published",
}
VERSION = re.compile(r"(?:unknown|[0-9]+\.[0-9]+(?:\.[0-9]+)?)")
KERNEL = re.compile(r"(?:unknown|[A-Za-z0-9._+~-]{1,255})")
PLAIN_FILENAME = re.compile(r"[A-Za-z0-9@._+~:-]{1,255}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def read_bounded_regular(path, maximum, allow_empty=False):
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_size > maximum
                or (not allow_empty and before.st_size == 0)):
            raise ValueError("input is missing, linked, empty, or excessive")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
        if (len(payload) > maximum
                or (not allow_empty and not payload)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or (after.st_dev, after.st_ino) != (path_after.st_dev, path_after.st_ino)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or after.st_size != len(payload)):
            raise ValueError("input changed while it was being read")
        return bytes(payload).decode("utf-8")
    finally:
        os.close(descriptor)


def load_document(path, maximum):
    return json.loads(
        read_bounded_regular(path, maximum),
        object_pairs_hook=unique_object,
        parse_constant=reject_json_constant,
    )


def plain_filename(value):
    return (
        value is None
        or (
            isinstance(value, str)
            and PLAIN_FILENAME.fullmatch(value) is not None
            and Path(value).name == value
            and value not in {".", ".."}
        )
    )


def bounded_message(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 2048
        and "\x00" not in value
        and all(character in "\n\t" or ord(character) >= 32 for character in value)
    )


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
    if not bounded_message(document["message"]):
        raise ValueError("result message is malformed")
    if document.get("trust") not in TRUST_VALUES:
        raise ValueError("result trust is malformed")
    target = document["target"]
    target_required = {
        "root", "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture",
    }
    if (not isinstance(target, dict) or not target_required <= set(target)
            or target.get("root") != "/target-root"
            or target.get("architecture") != "x86_64"
            or not isinstance(target.get("steamosVersion"), str)
            or VERSION.fullmatch(target["steamosVersion"]) is None
            or not isinstance(target.get("nvidiaVersion"), str)
            or VERSION.fullmatch(target["nvidiaVersion"]) is None
            or not isinstance(target.get("kernelVersion"), str)
            or KERNEL.fullmatch(target["kernelVersion"]) is None):
        raise ValueError("result target is malformed")
    inputs = document["inputs"]
    input_keys = {"archive", "provenance", "nvidiaUtils", "lib32NvidiaUtils"}
    if (not isinstance(inputs, dict) or set(inputs) != input_keys
            or any(not plain_filename(value) for value in inputs.values())):
        raise ValueError("result inputs are malformed")
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
        if document["reason"] != "install_complete" or document["phase"] != "complete":
            raise ValueError("success terminal state is inconsistent")
        if (document["trust"] == "pending-validation"
                or "unknown" in {
                    target["steamosVersion"], target["kernelVersion"],
                    target["nvidiaVersion"],
                }
                or any(value is None for value in inputs.values())):
            raise ValueError("success lacks exact target or input identity")
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
    elif document["status"] == "validated":
        if document["reason"] != "validation_complete" or document["phase"] != "validated":
            raise ValueError("validated terminal state is inconsistent")
        if (document["trust"] == "pending-validation"
                or "unknown" in {
                    target["steamosVersion"], target["kernelVersion"],
                    target["nvidiaVersion"],
                }
                or any(value is None for value in inputs.values())):
            raise ValueError("validated result lacks exact target or input identity")
    elif document["status"] == "cancelled" and document["reason"] != "cancelled":
        raise ValueError("cancelled terminal state is inconsistent")
    return document


def validate_progress(path):
    previous = {}
    latest_attempt = -1
    count = 0
    for raw_line in read_bounded_regular(
            path, MAX_PROGRESS_BYTES, allow_empty=True).splitlines():
        if not raw_line.startswith(PREFIX):
            continue
        if len(raw_line.encode()) > MAX_PROGRESS_LINE:
            raise ValueError("progress record is excessive")
        record = json.loads(
            raw_line[len(PREFIX):], object_pairs_hook=unique_object,
            parse_constant=reject_json_constant,
        )
        required = {"schemaVersion", "attempt", "phase", "indeterminate"}
        if not isinstance(record, dict) or not required <= set(record):
            raise ValueError("progress record is incomplete")
        if (record["schemaVersion"] != 1 or not isinstance(record["attempt"], int)
                or isinstance(record["attempt"], bool) or not 0 <= record["attempt"] <= 1_000_000
                or not isinstance(record["phase"], str)
                or TOKEN.fullmatch(record["phase"]) is None
                or not isinstance(record["indeterminate"], bool)):
            raise ValueError("progress envelope is malformed")
        if record["attempt"] < latest_attempt:
            raise ValueError("progress attempt regressed")
        latest_attempt = record["attempt"]
        if record["indeterminate"] and any(
                field in record for field in ("completed", "total", "unit")):
            raise ValueError("indeterminate progress contains determinate fields")
        if not record["indeterminate"]:
            for field in ("completed", "total"):
                if (not isinstance(record.get(field), int) or isinstance(record.get(field), bool)
                        or not 0 <= record[field] <= 2**63 - 1):
                    raise ValueError("progress count is malformed")
            if (record.get("unit") not in {"bytes", "items"}
                    or record["total"] == 0
                    or record["completed"] > record["total"]):
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
