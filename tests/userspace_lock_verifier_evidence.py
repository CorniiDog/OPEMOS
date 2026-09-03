#!/usr/bin/env python3
"""Compatibility tests for exact-snapshot verifier evidence."""

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from userspace_lock_generation_contract import canonical  # noqa: E402
from userspace_lock_verifier_evidence import (  # noqa: E402
    MAX_EVIDENCE_BYTES,
    VerifiedGenerationEvidence,
    VerifierEvidenceError,
    parse_evidence_record,
    validate_evidence_capability,
    verify_generation_snapshots,
)


GENERATOR = LIB / "generate_userspace_lock_verifier_evidence_fixtures.py"
SCHEMA = ROOT / "contracts/schemas/userspace-lock-verifier-evidence-v1.schema.json"


def generate():
    return subprocess.run(
        [sys.executable, str(GENERATOR)], cwd="/", check=True,
        stdout=subprocess.PIPE,
    ).stdout


def verifier_for(inputs, calls=None):
    verifier = inputs["verifier"]

    def verify(payload, signature, keyring, role):
        if calls is not None:
            calls.append((payload, signature, keyring, role))
        prefix = "discovery" if role == "discovery" else "manifest"
        return {
            "exitStatus": verifier[prefix + "ExitStatus"],
            "status": verifier[prefix + "Status"].encode(),
        }

    return verify


def capability(inputs, calls=None):
    return verify_generation_snapshots(
        canonical(inputs["policy"]), inputs["keyringPayload"].encode(),
        canonical(inputs["discovery"]), inputs["discoverySignature"].encode(),
        canonical(inputs["manifest"]), inputs["manifestSignature"].encode(),
        verifier_for(inputs, calls),
    )


def record_payload(case):
    if "rawRecord" in case:
        return case["rawRecord"].encode()
    recipe = case.get("rawRecordRecipe")
    if recipe is not None:
        assert recipe == {"text": " ", "count": MAX_EVIDENCE_BYTES + 1}
        return b" " * recipe["count"]
    return canonical(case["record"])


def main():
    first = generate()
    assert first == generate()
    matrix = json.loads(first)
    assert canonical(matrix) == first
    assert set(matrix) == {"schemaVersion", "kind", "limits", "cases"}
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-userspace-lock-verifier-evidence-compatibility"
    )
    assert matrix["limits"] == {
        "maxEvidenceBytes": MAX_EVIDENCE_BYTES,
        "maxCases": 48,
    }
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["document"]["additionalProperties"] is False
    assert schema["properties"]["documents"]["items"] is False
    assert len(schema["properties"]["documents"]["prefixItems"]) == 2
    assert schema["properties"]["verificationProfile"] == {
        "const": "openpgp-detached-validsig-v1"
    }

    cases = matrix["cases"]
    assert 20 <= len(cases) <= matrix["limits"]["maxCases"]
    names = []
    for case in cases:
        assert set(case) <= {
            "name", "expected", "inputs", "record", "rawRecord",
            "rawRecordRecipe",
        }
        names.append(case["name"])
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["name"])
        assert set(case["expected"]) == {
            "capabilityAccepted", "recordAccepted"
        }
        try:
            created = capability(case["inputs"])
            capability_ok = True
        except VerifierEvidenceError:
            created = None
            capability_ok = False
        assert capability_ok is case["expected"]["capabilityAccepted"], case[
            "name"
        ]
        if "record" in case or "rawRecord" in case or "rawRecordRecipe" in case:
            try:
                parse_evidence_record(record_payload(case))
                record_ok = True
            except VerifierEvidenceError:
                record_ok = False
            assert record_ok is case["expected"]["recordAccepted"], case["name"]
        if case["name"] == "valid-subkey-evidence":
            assert created is not None
            assert created.record() == case["record"]
    assert len(names) == len(set(names))

    valid = next(case for case in cases if case["name"] == "valid-subkey-evidence")
    inputs = valid["inputs"]
    calls = []
    created = capability(inputs, calls)
    assert [call[3] for call in calls] == ["discovery", "generation-manifest"]
    assert calls[0][:3] == (
        canonical(valid["inputs"]["discovery"]),
        valid["inputs"]["discoverySignature"].encode(),
        valid["inputs"]["keyringPayload"].encode(),
    )
    assert calls[1][:3] == (
        canonical(valid["inputs"]["manifest"]),
        valid["inputs"]["manifestSignature"].encode(),
        valid["inputs"]["keyringPayload"].encode(),
    )
    parsed = parse_evidence_record(canonical(created.record()))
    assert isinstance(parsed, dict)
    try:
        created._payload = b"forged\n"
    except AttributeError:
        pass
    else:
        raise AssertionError("verified capability was mutable")
    try:
        VerifiedGenerationEvidence(canonical(parsed), object())
    except TypeError:
        pass
    else:
        raise AssertionError("serialized evidence recreated a capability")
    try:
        validate_evidence_capability(
            created, canonical(inputs["policy"]),
            canonical(inputs["discovery"]) + b" ",
            inputs["discoverySignature"].encode(),
            canonical(inputs["manifest"]), inputs["manifestSignature"].encode(),
        )
    except VerifierEvidenceError:
        pass
    else:
        raise AssertionError("capability authorized different snapshot bytes")

    parse_calls = []
    try:
        verify_generation_snapshots(
            canonical(inputs["policy"]), inputs["keyringPayload"].encode(),
            b"{}\n", inputs["discoverySignature"].encode(),
            canonical(inputs["manifest"]), inputs["manifestSignature"].encode(),
            verifier_for(inputs, parse_calls),
        )
    except VerifierEvidenceError:
        assert [call[3] for call in parse_calls] == ["discovery"]
    else:
        raise AssertionError("malformed authenticated discovery was accepted")

    parse_calls = []
    try:
        verify_generation_snapshots(
            canonical(inputs["policy"]), inputs["keyringPayload"].encode(),
            canonical(inputs["discovery"]),
            inputs["discoverySignature"].encode(), b"{}\n",
            inputs["manifestSignature"].encode(),
            verifier_for(inputs, parse_calls),
        )
    except VerifierEvidenceError:
        assert [call[3] for call in parse_calls] == [
            "discovery", "generation-manifest"
        ]
    else:
        raise AssertionError("malformed authenticated manifest was accepted")

    def raising_verifier(*_arguments):
        raise RuntimeError("sensitive verifier failure")

    try:
        verify_generation_snapshots(
            canonical(inputs["policy"]), inputs["keyringPayload"].encode(),
            canonical(inputs["discovery"]),
            inputs["discoverySignature"].encode(),
            canonical(inputs["manifest"]), inputs["manifestSignature"].encode(),
            raising_verifier,
        )
    except VerifierEvidenceError as error:
        assert str(error) == "detached signature verifier failed"
        assert "sensitive" not in str(error)
    else:
        raise AssertionError("verifier exception created evidence")


if __name__ == "__main__":
    main()
