#!/usr/bin/env python3
"""Emit deterministic device-generation result and health compatibility fixtures."""

import copy
import json
import sys

from device_generation_contract import MAX_DOCUMENT_BYTES, canonical


HASH_7 = "7" * 64
HASH_8 = "8" * 64


def identity(sequence=8, digest=HASH_8):
    return {"sequence": sequence, "manifestSha256": digest}


def state(active=None, last_good=None, high_water=0, pending=False):
    return {
        "schemaVersion": 1,
        "channel": "reviewed-userspace-lock-generations",
        "active": active,
        "lastKnownGood": last_good,
        "highWaterSequence": high_water,
        "healthPending": pending,
    }


def result(value_state):
    return {
        "schemaVersion": 1,
        "channel": "reviewed-userspace-lock-generations",
        "status": "ok",
        "reason": "status",
        "state": value_state,
    }


def health(generation=None):
    return {
        "schemaVersion": 1,
        "kind": "opemos-device-generation-health",
        "status": "healthy",
        "generation": generation or identity(),
        "checks": ["generation-integrity", "recovery-ready"],
    }


def changed(value, callback):
    output = copy.deepcopy(value)
    callback(output)
    return output


def document_case(name, kind, document, accepted):
    return {
        "name": name, "kind": kind,
        "expected": {"accepted": accepted}, "document": document,
    }


def raw_case(name, kind, raw):
    return {
        "name": name, "kind": kind,
        "expected": {"accepted": False}, "rawDocument": raw,
    }


def matrix():
    empty = state()
    pending = state(identity(), identity(7, HASH_7), 8, True)
    healthy = state(identity(), identity(), 9, False)
    rollback = state(identity(7, HASH_7), identity(7, HASH_7), 9, False)
    base_result = result(healthy)
    base_health = health()
    cases = [
        document_case("valid-empty-state", "result", result(empty), True),
        document_case("valid-pending-state", "result", result(pending), True),
        document_case("valid-healthy-state", "result", base_result, True),
        document_case("valid-rollback-high-water", "result", result(rollback), True),
        document_case("valid-bounded-failure", "result", {
            "schemaVersion": 1,
            "channel": "reviewed-userspace-lock-generations",
            "status": "failed", "reason": "device_generation_busy",
            "message": "A bounded human diagnostic.",
        }, True),
        document_case("active-missing-with-high-water", "result", result(
            state(None, None, 8, False)
        ), False),
        document_case("active-exceeds-high-water", "result", result(
            state(identity(9), identity(9), 8, False)
        ), False),
        document_case("lkg-exceeds-high-water", "result", result(
            state(identity(8), identity(9), 8, True)
        ), False),
        document_case("pending-equals-lkg", "result", result(
            state(identity(), identity(), 8, True)
        ), False),
        document_case("healthy-differs-from-lkg", "result", result(
            state(identity(), identity(7, HASH_7), 8, False)
        ), False),
        document_case("boolean-sequence", "result", changed(
            base_result, lambda value: value["state"]["active"].update(sequence=True)
        ), False),
        document_case("invalid-manifest-hash", "result", changed(
            base_result,
            lambda value: value["state"]["active"].update(manifestSha256="A" * 64),
        ), False),
        document_case("unknown-result-field", "result", changed(
            base_result, lambda value: value.update(future=True)
        ), False),
        document_case("invalid-result-reason", "result", changed(
            base_result, lambda value: value.update(reason="Invalid reason")
        ), False),
        document_case("valid-health-bound", "health", base_health, True),
        document_case("health-wrong-active-binding", "health", health(
            identity(7, HASH_7)
        ), False),
        document_case("health-reversed-checks", "health", changed(
            base_health, lambda value: value["checks"].reverse()
        ), False),
        document_case("health-missing-check", "health", changed(
            base_health, lambda value: value.update(checks=["generation-integrity"])
        ), False),
        document_case("unknown-health-field", "health", changed(
            base_health, lambda value: value.update(future=True)
        ), False),
        raw_case(
            "duplicate-json-key", "result",
            '{"schemaVersion":1,"schemaVersion":1}\n',
        ),
        raw_case("non-finite-json", "result", '{"schemaVersion":NaN}\n'),
        raw_case("malformed-json", "health", '{"schemaVersion":1\n'),
        {
            "name": "oversized-document", "kind": "result",
            "expected": {"accepted": False},
            "documentRecipe": {
                "kind": "top-level-padding", "baseCase": "valid-healthy-state",
                "paddingBytes": MAX_DOCUMENT_BYTES,
            },
        },
    ]
    return {
        "schemaVersion": 1,
        "kind": "opemos-device-generation-compatibility-fixtures",
        "resultSchemaVersion": 1,
        "healthSchemaVersion": 1,
        "activeIdentity": identity(),
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES, "maxCases": 64},
        "cases": cases,
    }


def main():
    payload = canonical(matrix())
    if len(payload) > 512 * 1024:
        raise SystemExit("device-generation compatibility matrix is excessive")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
