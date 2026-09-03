#!/usr/bin/env python3
"""Emit canonical bounded gaming-payload compatibility fixtures."""

import copy
import json
import sys
from pathlib import Path

from gaming_payload_profiles import ROOT, target_record, validate_profile


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024


def case(name, document, record=True, binding=None, validation_patch=None):
    fixture = {
        "name": name,
        "expected": {
            "recordAccepted": record,
            "terminalBindingAccepted": record if binding is None else binding,
        },
        "document": document,
    }
    if validation_patch is not None:
        fixture["validationPatch"] = validation_patch
    return fixture


def changed(base, update):
    document = copy.deepcopy(base)
    update(document)
    return document


def main():
    target = {
        "steamosVersion": "3.8.14",
        "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
        "nvidiaVersion": "575.64.05",
        "architecture": "x86_64",
    }
    authority = target_record(**{
        "steamos": target["steamosVersion"],
        "kernel": target["kernelVersion"],
        "nvidia": target["nvidiaVersion"],
        "architecture": target["architecture"],
    })
    reviewed = validate_profile(
        ROOT / "profiles/gaming" / authority["profileAsset"],
        ROOT / "locks/userspace" / authority["userspaceLockAsset"],
        target,
    )
    validation = {
        "target": target,
        "userspaceLock": {
            "name": authority["userspaceLockAsset"],
            "sha256": authority["userspaceLockSha256"],
        },
        "packages": [{
            "name": record["name"],
            "filename": record["filename"],
            "signatureFilename": record["sourceSignatureFilename"],
            "fullVersion": record["version"],
            "sha256": record["sha256"],
            "signatureSha256": record["sourceSignatureSha256"],
            "signer": record["sourceSignerFingerprint"],
            "installedSize": record["installedSize"],
        } for record in reviewed["packageRecords"]],
    }
    not_requested = {
        "schemaVersion": 1, "status": "not-requested",
        "profileId": "gaming-no-cuda-v1",
    }
    cases = [
        case("valid-not-requested", not_requested),
        case("valid-reviewed", reviewed),
        case("top-level-addition-rejected", changed(
            reviewed, lambda value: value.update({"future": True})
        ), False),
        case("not-requested-addition-rejected", changed(
            not_requested, lambda value: value.update({"reason": "disabled"})
        ), False),
        case("unsupported-status", changed(
            reviewed, lambda value: value.update({"status": "unsupported"})
        ), False),
        case("unreviewed-status", changed(
            reviewed, lambda value: value.update({"status": "unreviewed"})
        ), False),
        case("unknown-profile", changed(
            reviewed, lambda value: value.update({"profileId": "future-profile"})
        ), False),
        case("missing-profile-id", changed(
            reviewed, lambda value: value.pop("profileId")
        ), False),
        case("profile-hash-binding-mismatch", changed(
            reviewed, lambda value: value.update({"sha256": "0" * 64})
        ), True, False),
        case("policy-hash-binding-mismatch", changed(
            reviewed, lambda value: value.update({"policySha256": "0" * 64})
        ), True, False),
        case("userspace-lock-binding-mismatch", reviewed, True, False, {
            "userspaceLock": {"sha256": "0" * 64}
        }),
        case("target-binding-mismatch", changed(
            reviewed, lambda value: value["target"].update(
                {"kernelVersion": value["target"]["kernelVersion"] + "-other"}
            )
        ), True, False),
        case("package-hash-binding-mismatch", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"sha256": "0" * 64}
            )
        ), True, False),
        case("package-version-binding-mismatch", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"version": "575.64.05-1.gaming-no-cuda.2"}
            )
        ), True, False),
        case("source-hash-binding-mismatch", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"sourceSha256": "0" * 64}
            )
        ), True, False),
        case("missing-package", changed(
            reviewed, lambda value: value["packageRecords"].pop()
        ), False),
        case("extra-package", changed(
            reviewed, lambda value: value["packageRecords"].append(
                copy.deepcopy(value["packageRecords"][-1])
            )
        ), False),
        case("duplicate-package", changed(
            reviewed, lambda value: value["packageRecords"].__setitem__(
                1, copy.deepcopy(value["packageRecords"][0])
            )
        ), False),
        case("package-order-mismatch", changed(
            reviewed, lambda value: value["packageRecords"].reverse()
        ), False),
        case("unsafe-package-filename", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"filename": "../package.pkg.tar.zst"}
            )
        ), False),
        case("empty-source-filename", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"sourceFilename": ""}
            )
        ), False),
        case("malformed-signer", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"sourceSignerFingerprint": "abcd"}
            )
        ), False),
        case("negative-installed-size", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"installedSize": -1}
            )
        ), False),
        case("zero-package-saved-bytes", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"savedBytes": 0}
            )
        ), False),
        case("saved-byte-total-mismatch", changed(
            reviewed, lambda value: value.update(
                {"savedBytes": value["savedBytes"] + 1}
            )
        ), False),
        case("missing-package-field", changed(
            reviewed, lambda value: value["packageRecords"][0].pop("sha256")
        ), False),
        case("unknown-package-field", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"future": True}
            )
        ), False),
        case("wrong-omitted-capability", changed(
            reviewed, lambda value: value.update(
                {"omittedCapabilities": ["graphics"]}
            )
        ), False),
        case("missing-preserved-capability", changed(
            reviewed, lambda value: value["preservedCapabilities"].pop()
        ), False),
        case("duplicate-preserved-capability", changed(
            reviewed, lambda value: value["preservedCapabilities"].append(
                value["preservedCapabilities"][0]
            )
        ), False),
        case("reordered-preserved-capabilities", changed(
            reviewed, lambda value: value["preservedCapabilities"].reverse()
        ), False),
        case("wrong-delivery-strategy", changed(
            reviewed, lambda value: value["delivery"].update(
                {"strategy": "filename-deletion"}
            )
        ), False),
        case("unknown-delivery-field", changed(
            reviewed, lambda value: value["delivery"].update({"future": True})
        ), False),
        case("unknown-repacker-field", changed(
            reviewed, lambda value: value["delivery"]["repacker"].update(
                {"threads": 2}
            )
        ), False),
        case("unknown-target-field", changed(
            reviewed, lambda value: value["target"].update({"root": "/target-root"})
        ), False),
        case("wrong-architecture", changed(
            reviewed, lambda value: value["target"].update(
                {"architecture": "aarch64"}
            )
        ), False),
        case("malformed-profile-hash", changed(
            reviewed, lambda value: value.update({"sha256": "short"})
        ), False),
        case("non-string-profile-hash", changed(
            reviewed, lambda value: value.update({"sha256": 7})
        ), False),
        case("non-string-target-version", changed(
            reviewed, lambda value: value["target"].update({"steamosVersion": 3814})
        ), False),
        case("non-string-package-filename", changed(
            reviewed, lambda value: value["packageRecords"][0].update(
                {"filename": 7}
            )
        ), False),
        case("non-object-package-record", changed(
            reviewed, lambda value: value["packageRecords"].__setitem__(0, [])
        ), False),
        case("zero-saved-bytes", changed(
            reviewed, lambda value: value.update({"savedBytes": 0})
        ), False),
    ]
    cases.extend([
        {
            "name": "malformed-json",
            "expected": {"recordAccepted": False, "terminalBindingAccepted": False},
            "rawDocument": "{",
        },
        {
            "name": "duplicate-json-key",
            "expected": {"recordAccepted": False, "terminalBindingAccepted": False},
            "rawDocument": '{"schemaVersion":1,"schemaVersion":1}',
        },
        {
            "name": "non-finite-json",
            "expected": {"recordAccepted": False, "terminalBindingAccepted": False},
            "rawDocument": '{"schemaVersion":NaN}',
        },
        {
            "name": "oversized-document",
            "expected": {"recordAccepted": False, "terminalBindingAccepted": False},
            "documentRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-not-requested",
                "paddingBytes": MAX_DOCUMENT_BYTES,
            },
        },
    ])
    matrix = {
        "schemaVersion": 1,
        "kind": "opemos-installer-gaming-payload-compatibility-fixtures",
        "gamingPayloadSchemaVersion": 1,
        "validation": validation,
        "additivePolicy": "closed-security-critical-record",
        "unfrozenFields": [],
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES},
        "cases": cases,
    }
    payload = (json.dumps(matrix, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("Generated gaming-payload fixtures exceed their bound.")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
