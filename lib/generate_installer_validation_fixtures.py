#!/usr/bin/env python3
"""Emit the canonical bounded installer-validation schema-1 fixture matrix."""

import copy
import json
import sys

from generate_installer_result_fixtures import validation_proof


MAX_OUTPUT_BYTES = 512 * 1024


def case(name, document, accepted):
    return {
        "name": name,
        "expected": {"accepted": accepted},
        "document": document,
    }


def changed(document, update):
    result = copy.deepcopy(document)
    update(result)
    return result


def matrix():
    direct = validation_proof()
    authenticated = changed(direct, lambda value: value.update(inputSource={
        "mode": "authenticated-bundle", "bundleCacheId": "e" * 64,
    }))
    additive = copy.deepcopy(direct)
    additive["futureAdditiveProof"] = {"bounded": True}
    additive["inputSource"]["futureTransportIdentity"] = "ignored-by-schema-1"
    additive["storage"]["futureReserveBytes"] = 0
    additive["compression"]["futureAdmissionDetail"] = False
    cases = [
        case("valid-direct-input", direct, True),
        case("valid-authenticated-bundle-input", authenticated, True),
        case("safe-additive-fields", additive, True),
        case("missing-input-source", changed(
            direct, lambda value: value.pop("inputSource")
        ), False),
        case("missing-archive-identity", changed(
            direct, lambda value: value.pop("archiveSha256")
        ), False),
        case("missing-boot-policy", changed(
            direct, lambda value: value.pop("boot")
        ), False),
        case("missing-storage", changed(
            direct, lambda value: value.pop("storage")
        ), False),
        case("input-source-identity-mismatch", changed(
            direct, lambda value: value["inputSource"].update(bundleCacheId="f" * 64)
        ), False),
        case("invalid-archive-hash", changed(
            direct, lambda value: value.update(archiveSha256="not-a-hash")
        ), False),
        case("unsafe-lock-filename", changed(
            direct, lambda value: value["userspaceLock"].update(name="../lock.json")
        ), False),
        case("boot-policy-mismatch", changed(
            direct,
            lambda value: value["boot"]["requiredKernelArguments"].pop(),
        ), False),
        case("dependency-version-mismatch", changed(
            direct,
            lambda value: value["packageDependencyClosure"][0].update(version="9-9"),
        ), False),
        case("duplicate-package-identity", changed(
            direct, lambda value: value["packages"].append(
                copy.deepcopy(value["packages"][0])
            )
        ), False),
        case("compression-storage-mismatch", changed(
            direct,
            lambda value: value["compression"].update(declaredPackageBytes=1),
        ), False),
        case("root-metadata-reserve-mismatch", changed(
            direct,
            lambda value: value["storage"].update(
                rootRequiredBytes=value["storage"]["rootRequiredBytes"] - 1
            ),
        ), False),
        case("var-reserve-mismatch", changed(
            direct,
            lambda value: value["storage"].update(varRequiredBytes=16_000_000),
        ), False),
        {
            "name": "dependency-closure-limit",
            "expected": {"accepted": False},
            "documentRecipe": {
                "kind": "extend-dependency-closure",
                "baseCase": "valid-direct-input",
                "additionalRecords": 4091,
            },
        },
        {
            "name": "malformed-json",
            "expected": {"accepted": False},
            "rawDocument": '{"archiveSha256":"',
        },
        {
            "name": "duplicate-json-key",
            "expected": {"accepted": False},
            "rawDocument": '{"archiveSha256":"' + "a" * 64
            + '","archiveSha256":"' + "a" * 64 + '"}',
        },
        {
            "name": "non-finite-json",
            "expected": {"accepted": False},
            "rawDocument": '{"future":NaN}',
        },
    ]
    return {
        "schemaVersion": 1,
        "kind": "opemos-installer-validation-compatibility-fixtures",
        "validationSchemaVersion": 1,
        "unfrozenFields": ["message"],
        "cases": cases,
    }


def main():
    payload = (json.dumps(matrix(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("installer-validation compatibility matrix exceeds its size limit")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
