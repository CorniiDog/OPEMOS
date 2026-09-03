#!/usr/bin/env python3
"""Emit the canonical userspace-verification schema-1 compatibility matrix."""

import copy
import json
import sys

from generate_installer_result_fixtures import NVIDIA, userspace_verification, validation_proof


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024


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
    valid = userspace_verification()
    cases = [case("valid-normalized-success", valid, True)]
    additive = copy.deepcopy(valid)
    additive["producerRevision"] = 2
    cases.append(case("safe-additive-top-level", additive, True))
    cases.append(case("valid-failure-diagnostic", {
        "schemaVersion": 1,
        "status": "failed",
        "reason": "installed_userspace_mismatch",
        "message": "Human-readable fixture wording is intentionally not frozen.",
        "packageMismatches": [{
            "packageName": "nvidia-utils",
            "invalidFields": ["payloadHash", "payloadMode", "payloadOwnership"],
            "affectedEntries": ["usr/lib/libnvidia-example.so.575.64.05"],
        }],
    }, True, False))

    def remove_package(document):
        document["packages"].pop()
        document["pacmanDatabase"]["verifiedPackageCount"] -= 1

    def add_package(document):
        package = copy.deepcopy(document["packages"][0])
        package["packageName"] = "unexpected-package"
        package["packageFilename"] = "unexpected-package-1-1-x86_64.pkg.tar.zst"
        package["packageSha256"] = "f" * 64
        document["packages"].append(package)
        document["pacmanDatabase"]["verifiedPackageCount"] += 1

    cases.extend([
        case("missing-package", changed(valid, remove_package), True, False),
        case("extra-package", changed(valid, add_package), True, False),
        case("duplicate-package", changed(
            valid, lambda d: d["packages"].__setitem__(1, copy.deepcopy(d["packages"][0]))
        ), False),
        case("lock-binding-mismatch", changed(
            valid, lambda d: d["validationBinding"].__setitem__(
                "userspaceLockSha256", "f" * 64
            )
        ), True, False),
        case("provenance-binding-mismatch", changed(
            valid, lambda d: d["validationBinding"].__setitem__(
                "provenanceSha256", "f" * 64
            )
        ), True, False),
        case("filename-mismatch", changed(
            valid, lambda d: d["packages"][0].__setitem__(
                "packageFilename", "different-1-1-x86_64.pkg.tar.zst"
            )
        ), True, False),
        case("version-mismatch", changed(
            valid, lambda d: d["packages"][0].__setitem__("version", "9.9.9-1")
        ), True, False),
        case("package-hash-mismatch", changed(
            valid, lambda d: d["packages"][0].__setitem__("packageSha256", "f" * 64)
        ), True, False),
        case("dependencies-mismatch", changed(
            valid, lambda d: d["packages"][0]["dependencies"].append("unexpected")
        ), True, False),
        case("provides-mismatch", changed(
            valid, lambda d: d["packages"][0]["provides"].append("unexpected-provider")
        ), True, False),
        case("query-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("packageQueryVerified", False)
        ), False),
        case("pacman-integrity-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("pacmanIntegrityVerified", False)
        ), False),
        case("payload-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadVerified", False)
        ), False),
        case("database-not-consistent", changed(
            valid, lambda d: d["pacmanDatabase"].__setitem__("consistencyVerified", False)
        ), False),
        case("database-count-mismatch", changed(
            valid, lambda d: d["pacmanDatabase"].__setitem__("verifiedPackageCount", 2)
        ), False),
        case("database-path-mismatch", changed(
            valid, lambda d: d["pacmanDatabase"].__setitem__("path", "/var/lib/pacman")
        ), False),
        case("payload-path-unconfined", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadPathsConfined", False)
        ), False),
        case("payload-hash-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadHashesVerified", False)
        ), False),
        case("payload-mode-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadModesVerified", False)
        ), False),
        case("payload-ownership-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadOwnershipVerified", False)
        ), False),
        case("payload-link-not-verified", changed(
            valid, lambda d: d["packages"][0].__setitem__("payloadLinksVerified", False)
        ), False),
        case("duplicate-dependency-relation", changed(
            valid, lambda d: d["packages"][0]["dependencies"].append(
                d["packages"][0]["dependencies"][0]
            )
        ), False),
        case("duplicate-provider-relation", changed(
            valid, lambda d: d["packages"][0]["provides"].append(
                d["packages"][0]["provides"][0]
            )
        ), False),
        case("reordered-relations", changed(
            valid, lambda d: d["packages"][0].__setitem__(
                "dependencies", list(reversed(d["packages"][0]["dependencies"]))
            )
        ), True, True),
        case("unsafe-package-filename", changed(
            valid, lambda d: d["packages"][0].__setitem__(
                "packageFilename", "../package.pkg.tar.zst"
            )
        ), False),
        case("oversized-relations", changed(
            valid, lambda d: d["packages"][0].__setitem__(
                "dependencies", [f"dependency-{index}" for index in range(65)]
            )
        ), False),
        case("firmware-version-mismatch", changed(
            valid,
            lambda d: (
                d["gspFirmware"].__setitem__("version", "999.1.1"),
                d["gspFirmware"].__setitem__(
                    "targetRelativeFiles", ["usr/lib/firmware/nvidia/999.1.1/gsp.bin"]
                ),
            ),
        ), True, False),
        case("firmware-path-escape", changed(
            valid, lambda d: d["gspFirmware"].__setitem__(
                "targetRelativeFiles", ["usr/lib/firmware/nvidia/575.64.05/../gsp.bin"]
            )
        ), False),
        case("missing-firmware", changed(
            valid, lambda d: d["gspFirmware"].__setitem__("targetRelativeFiles", [])
        ), False),
        case("duplicate-firmware-path", changed(
            valid, lambda d: d["gspFirmware"].__setitem__(
                "targetRelativeFiles", [
                    "usr/lib/firmware/nvidia/575.64.05/gsp.bin",
                    "usr/lib/firmware/nvidia/575.64.05/gsp.bin",
                ]
            )
        ), False),
        case("non-gsp-firmware-name", changed(
            valid, lambda d: d["gspFirmware"].__setitem__(
                "targetRelativeFiles", ["usr/lib/firmware/nvidia/575.64.05/firmware.bin"]
            )
        ), False),
        case("zero-payload-entries", changed(
            valid, lambda d: [d["packages"][0].__setitem__(field, 0) for field in (
                "directories", "regularFiles", "symlinks", "hardlinks", "sharedLibraries"
            )]
        ), False),
        case("shared-library-count-inconsistent", changed(
            valid, lambda d: d["packages"][0].__setitem__("sharedLibraries", 4)
        ), False),
        case("unknown-package-field", changed(
            valid, lambda d: d["packages"][0].__setitem__("hostPath", "/secret")
        ), False),
    ])
    cases.extend([
        {"name": "malformed-json", "expected": {
            "recordAccepted": False, "successProofAccepted": False,
        }, "rawDocument": '{"schemaVersion":1'},
        {"name": "duplicate-json-key", "expected": {
            "recordAccepted": False, "successProofAccepted": False,
        }, "rawDocument": '{"schemaVersion":1,"schemaVersion":1}'},
        {"name": "non-finite-json", "expected": {
            "recordAccepted": False, "successProofAccepted": False,
        }, "rawDocument": '{"schemaVersion":1,"status":NaN}'},
        {"name": "oversized-document", "expected": {
            "recordAccepted": False, "successProofAccepted": False,
        }, "documentRecipe": {
            "kind": "top-level-padding", "baseCase": "valid-normalized-success",
            "paddingBytes": MAX_DOCUMENT_BYTES,
        }},
    ])
    return {
        "schemaVersion": 1,
        "kind": "opemos-installer-userspace-verification-compatibility-fixtures",
        "userspaceVerificationSchemaVersion": 1,
        "targetNvidiaVersion": NVIDIA,
        "validation": validation_proof(),
        "unfrozenFields": ["message"],
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES},
        "cases": cases,
    }


def main():
    payload = json.dumps(matrix(), sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise SystemExit("generated userspace-verification matrix exceeds its output limit")
    sys.stdout.write(payload)


if __name__ == "__main__":
    main()
