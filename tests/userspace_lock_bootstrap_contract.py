#!/usr/bin/env python3
"""Canonical inactive userspace-lock bootstrap policy compatibility tests."""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from userspace_lock_bootstrap_contract import (  # noqa: E402
    MAX_CHECKPOINT_BYTES,
    MAX_KEYRING_BYTES,
    MAX_POLICY_BYTES,
    BootstrapContractError,
    canonical,
    parse_checkpoint,
    parse_policy,
    validate_bootstrap_activation,
)


GENERATOR = LIB / "generate_userspace_lock_bootstrap_fixtures.py"


def main():
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(GENERATOR)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(outputs[0])
    assert set(matrix) == {
        "schemaVersion", "kind", "policySchemaVersion",
        "checkpointSchemaVersion", "limits", "cases",
    }
    assert matrix["schemaVersion"] == matrix["policySchemaVersion"] == 1
    assert matrix["checkpointSchemaVersion"] == 1
    assert matrix["limits"] == {
        "maxPolicyBytes": MAX_POLICY_BYTES,
        "maxCheckpointBytes": MAX_CHECKPOINT_BYTES,
        "maxCases": 64,
    }
    assert 1 <= len(matrix["cases"]) <= matrix["limits"]["maxCases"]
    by_name = {
        case["name"]: copy.deepcopy(case)
        for case in matrix["cases"] if "policy" in case
    }
    names = []
    for case in matrix["cases"]:
        names.append(case["name"])
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["name"])
        assert set(case["expected"]) == {
            "policyAccepted", "checkpointAccepted", "activationAccepted",
        }
        assert all(type(value) is bool for value in case["expected"].values())
        if "documentRecipe" in case:
            recipe = case["documentRecipe"]
            assert set(recipe) == {"kind", "baseCase", "paddingBytes"}
            assert recipe["kind"] == "top-level-padding"
            policy_document = copy.deepcopy(by_name[recipe["baseCase"]]["policy"])
            policy_document["padding"] = "x" * recipe["paddingBytes"]
            policy_payload = canonical(policy_document)
        elif "rawPolicy" in case:
            policy_payload = case["rawPolicy"].encode()
        else:
            policy_payload = canonical(case["policy"])
        try:
            selected_policy = parse_policy(policy_payload)
            policy_accepted = True
        except BootstrapContractError:
            selected_policy = None
            policy_accepted = False
        checkpoint_accepted = False
        checkpoint = None
        if policy_accepted and any(field in case for field in (
                "checkpoint", "rawCheckpoint", "checkpointRecipe")):
            if "rawCheckpoint" in case:
                checkpoint_payload = case["rawCheckpoint"].encode()
            elif "checkpointRecipe" in case:
                recipe = case["checkpointRecipe"]
                assert set(recipe) == {"kind", "baseCase", "paddingBytes"}
                assert recipe["kind"] == "top-level-padding"
                checkpoint_document = copy.deepcopy(
                    by_name[recipe["baseCase"]]["checkpoint"]
                )
                checkpoint_document["padding"] = "x" * recipe["paddingBytes"]
                checkpoint_payload = canonical(checkpoint_document)
            else:
                checkpoint_payload = canonical(case["checkpoint"])
            try:
                checkpoint = parse_checkpoint(checkpoint_payload, policy_payload)
                checkpoint_accepted = True
            except BootstrapContractError:
                pass
        activation_accepted = False
        if checkpoint_accepted and all(field in case for field in (
                "discovery", "manifest", "target", "state", "keyringPayload")):
            state = case["state"]
            lineage = [tuple(item) for item in case.get("lineage", [])]
            try:
                validate_bootstrap_activation(
                    selected_policy, policy_payload,
                    case["keyringPayload"].encode(), checkpoint,
                    case["discovery"], case["manifest"], case["target"],
                    state["highWaterSequence"],
                    state["activeManifestSha256"], state["activeSequence"],
                    lineage,
                )
                activation_accepted = True
            except BootstrapContractError:
                pass
        assert policy_accepted is case["expected"]["policyAccepted"], case["name"]
        assert checkpoint_accepted is case["expected"]["checkpointAccepted"], case["name"]
        assert activation_accepted is case["expected"]["activationAccepted"], case["name"]
    assert len(names) == len(set(names))
    assert {
        "valid-fresh-exact-checkpoint", "valid-existing-forward",
        "valid-authenticated-lineage-catchup", "weak-hash-policy",
        "generation-authority-mismatch", "mutable-discovery-ref",
        "mutable-release-ref", "redirect-policy-enabled",
        "punycode-origin", "unicode-origin", "ipv4-origin", "ipv6-origin",
        "single-label-origin", "numeric-top-level-origin", "uppercase-origin",
        "percent-encoded-path", "portable-path-trailing-dot",
        "portable-path-device-name", "wrong-discovery-signature-name",
        "generation-older-than-checkpoint", "checkpoint-manifest-mismatch",
        "generation-requests-signer-rotation",
        "existing-state-replay", "existing-state-downgrade",
        "keyring-payload-mismatch",
        "duplicate-policy-key", "duplicate-checkpoint-key",
        "oversized-policy", "oversized-checkpoint", "future-policy-schema",
    } <= set(names)

    policy_schema = json.loads((
        ROOT / "contracts/schemas/userspace-lock-bootstrap-policy-v1.schema.json"
    ).read_text(encoding="utf-8"))
    checkpoint_schema = json.loads((
        ROOT / "contracts/schemas/userspace-lock-bootstrap-checkpoint-v1.schema.json"
    ).read_text(encoding="utf-8"))
    assert policy_schema["additionalProperties"] is False
    assert policy_schema["properties"]["authority"]["additionalProperties"] is False
    assert policy_schema["properties"]["channel"]["additionalProperties"] is False
    assert checkpoint_schema["additionalProperties"] is False
    valid = by_name["valid-fresh-exact-checkpoint"]
    valid_policy_payload = canonical(valid["policy"])
    try:
        validate_bootstrap_activation(
            valid["policy"], valid_policy_payload,
            b"x" * (MAX_KEYRING_BYTES + 1), valid["checkpoint"],
            valid["discovery"], valid["manifest"], valid["target"],
        )
    except BootstrapContractError:
        pass
    else:
        raise AssertionError("oversized bootstrap keyring was accepted")


if __name__ == "__main__":
    main()
