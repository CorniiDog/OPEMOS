#!/usr/bin/env python3
"""Emit the canonical module-verification schema-1 compatibility matrix."""

import copy
import json
import sys

from generate_installer_result_fixtures import KERNEL, module_verification, validation_proof


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024


def case(name, document, record_accepted, success_proof_accepted=None):
    if success_proof_accepted is None:
        success_proof_accepted = record_accepted
    return {
        "name": name,
        "expected": {
            "recordAccepted": record_accepted,
            "successProofAccepted": success_proof_accepted,
        },
        "document": document,
    }


def changed(base, mutator):
    document = copy.deepcopy(base)
    mutator(document)
    return document


def matrix():
    valid = module_verification()
    cases = [case("valid-normalized-success", valid, True)]

    additive = copy.deepcopy(valid)
    additive["producerRevision"] = 2
    cases.append(case("safe-additive-top-level", additive, True))

    failure = copy.deepcopy(valid["modules"][0])
    failure.update({
        "actualPayloadSha256": None,
        "actualMode": None,
        "actualUid": None,
        "actualGid": None,
        "compressedSizeBytes": None,
        "decompressionStatus": "missing",
        "invalidFields": ["presence", "payloadSha256", "mode", "uid", "gid", "decompression"],
    })
    cases.append(case("valid-failure-diagnostic", {
        "schemaVersion": 1,
        "status": "failed",
        "reason": "installed_module_mismatch",
        "message": "Human-readable fixture wording is intentionally not frozen.",
        "moduleMismatches": [failure],
    }, True, False))

    cases.extend([
        case("missing-module", changed(valid, lambda d: d["modules"].pop()), False),
        case("duplicate-module-identity", changed(
            valid, lambda d: d["modules"].__setitem__(4, copy.deepcopy(d["modules"][0]))
        ), False),
        case("unknown-module-identity", changed(
            valid, lambda d: d["modules"][0].__setitem__("moduleName", "nouveau.ko")
        ), False),
        case("oversized-module-set", changed(
            valid, lambda d: d["modules"].append(copy.deepcopy(d["modules"][0]))
        ), False),
        case("payload-hash-binding-mismatch", changed(
            valid,
            lambda d: (
                d["modules"][0].__setitem__("expectedPayloadSha256", "f" * 64),
                d["modules"][0].__setitem__("actualPayloadSha256", "f" * 64),
            ),
        ), True, False),
        case("actual-payload-hash-mismatch", changed(
            valid, lambda d: d["modules"][0].__setitem__("actualPayloadSha256", "f" * 64)
        ), False),
        case("raw-representation", changed(
            valid,
            lambda d: (
                d["modules"][0].__setitem__("representation", ".ko"),
                d["modules"][0].__setitem__("decompressionStatus", "not-required"),
            ),
        ), False),
        case("wrong-kernel-path", changed(
            valid,
            lambda d: d["modules"][0].__setitem__(
                "targetRelativePath",
                d["modules"][0]["targetRelativePath"].replace(KERNEL, "6.0.0-other"),
            ),
        ), True, False),
        case("path-traversal", changed(
            valid,
            lambda d: d["modules"][0].__setitem__(
                "targetRelativePath", "usr/lib/modules/../escape/nvidia.ko.zst"
            ),
        ), False),
        case("mode-mismatch", changed(
            valid, lambda d: d["modules"][0].__setitem__("actualMode", "0600")
        ), False),
        case("uid-mismatch", changed(
            valid, lambda d: d["modules"][0].__setitem__("actualUid", 1000)
        ), False),
        case("gid-mismatch", changed(
            valid, lambda d: d["modules"][0].__setitem__("actualGid", 1000)
        ), False),
        case("decompression-mismatch", changed(
            valid, lambda d: d["modules"][0].__setitem__("decompressionStatus", "failed")
        ), False),
        case("zero-compressed-size", changed(
            valid, lambda d: d["modules"][0].__setitem__("compressedSizeBytes", 0)
        ), False),
        case("missing-required-field", changed(
            valid, lambda d: d["modules"][0].pop("actualUid")
        ), False),
        case("unknown-record-field", changed(
            valid, lambda d: d["modules"][0].__setitem__("hostPath", "/secret")
        ), False),
    ])

    cases.extend([
        {
            "name": "malformed-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":1',
        },
        {
            "name": "duplicate-json-key",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":1,"schemaVersion":1}',
        },
        {
            "name": "non-finite-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":1,"status":NaN}',
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
    return {
        "schemaVersion": 1,
        "kind": "opemos-installer-module-verification-compatibility-fixtures",
        "moduleVerificationSchemaVersion": 1,
        "targetKernel": KERNEL,
        "validationModules": validation_proof()["modules"],
        "unfrozenFields": ["message"],
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES},
        "cases": cases,
    }


def main():
    payload = json.dumps(matrix(), sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise SystemExit("generated module-verification matrix exceeds its output limit")
    sys.stdout.write(payload)


if __name__ == "__main__":
    main()
