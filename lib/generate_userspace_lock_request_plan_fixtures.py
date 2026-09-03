#!/usr/bin/env python3
"""Emit deterministic immutable-generation request-plan fixtures."""

import copy
import hashlib
import json
import sys

from generate_userspace_lock_bootstrap_fixtures import base_documents
from generate_userspace_lock_generation_fixtures import refresh
from userspace_lock_generation_contract import canonical
from userspace_lock_request_plan import (
    MAX_PLAN_BYTES,
    MAX_REQUEST_METADATA_BYTES,
    MAX_REQUESTS,
    MAX_URL_BYTES,
    build_request_plan,
)


MAX_OUTPUT_BYTES = 1024 * 1024
DISCOVERY_SIGNATURE = b"test-discovery-signature-v1\n"
MANIFEST_SIGNATURE = b"test-manifest-signature-v1\n"


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def base_inputs():
    policy, _checkpoint, discovery, manifest = base_documents()
    payloads = {}
    for record in manifest["files"]:
        payload = ("authenticated:" + record["filename"] + "\n").encode()
        record["size"] = len(payload)
        record["sha256"] = digest(payload)
        payloads[record["filename"]] = payload
    files = {record["filename"]: record for record in manifest["files"]}
    for target_record in manifest["targetLocks"]:
        file_record = files[target_record["lock"]["filename"]]
        target_record["lock"]["size"] = file_record["size"]
        target_record["lock"]["sha256"] = file_record["sha256"]
    discovery["generation"]["signatureSize"] = len(MANIFEST_SIGNATURE)
    discovery["generation"]["signatureSha256"] = digest(MANIFEST_SIGNATURE)
    refresh(discovery, manifest)
    policy_payload = canonical(policy)
    discovery_payload = canonical(discovery)
    manifest_payload = canonical(manifest)
    authentication = {
        "schemaVersion": 1,
        "status": "authenticated",
        "policySha256": digest(policy_payload),
        "keyringSha256": policy["authority"]["keyringSha256"],
        "primarySigningFingerprint": policy["authority"][
            "primarySigningFingerprint"
        ],
        "discoveryPayloadSha256": digest(discovery_payload),
        "discoverySignatureSha256": digest(DISCOVERY_SIGNATURE),
        "discoveryHashAlgorithmId": 10,
        "manifestPayloadSha256": digest(manifest_payload),
        "manifestSignatureSha256": digest(MANIFEST_SIGNATURE),
        "manifestHashAlgorithmId": 8,
    }
    return {
        "policy": policy,
        "discovery": discovery,
        "discoverySignature": DISCOVERY_SIGNATURE.decode(),
        "manifest": manifest,
        "manifestSignature": MANIFEST_SIGNATURE.decode(),
        "authentication": authentication,
        "payloads": {
            name: payload.decode() for name, payload in payloads.items()
        },
    }


def arguments(inputs):
    return (
        canonical(inputs["policy"]),
        canonical(inputs["discovery"]),
        inputs["discoverySignature"].encode(),
        canonical(inputs["manifest"]),
        inputs["manifestSignature"].encode(),
        inputs["authentication"],
        {name: payload.encode() for name, payload in inputs["payloads"].items()},
    )


def fixture(name, inputs, inputs_accepted=True, plan=None,
            plan_accepted=None, raw_plan=None, raw_plan_recipe=None):
    if plan_accepted is None:
        plan_accepted = inputs_accepted
    value = {
        "name": name,
        "expected": {
            "inputsAccepted": inputs_accepted,
            "planAccepted": plan_accepted,
        },
        "inputs": inputs,
    }
    if raw_plan is not None:
        value["rawPlan"] = raw_plan
    elif raw_plan_recipe is not None:
        value["rawPlanRecipe"] = raw_plan_recipe
    elif plan is not None:
        value["plan"] = plan
    return value


def changed(value, callback):
    result = copy.deepcopy(value)
    callback(result)
    return result


def matrix():
    base = base_inputs()
    base_plan = build_request_plan(*arguments(base))
    cases = [fixture("valid-canonical-plan", base, plan=base_plan)]

    for name, field in (
            ("unauthenticated-policy", "policySha256"),
            ("unauthenticated-discovery", "discoveryPayloadSha256"),
            ("unauthenticated-discovery-signature", "discoverySignatureSha256"),
            ("unauthenticated-manifest", "manifestPayloadSha256"),
            ("unauthenticated-manifest-signature", "manifestSignatureSha256")):
        inputs = changed(base, lambda value, key=field: value[
            "authentication"
        ].__setitem__(key, "f" * 64))
        cases.append(fixture(name, inputs, inputs_accepted=False))
    cases.append(fixture(
        "unauthenticated-status",
        changed(base, lambda value: value["authentication"].update(
            status="unverified"
        )), inputs_accepted=False,
    ))
    cases.append(fixture(
        "missing-authentication-evidence",
        changed(base, lambda value: value.__setitem__("authentication", None)),
        inputs_accepted=False,
    ))
    cases.append(fixture(
        "unknown-authentication-field",
        changed(base, lambda value: value["authentication"].update(future=True)),
        inputs_accepted=False,
    ))
    cases.append(fixture(
        "weak-signature-hash",
        changed(base, lambda value: value["authentication"].update(
            manifestHashAlgorithmId=2
        )), inputs_accepted=False,
    ))
    cases.append(fixture(
        "missing-payload",
        changed(base, lambda value: value["payloads"].pop(
            sorted(value["payloads"])[0]
        )), inputs_accepted=False,
    ))
    cases.append(fixture(
        "unexpected-payload",
        changed(base, lambda value: value["payloads"].update(
            unexpected=b"unexpected\n".decode()
        )), inputs_accepted=False,
    ))
    cases.append(fixture(
        "payload-hash-mismatch",
        changed(base, lambda value: value["payloads"].__setitem__(
            sorted(value["payloads"])[0], "different\n"
        )), inputs_accepted=False,
    ))
    cases.append(fixture(
        "release-tag-sequence-mismatch",
        changed(base, lambda value: value["discovery"]["generation"].update(
            releaseTag="opemos-userspace-lock-generation-v1-s8"
        )), inputs_accepted=False,
    ))

    def plan_case(name, callback):
        plan = changed(base_plan, callback)
        cases.append(fixture(
            name, base, inputs_accepted=True, plan=plan, plan_accepted=False
        ))

    plan_case("redirects-enabled", lambda value: value.update(redirects=True))
    plan_case("cross-origin-substitution", lambda value: value[
        "requests"
    ][0].update(url="https://mirror.example.invalid/opemos/discovery.json"))
    plan_case("mutable-release-ref", lambda value: value["requests"][2].update(
        url=value["origin"] + "/opemos/releases/generations/latest/manifest.json"
    ))
    plan_case("path-traversal", lambda value: value["requests"][2].update(
        url=value["origin"] + "/opemos/releases/generations/../manifest.json"
    ))
    plan_case("path-field-substitution", lambda value: value[
        "requests"
    ][2].update(path="/opemos/releases/generations/other/manifest.json"))
    plan_case("percent-encoded-path", lambda value: value["requests"][2].update(
        url=value["requests"][2]["url"].replace("generation", "%67eneration", 1)
    ))
    plan_case("query-component", lambda value: value["requests"][2].update(
        url=value["requests"][2]["url"] + "?download=1"
    ))
    plan_case("fragment-component", lambda value: value["requests"][2].update(
        url=value["requests"][2]["url"] + "#asset"
    ))
    plan_case("excessive-url", lambda value: value["requests"][2].update(
        url="https://updates.example.invalid/" + "a" * MAX_URL_BYTES
    ))
    plan_case("missing-request", lambda value: (
        value["requests"].pop(), value.update(requestCount=value["requestCount"] - 1)
    ))
    plan_case("duplicate-request", lambda value: (
        value["requests"].append(copy.deepcopy(value["requests"][-1])),
        value.update(requestCount=value["requestCount"] + 1)
    ))
    plan_case("extra-request", lambda value: (
        value["requests"].append({
            "requestKind": "payload", "assetRole": "package",
            "filename": "extra.pkg.tar.zst",
            "path": "/opemos/releases/generations/extra.pkg.tar.zst",
            "url": value["origin"] + "/opemos/releases/generations/extra.pkg.tar.zst",
            "expectedSize": 1, "expectedSha256": "0" * 64,
        }), value.update(requestCount=value["requestCount"] + 1)
    ))
    plan_case("case-colliding-filename", lambda value: (
        value["requests"].append({
            **copy.deepcopy(value["requests"][-1]),
            "filename": value["requests"][-1]["filename"].upper(),
        }), value.update(requestCount=value["requestCount"] + 1)
    ))
    plan_case("wrong-release-tag", lambda value: value.update(
        releaseTag="opemos-userspace-lock-generation-v1-s8"
    ))
    plan_case("wrong-keyring-identity", lambda value: value.update(
        keyringSha256="0" * 64
    ))
    plan_case("wrong-signer-identity", lambda value: value.update(
        primarySigningFingerprint="B" * 40
    ))
    plan_case("wrong-signature-algorithm", lambda value: value.update(
        manifestHashAlgorithmId=9
    ))
    plan_case("wrong-sequence", lambda value: value.update(sequence=8))
    plan_case("wrong-request-count", lambda value: value.update(requestCount=99))
    plan_case("wrong-aggregate-size", lambda value: value.update(
        aggregateExpectedBytes=value["aggregateExpectedBytes"] + 1
    ))
    plan_case("wrong-aggregate-metadata", lambda value: value.update(
        aggregateMetadataBytes=value["aggregateMetadataBytes"] + 1
    ))
    plan_case("unknown-plan-field", lambda value: value.update(future=True))
    plan_case("unknown-request-field", lambda value: value[
        "requests"
    ][0].update(future=True))
    cases.append(fixture(
        "malformed-plan-json", base, inputs_accepted=True,
        plan_accepted=False, raw_plan="{",
    ))
    cases.append(fixture(
        "duplicate-plan-key", base, inputs_accepted=True,
        plan_accepted=False,
        raw_plan='{"schemaVersion":1,"schemaVersion":1}\n',
    ))
    cases.append(fixture(
        "non-finite-plan-number", base, inputs_accepted=True,
        plan_accepted=False, raw_plan='{"schemaVersion":NaN}\n',
    ))
    cases.append(fixture(
        "oversized-plan", base, inputs_accepted=True,
        plan_accepted=False,
        raw_plan_recipe={"text": " ", "count": MAX_PLAN_BYTES + 1},
    ))
    return {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-request-plan-compatibility",
        "limits": {
            "maxRequests": MAX_REQUESTS,
            "maxUrlBytes": MAX_URL_BYTES,
            "maxRequestMetadataBytes": MAX_REQUEST_METADATA_BYTES,
            "maxPlanBytes": MAX_PLAN_BYTES,
        },
        "cases": cases,
    }


def main():
    payload = canonical(matrix())
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("request-plan fixture matrix exceeds its output bound")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
