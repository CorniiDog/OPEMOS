#!/usr/bin/env python3
"""Emit canonical bounded initramfs-workspace compatibility fixtures."""

import copy
import json
import sys


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024
MAX_CAPACITY = 2**63 - 1
MAX_REQUIRED_INODES = 65536
INITRAMFS_RESERVE_BYTES = 160_000_000


def case(name, document, record=True, validated=False, mutation=False):
    return {
        "name": name,
        "expected": {
            "recordAccepted": record,
            "validatedResultAccepted": validated,
            "mutationSuccessAccepted": mutation,
        },
        "document": document,
    }


def changed(base, update):
    document = copy.deepcopy(base)
    update(document)
    return document


def target_available(mode="finite-statvfs"):
    return {
        "schemaVersion": 1, "status": "verified",
        "reason": "initramfs_workspace_target_available",
        "phase": "target_directory", "condition": "available",
        "requiredBytes": 4096, "requiredInodes": 1,
        "availableBytes": 1_000_000_000,
        "availableInodes": 100_000 if mode == "finite-statvfs" else None,
        "inodeCapacityMode": mode, "mode": "1777",
    }


def preparation_required(mode="finite-statvfs"):
    value = target_available(mode)
    value.update({
        "status": "preparation-required",
        "reason": "initramfs_workspace_target_missing",
        "condition": "missing_directory", "mode": None,
    })
    return value


def mounted(mode="finite-statvfs"):
    return {
        "schemaVersion": 1, "status": "verified",
        "reason": "initramfs_workspace_available",
        "phase": "mounted_workspace", "condition": "available",
        "requiredBytes": INITRAMFS_RESERVE_BYTES, "requiredInodes": 4096,
        "availableBytes": 1_000_000_000,
        "availableInodes": 100_000 if mode == "finite-statvfs" else None,
        "inodeCapacityMode": mode, "mode": "1777",
    }


def failed(condition="insufficient_bytes"):
    document = {
        "schemaVersion": 1, "status": "failed",
        "reason": "initramfs_workspace_unavailable",
        "phase": "backing_capacity", "condition": condition,
        "message": "Human-readable fixture wording is intentionally not frozen.",
        "requiredBytes": INITRAMFS_RESERVE_BYTES, "requiredInodes": 4096,
    }
    if condition == "insufficient_bytes":
        document.update({
            "availableBytes": INITRAMFS_RESERVE_BYTES - 1,
            "availableInodes": 100_000,
            "inodeCapacityMode": "finite-statvfs",
        })
    elif condition == "insufficient_inodes":
        document.update({
            "availableBytes": 1_000_000_000, "availableInodes": 1,
            "inodeCapacityMode": "finite-statvfs",
        })
    return document


def main():
    target = target_available()
    prep = preparation_required()
    mounted_finite = mounted()
    cases = [
        case("valid-target-finite", target, True, True, False),
        case("valid-target-bind-inodes", target_available(
            "not-applicable-bind-target"
        ), True, True, False),
        case("valid-preparation-finite", prep, True, True, False),
        case("valid-preparation-bind-inodes", preparation_required(
            "not-applicable-bind-target"
        ), True, True, False),
        case("valid-mounted-finite", mounted_finite, True, False, True),
        case("valid-mounted-dynamic", mounted("dynamic-probed"), True, False, True),
        case("valid-backing-finite", changed(
            mounted_finite, lambda value: value.update({"phase": "backing_capacity"})
        )),
        case("safe-additive-top-level", changed(
            target, lambda value: value.update({
                "producer": {"name": "check_initramfs_workspace.py", "schemaVersion": 1}
            })
        ), True, True, False),
        case("valid-failure-insufficient-bytes", failed()),
        case("valid-failure-insufficient-inodes", failed("insufficient_inodes")),
        case("valid-failure-dynamic-probe", changed(
            failed("insufficient_inodes"), lambda value: value.update({
                "availableInodes": None,
                "inodeCapacityMode": "dynamic-probe-failed",
            })
        )),
        case("storage-reserve-binding-mismatch", changed(
            mounted_finite, lambda value: value.update(
                {"requiredBytes": INITRAMFS_RESERVE_BYTES - 1}
            )
        ), True, False, False),
        case("mutation-inode-binding-mismatch", changed(
            mounted_finite, lambda value: value.update({"requiredInodes": 4095})
        ), True, False, False),
        case("validation-byte-binding-mismatch", changed(
            target, lambda value: value.update({"requiredBytes": 4095})
        ), True, False, False),
        case("validation-inode-binding-mismatch", changed(
            target, lambda value: value.update({"requiredInodes": 2})
        ), True, False, False),
        case("maximum-capacity-and-inodes", changed(
            mounted_finite, lambda value: value.update({
                "requiredBytes": MAX_CAPACITY, "availableBytes": MAX_CAPACITY,
                "requiredInodes": MAX_REQUIRED_INODES,
                "availableInodes": MAX_CAPACITY,
                "phase": "backing_capacity",
            })
        )),
        case("negative-required-bytes", changed(
            target, lambda value: value.update({"requiredBytes": -1})
        ), False),
        case("excessive-required-bytes", changed(
            target, lambda value: value.update({"requiredBytes": MAX_CAPACITY + 1})
        ), False),
        case("excessive-required-inodes", changed(
            target, lambda value: value.update(
                {"requiredInodes": MAX_REQUIRED_INODES + 1}
            )
        ), False),
        case("boolean-required-inodes", changed(
            target, lambda value: value.update({"requiredInodes": True})
        ), False),
        case("negative-available-bytes", changed(
            target, lambda value: value.update({"availableBytes": -1})
        ), False),
        case("nested-available-bytes", changed(
            target, lambda value: value.update({"availableBytes": {"value": 1}})
        ), False),
        case("finite-missing-inodes", changed(
            target, lambda value: value.pop("availableInodes")
        ), False),
        case("finite-null-inodes", changed(
            target, lambda value: value.update({"availableInodes": None})
        ), False),
        case("finite-insufficient-inodes-verified", changed(
            target, lambda value: value.update({"availableInodes": 0})
        ), False),
        case("insufficient-bytes-verified", changed(
            target, lambda value: value.update({"availableBytes": 4095})
        ), False),
        case("dynamic-with-reported-inodes", changed(
            mounted("dynamic-probed"), lambda value: value.update(
                {"availableInodes": 100_000}
            )
        ), False),
        case("dynamic-target-state", changed(
            target, lambda value: value.update({
                "inodeCapacityMode": "dynamic-probed", "availableInodes": None,
            })
        ), False),
        case("bind-mode-mounted-state", changed(
            mounted_finite, lambda value: value.update({
                "inodeCapacityMode": "not-applicable-bind-target",
                "availableInodes": None,
            })
        ), False),
        case("probe-failure-verified", changed(
            mounted_finite, lambda value: value.update({
                "inodeCapacityMode": "dynamic-probe-failed",
                "availableInodes": None,
            })
        ), False),
        case("missing-mode", changed(
            target, lambda value: value.pop("mode")
        ), False),
        case("wrong-mode", changed(
            target, lambda value: value.update({"mode": "0755"})
        ), False),
        case("verified-message", changed(
            target, lambda value: value.update({"message": "not permitted"})
        ), False),
        case("preparation-nonnull-mode", changed(
            prep, lambda value: value.update({"mode": "1777"})
        ), False),
        case("preparation-message", changed(
            prep, lambda value: value.update({"message": "not permitted"})
        ), False),
        case("preparation-insufficient-bytes", changed(
            prep, lambda value: value.update({"availableBytes": 4095})
        ), False),
        case("failure-missing-message", changed(
            failed(), lambda value: value.pop("message")
        ), False),
        case("failure-available-condition", changed(
            failed(), lambda value: value.update({"condition": "available"})
        ), False),
        case("failure-contradictory-bytes", changed(
            failed(), lambda value: value.update(
                {"availableBytes": INITRAMFS_RESERVE_BYTES}
            )
        ), False),
        case("failure-contradictory-inodes", changed(
            failed("insufficient_inodes"), lambda value: value.update(
                {"availableInodes": 4096}
            )
        ), False),
        case("target-reason-contradiction", changed(
            target, lambda value: value.update(
                {"reason": "initramfs_workspace_available"}
            )
        ), False),
        case("mounted-phase-contradiction", changed(
            mounted_finite, lambda value: value.update(
                {"phase": "target_directory"}
            )
        ), False),
        case("unknown-phase", changed(
            target, lambda value: value.update({"phase": "target_workspace"})
        ), False),
        case("missing-required-field", changed(
            target, lambda value: value.pop("condition")
        ), False),
    ]
    cases.extend([
        {
            "name": "malformed-json",
            "expected": {"recordAccepted": False, "validatedResultAccepted": False,
                         "mutationSuccessAccepted": False},
            "rawDocument": "{",
        },
        {
            "name": "duplicate-json-key",
            "expected": {"recordAccepted": False, "validatedResultAccepted": False,
                         "mutationSuccessAccepted": False},
            "rawDocument": '{"schemaVersion":1,"schemaVersion":1}',
        },
        {
            "name": "non-finite-json",
            "expected": {"recordAccepted": False, "validatedResultAccepted": False,
                         "mutationSuccessAccepted": False},
            "rawDocument": '{"schemaVersion":NaN}',
        },
        {
            "name": "oversized-document",
            "expected": {"recordAccepted": False, "validatedResultAccepted": False,
                         "mutationSuccessAccepted": False},
            "documentRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-target-finite",
                "paddingBytes": MAX_DOCUMENT_BYTES,
            },
        },
    ])
    matrix = {
        "schemaVersion": 1,
        "kind": "opemos-installer-initramfs-workspace-compatibility-fixtures",
        "initramfsWorkspaceSchemaVersion": 1,
        "validationStorage": {"initramfsReserveBytes": INITRAMFS_RESERVE_BYTES},
        "unfrozenFields": ["message"],
        "limits": {
            "maxDocumentBytes": MAX_DOCUMENT_BYTES,
            "maxCapacity": MAX_CAPACITY,
            "maxRequiredInodes": MAX_REQUIRED_INODES,
        },
        "cases": cases,
    }
    payload = (json.dumps(matrix, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("Generated initramfs-workspace fixtures exceed their bound.")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
