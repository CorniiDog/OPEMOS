#!/usr/bin/env python3
"""Emit deterministic inactive userspace-lock bootstrap compatibility fixtures."""

import copy
import hashlib
import sys

from generate_userspace_lock_generation_fixtures import (
    canonical,
    documents,
    exact_target,
    refresh,
)
from userspace_lock_bootstrap_contract import (
    CHECKPOINT_KIND,
    MAX_CHECKPOINT_BYTES,
    MAX_POLICY_BYTES,
    POLICY_KIND,
)


MAX_OUTPUT_BYTES = 512 * 1024
KEYRING_PAYLOAD = b"inactive-test-keyring-v1\n"


def policy():
    return {
        "schemaVersion": 1,
        "kind": POLICY_KIND,
        "status": "active",
        "policyId": "opemos-userspace-lock-generations",
        "policySchemaVersion": 1,
        "authority": {
            "keyringFilename": "opemos-userspace-lock-generations.gpg",
            "keyringSha256": hashlib.sha256(KEYRING_PAYLOAD).hexdigest(),
            "primarySigningFingerprint": "A" * 40,
            "signatureScheme": "openpgp-detached-v1",
            "allowedHashAlgorithmIds": [8, 9, 10],
        },
        "channel": {
            "origin": "https://updates.example.invalid",
            "discoveryPath": (
                "/opemos/channels/reviewed/v1/"
                "opemos-userspace-lock-discovery-v1.json"
            ),
            "discoveryFilename": "opemos-userspace-lock-discovery-v1.json",
            "discoverySignatureFilename": (
                "opemos-userspace-lock-discovery-v1.json.sig"
            ),
            "immutableReleasePathPrefix": "/opemos/releases/generations/",
            "releaseTagPrefix": "opemos-userspace-lock-generation-v1-s",
            "allowRedirects": False,
        },
        "compatibility": {
            "discoverySchemaVersions": [1],
            "generationManifestSchemaVersions": [1],
            "userspaceLockSchemaVersions": [1],
            "installerResultSchemaVersions": [1],
        },
        "replayPolicy": {
            "requireMonotonicHighWater": True,
            "requireImmediatePredecessor": True,
            "allowAuthenticatedLineageCatchup": True,
            "maximumLineageGenerations": 64,
        },
    }


def generation_documents(selected_policy, sequence, predecessor):
    policy_payload = canonical(selected_policy)
    discovery, manifest = documents(sequence=sequence, predecessor=predecessor)
    authority = {
        "policyId": selected_policy["policyId"],
        "policySchemaVersion": selected_policy["policySchemaVersion"],
        "policySha256": hashlib.sha256(policy_payload).hexdigest(),
        "keyringFilename": selected_policy["authority"]["keyringFilename"],
        "keyringSha256": selected_policy["authority"]["keyringSha256"],
        "signingKeyFingerprint": selected_policy["authority"][
            "primarySigningFingerprint"
        ],
    }
    manifest["authority"] = copy.deepcopy(authority)
    refresh(discovery, manifest)
    return discovery, manifest


def base_documents(sequence=7, predecessor="1" * 64):
    selected_policy = policy()
    policy_payload = canonical(selected_policy)
    discovery, manifest = generation_documents(
        selected_policy, sequence, predecessor
    )
    checkpoint = {
        "schemaVersion": 1,
        "kind": CHECKPOINT_KIND,
        "policySha256": hashlib.sha256(policy_payload).hexdigest(),
        "minimumSequence": sequence,
        "minimumManifestSha256": discovery["generation"]["manifestSha256"],
    }
    return selected_policy, checkpoint, discovery, manifest


def document_case(name, values, policy_ok=True, checkpoint_ok=True,
                  activation_ok=True, state=None, lineage=None):
    selected_policy, checkpoint, discovery, manifest = copy.deepcopy(values)
    return {
        "name": name,
        "expected": {
            "policyAccepted": policy_ok,
            "checkpointAccepted": checkpoint_ok,
            "activationAccepted": activation_ok,
        },
        "policy": selected_policy,
        "checkpoint": checkpoint,
        "keyringPayload": KEYRING_PAYLOAD.decode("ascii"),
        "discovery": discovery,
        "manifest": manifest,
        "target": exact_target(),
        "state": state or {
            "highWaterSequence": 0,
            "activeSequence": None,
            "activeManifestSha256": None,
        },
        "lineage": lineage or [],
    }


def matrix():
    base = base_documents()
    cases = [document_case("valid-fresh-exact-checkpoint", base)]
    active_hash = base[2]["generation"]["manifestSha256"]
    forward_discovery, forward_manifest = generation_documents(
        base[0], 8, active_hash
    )
    forward = (base[0], base[1], forward_discovery, forward_manifest)
    cases.append(document_case(
        "valid-existing-forward", forward,
        state={
            "highWaterSequence": 7,
            "activeSequence": 7,
            "activeManifestSha256": active_hash,
        },
    ))
    forward_hash = forward_discovery["generation"]["manifestSha256"]
    catchup_discovery, catchup_manifest = generation_documents(
        base[0], 9, forward_hash
    )
    catchup = (base[0], base[1], catchup_discovery, catchup_manifest)
    cases.append(document_case(
        "valid-authenticated-lineage-catchup", catchup,
        state={
            "highWaterSequence": 7,
            "activeSequence": 7,
            "activeManifestSha256": active_hash,
        },
        lineage=[[forward_discovery, forward_manifest]],
    ))

    def policy_case(name, mutate):
        values = copy.deepcopy(base)
        mutate(values[0])
        cases.append(document_case(
            name, values, policy_ok=False, checkpoint_ok=False,
            activation_ok=False,
        ))

    policy_case("unknown-policy-field", lambda value: value.update(future=True))
    policy_case("unknown-authority-field", lambda value: value[
        "authority"
    ].update(future=True))
    policy_case("future-policy-schema", lambda value: value.update(schemaVersion=2))
    policy_case("weak-hash-policy", lambda value: value["authority"].update(
        allowedHashAlgorithmIds=[2, 8, 9, 10]
    ))
    policy_case("ambiguous-hash-policy", lambda value: value["authority"].update(
        allowedHashAlgorithmIds=[8, 8, 9, 10]
    ))
    policy_case("wrong-primary-fingerprint", lambda value: value["authority"].update(
        primarySigningFingerprint="a" * 40
    ))
    policy_case("wrong-signature-scheme", lambda value: value["authority"].update(
        signatureScheme="openpgp"
    ))
    policy_case("http-origin", lambda value: value["channel"].update(
        origin="http://updates.example.invalid"
    ))
    policy_case("origin-with-userinfo", lambda value: value["channel"].update(
        origin="https://user@updates.example.invalid"
    ))
    policy_case("origin-with-port", lambda value: value["channel"].update(
        origin="https://updates.example.invalid:443"
    ))
    policy_case("origin-with-query", lambda value: value["channel"].update(
        origin="https://updates.example.invalid?channel=reviewed"
    ))
    policy_case("invalid-origin-label", lambda value: value["channel"].update(
        origin="https://updates..example.invalid"
    ))
    policy_case("punycode-origin", lambda value: value["channel"].update(
        origin="https://xn--updates-9za.example.invalid"
    ))
    policy_case("unicode-origin", lambda value: value["channel"].update(
        origin="https://updatés.example.invalid"
    ))
    policy_case("ipv4-origin", lambda value: value["channel"].update(
        origin="https://192.0.2.1"
    ))
    policy_case("ipv6-origin", lambda value: value["channel"].update(
        origin="https://[2001:db8::1]"
    ))
    policy_case("trailing-dot-origin", lambda value: value["channel"].update(
        origin="https://updates.example.invalid."
    ))
    policy_case("single-label-origin", lambda value: value["channel"].update(
        origin="https://localhost"
    ))
    policy_case("numeric-top-level-origin", lambda value: value[
        "channel"
    ].update(origin="https://updates.example.123"))
    policy_case("uppercase-origin", lambda value: value["channel"].update(
        origin="https://Updates.example.invalid"
    ))
    policy_case("redirect-policy-enabled", lambda value: value["channel"].update(
        allowRedirects=True
    ))
    policy_case("mutable-discovery-ref", lambda value: value["channel"].update(
        discoveryPath=(
            "/opemos/latest/opemos-userspace-lock-discovery-v1.json"
        )
    ))
    policy_case("mutable-release-ref", lambda value: value["channel"].update(
        immutableReleasePathPrefix="/opemos/refs/heads/main/"
    ))
    policy_case("percent-encoded-path", lambda value: value["channel"].update(
        immutableReleasePathPrefix="/opemos/%72eleases/generations/"
    ))
    policy_case("portable-path-trailing-dot", lambda value: value[
        "channel"
    ].update(immutableReleasePathPrefix="/opemos/releases./generations/"))
    policy_case("portable-path-device-name", lambda value: value[
        "channel"
    ].update(immutableReleasePathPrefix="/opemos/con/generations/"))
    policy_case("wrong-discovery-name", lambda value: value["channel"].update(
        discoveryFilename="discovery.json"
    ))
    policy_case("wrong-discovery-signature-name", lambda value: value[
        "channel"
    ].update(discoverySignatureFilename=(
        "OPEMOS-userspace-lock-discovery-v1.json.sig"
    )))
    policy_case("future-discovery-schema", lambda value: value[
        "compatibility"
    ].update(discoverySchemaVersions=[1, 2]))
    policy_case("weakened-replay-policy", lambda value: value[
        "replayPolicy"
    ].update(requireMonotonicHighWater=False))

    wrong_checkpoint = copy.deepcopy(base)
    wrong_checkpoint[1]["policySha256"] = "f" * 64
    cases.append(document_case(
        "checkpoint-policy-mismatch", wrong_checkpoint,
        checkpoint_ok=False, activation_ok=False,
    ))
    future_checkpoint = copy.deepcopy(base)
    future_checkpoint[1]["schemaVersion"] = 2
    cases.append(document_case(
        "future-checkpoint-schema", future_checkpoint,
        checkpoint_ok=False, activation_ok=False,
    ))
    older = copy.deepcopy(base)
    older[1]["minimumSequence"] = 8
    older[1]["minimumManifestSha256"] = "8" * 64
    cases.append(document_case(
        "generation-older-than-checkpoint", older, activation_ok=False
    ))
    wrong_checkpoint_identity = copy.deepcopy(base)
    wrong_checkpoint_identity[1]["minimumManifestSha256"] = "f" * 64
    cases.append(document_case(
        "checkpoint-manifest-mismatch", wrong_checkpoint_identity,
        activation_ok=False,
    ))
    wrong_authority = copy.deepcopy(base)
    wrong_authority[2]["authority"]["keyringSha256"] = "f" * 64
    wrong_authority[3]["authority"]["keyringSha256"] = "f" * 64
    refresh(wrong_authority[2], wrong_authority[3])
    cases.append(document_case(
        "generation-authority-mismatch", wrong_authority, activation_ok=False
    ))
    signer_rotation = copy.deepcopy(base)
    signer_rotation[2]["authority"]["signingKeyFingerprint"] = "B" * 40
    signer_rotation[3]["authority"]["signingKeyFingerprint"] = "B" * 40
    refresh(signer_rotation[2], signer_rotation[3])
    cases.append(document_case(
        "generation-requests-signer-rotation", signer_rotation,
        activation_ok=False,
    ))
    wrong_compatibility = copy.deepcopy(base)
    wrong_compatibility[2]["compatibility"]["userspaceLockSchemaVersion"] = 2
    cases.append(document_case(
        "generation-future-lock-schema", wrong_compatibility,
        activation_ok=False,
    ))
    replay = copy.deepcopy(base)
    cases.append(document_case(
        "existing-state-replay", replay, activation_ok=False,
        state={
            "highWaterSequence": 7,
            "activeSequence": 7,
            "activeManifestSha256": replay[2]["generation"]["manifestSha256"],
        },
    ))
    downgrade_discovery, downgrade_manifest = generation_documents(
        base[0], 6, "0" * 64
    )
    downgrade = (base[0], base[1], downgrade_discovery, downgrade_manifest)
    cases.append(document_case(
        "existing-state-downgrade", downgrade, activation_ok=False,
        state={
            "highWaterSequence": 7,
            "activeSequence": 7,
            "activeManifestSha256": base[2]["generation"]["manifestSha256"],
        },
    ))
    wrong_keyring = copy.deepcopy(base)
    wrong_keyring_case = document_case(
        "keyring-payload-mismatch", wrong_keyring, activation_ok=False
    )
    wrong_keyring_case["keyringPayload"] = "different-test-keyring\n"
    cases.append(wrong_keyring_case)
    cases.extend([
        {
            "name": "duplicate-policy-key",
            "expected": {
                "policyAccepted": False,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "rawPolicy": '{"schemaVersion":1,"schemaVersion":1}\n',
        },
        {
            "name": "non-finite-checkpoint",
            "expected": {
                "policyAccepted": True,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "policy": base[0],
            "rawCheckpoint": '{"schemaVersion":NaN}\n',
        },
        {
            "name": "duplicate-checkpoint-key",
            "expected": {
                "policyAccepted": True,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "policy": base[0],
            "rawCheckpoint": '{"schemaVersion":1,"schemaVersion":1}\n',
        },
        {
            "name": "malformed-policy",
            "expected": {
                "policyAccepted": False,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "rawPolicy": '{"schemaVersion":1\n',
        },
        {
            "name": "oversized-policy",
            "expected": {
                "policyAccepted": False,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "documentRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-fresh-exact-checkpoint",
                "paddingBytes": MAX_POLICY_BYTES,
            },
        },
        {
            "name": "oversized-checkpoint",
            "expected": {
                "policyAccepted": True,
                "checkpointAccepted": False,
                "activationAccepted": False,
            },
            "policy": base[0],
            "checkpointRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-fresh-exact-checkpoint",
                "paddingBytes": MAX_CHECKPOINT_BYTES,
            },
        },
    ])
    return {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-bootstrap-compatibility-fixtures",
        "policySchemaVersion": 1,
        "checkpointSchemaVersion": 1,
        "limits": {
            "maxPolicyBytes": MAX_POLICY_BYTES,
            "maxCheckpointBytes": MAX_CHECKPOINT_BYTES,
            "maxCases": 64,
        },
        "cases": cases,
    }


def main():
    payload = canonical(matrix())
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("bootstrap compatibility fixture matrix is excessive")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
