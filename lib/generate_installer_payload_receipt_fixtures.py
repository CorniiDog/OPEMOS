#!/usr/bin/env python3
"""Emit canonical bounded payload-receipt compatibility fixtures."""

import copy
import json
import sys

from generate_installer_result_fixtures import KERNEL, payload_receipt
from payload_receipt import MAX_INPUTS as PAYLOAD_RECEIPT_INPUTS, receipt_id


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 64 * 1024
OTHER_KERNEL = "6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45"


def case(name, document, record=True, proof=None):
    return {
        "name": name,
        "expected": {
            "recordAccepted": record,
            "successProofAccepted": record if proof is None else proof,
        },
        "document": document,
    }


def changed(base, update, recompute=False):
    document = copy.deepcopy(base)
    update(document)
    if recompute:
        document["receiptId"] = receipt_id(document["target"], document["records"])
    return document


def main():
    valid = payload_receipt()
    cases = [
        case("valid-normalized-success", valid),
        case("safe-additive-top-level", changed(
            valid, lambda value: value.update({
                "producer": {"name": "payload_receipt.py", "schemaVersion": 1}
            })
        )),
        case("target-binding-mismatch", changed(
            valid,
            lambda value: value["target"].update({"kernelVersion": OTHER_KERNEL}),
            recompute=True,
        ), True, False),
        case("alternate-record-hash", changed(
            valid,
            lambda value: value["records"][0].update({"sha256": "f" * 64}),
            recompute=True,
        )),
        case("receipt-id-mismatch", changed(
            valid, lambda value: value.update({"receiptId": "0" * 64})
        ), False),
        case("missing-record", changed(
            valid, lambda value: value["records"].pop()
        ), False),
        case("extra-record", changed(
            valid, lambda value: value["records"].append(
                copy.deepcopy(value["records"][-1])
            )
        ), False),
        case("duplicate-record", changed(
            valid, lambda value: value["records"].__setitem__(
                1, copy.deepcopy(value["records"][0])
            )
        ), False),
        case("records-out-of-order", changed(
            valid, lambda value: value["records"].reverse()
        ), False),
        case("unknown-role", changed(
            valid, lambda value: value["records"][0].update({"role": "unknown"})
        ), False),
        case("unsafe-role", changed(
            valid, lambda value: value["records"][0].update({"role": "../role"})
        ), False),
        case("wrong-role-filename", changed(
            valid, lambda value: value["records"][0].update(
                {"filename": "buildInfo.json"}
            )
        ), False),
        case("unsafe-filename", changed(
            valid, lambda value: value["records"][0].update(
                {"filename": "../BUILD-INFO.txt"}
            )
        ), False),
        case("empty-filename", changed(
            valid, lambda value: value["records"][0].update({"filename": ""})
        ), False),
        case("zero-record-size", changed(
            valid, lambda value: value["records"][0].update({"sizeBytes": 0})
        ), False),
        case("malformed-record-hash", changed(
            valid, lambda value: value["records"][0].update({"sha256": "short"})
        ), False),
        case("missing-record-field", changed(
            valid, lambda value: value["records"][0].pop("sha256")
        ), False),
        case("unknown-record-field", changed(
            valid, lambda value: value["records"][0].update({"future": True})
        ), False),
        case("missing-target-field", changed(
            valid, lambda value: value["target"].pop("kernelVersion")
        ), False),
        case("unknown-target-field", changed(
            valid, lambda value: value["target"].update({"root": "/target-root"})
        ), False),
        case("unknown-target-kernel", changed(
            valid, lambda value: value["target"].update({"kernelVersion": "unknown"})
        ), False),
        case("wrong-target-architecture", changed(
            valid, lambda value: value["target"].update({"architecture": "aarch64"})
        ), False),
        case("malformed-target-version", changed(
            valid, lambda value: value["target"].update({"nvidiaVersion": "latest"})
        ), False),
        case("wrong-rootfs-relative-path", changed(
            valid, lambda value: value.update({"rootfsRelativePath": "tmp/receipt.json"})
        ), False),
        case("path-traversal", changed(
            valid, lambda value: value.update({"rootfsRelativePath": "../receipt.json"})
        ), False),
        case("wrong-status", changed(
            valid, lambda value: value.update({"status": "failed"})
        ), False),
        case("wrong-reason", changed(
            valid, lambda value: value.update({"reason": "payload_receipt_committed"})
        ), False),
    ]
    role_slugs = {
        "buildInfo": "build-info",
        "provenance": "provenance",
        "validation": "validation",
        "moduleVerification": "module-verification",
        "userspaceVerification": "userspace-verification",
        "initramfsVerification": "initramfs-verification",
    }
    for index, role in enumerate(PAYLOAD_RECEIPT_INPUTS):
        maximum = PAYLOAD_RECEIPT_INPUTS[role][1]
        slug = role_slugs[role]
        cases.append(case(
            f"maximum-{slug}-size",
            changed(
                valid,
                lambda value, offset=index, size=maximum:
                    value["records"][offset].update({"sizeBytes": size}),
                recompute=True,
            ),
        ))
        cases.append(case(
            f"excessive-{slug}-size",
            changed(
                valid,
                lambda value, offset=index, size=maximum + 1:
                    value["records"][offset].update({"sizeBytes": size}),
            ),
            False,
        ))
    cases.extend([
        {
            "name": "malformed-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": "{",
        },
        {
            "name": "duplicate-json-key",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":1,"schemaVersion":1}',
        },
        {
            "name": "non-finite-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":NaN}',
        },
        {
            "name": "oversized-document",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "documentRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-normalized-success",
                "paddingBytes": MAX_DOCUMENT_BYTES,
            },
        },
    ])
    matrix = {
        "schemaVersion": 1,
        "kind": "opemos-installer-payload-receipt-compatibility-fixtures",
        "payloadReceiptSchemaVersion": 1,
        "target": valid["target"],
        "unfrozenFields": [],
        "failureContract": "outer-installer-result-only",
        "bindingScope": "target-and-self-identity",
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES},
        "cases": cases,
    }
    payload = (json.dumps(matrix, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("Generated payload-receipt fixtures exceed their bound.")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
