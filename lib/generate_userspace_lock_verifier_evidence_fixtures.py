#!/usr/bin/env python3
"""Emit deterministic exact-snapshot verifier-evidence compatibility fixtures."""

import copy
import sys

from generate_openpgp_status_fixtures import valid as valid_openpgp_status
from generate_userspace_lock_request_plan_fixtures import base_inputs
from userspace_lock_generation_contract import MAX_OPENPGP_STATUS_BYTES, canonical
from userspace_lock_verifier_evidence import (
    MAX_EVIDENCE_BYTES,
    verify_generation_snapshots,
)


MAX_OUTPUT_BYTES = 1024 * 1024


def changed(value, callback):
    result = copy.deepcopy(value)
    callback(result)
    return result


def fixture(name, inputs, capability=True, record=None, record_accepted=None,
            raw_record=None, raw_recipe=None):
    if record_accepted is None:
        record_accepted = capability
    value = {
        "name": name,
        "expected": {
            "capabilityAccepted": capability,
            "recordAccepted": record_accepted,
        },
        "inputs": inputs,
    }
    if raw_record is not None:
        value["rawRecord"] = raw_record
    elif raw_recipe is not None:
        value["rawRecordRecipe"] = raw_recipe
    elif record is not None:
        value["record"] = record
    return value


def matrix():
    base = base_inputs()
    record = base["evidenceRecord"]
    cases = [fixture("valid-subkey-evidence", base, record=record)]
    direct = changed(base, lambda value: value["verifier"].update(
        discoveryStatus=valid_openpgp_status(
            9, signing="A" * 40, primary="A" * 40
        ),
        manifestStatus=valid_openpgp_status(10),
    ))

    def direct_verifier(_payload, _signature, _keyring, role):
        prefix = "discovery" if role == "discovery" else "manifest"
        return {
            "exitStatus": direct["verifier"][prefix + "ExitStatus"],
            "status": direct["verifier"][prefix + "Status"].encode(),
        }

    direct_evidence = verify_generation_snapshots(
        canonical(direct["policy"]), direct["keyringPayload"].encode(),
        canonical(direct["discovery"]), direct["discoverySignature"].encode(),
        canonical(direct["manifest"]), direct["manifestSignature"].encode(),
        direct_verifier,
    ).record()
    cases.append(fixture(
        "valid-primary-and-subkey", direct, record=direct_evidence
    ))

    def capability_case(name, callback):
        inputs = changed(base, callback)
        cases.append(fixture(
            name, inputs, capability=False, record=record, record_accepted=True
        ))

    capability_case("wrong-keyring", lambda value: value.__setitem__(
        "keyringPayload", "wrong-keyring\n"
    ))
    capability_case("discovery-verifier-nonzero", lambda value: value[
        "verifier"
    ].update(discoveryExitStatus=1))
    capability_case("manifest-verifier-nonzero", lambda value: value[
        "verifier"
    ].update(manifestExitStatus=1))
    capability_case("weak-hash", lambda value: value["verifier"].update(
        discoveryStatus=valid_openpgp_status(2)
    ))
    capability_case("wrong-primary", lambda value: value["verifier"].update(
        manifestStatus=valid_openpgp_status(8, primary="C" * 40)
    ))
    capability_case("multiple-signatures", lambda value: value[
        "verifier"
    ].update(manifestStatus=valid_openpgp_status(8) + valid_openpgp_status(9)))
    capability_case("malformed-status", lambda value: value["verifier"].update(
        discoveryStatus="diagnostic\n"
    ))
    capability_case("excessive-status", lambda value: value["verifier"].update(
        discoveryStatus=valid_openpgp_status(8) + "x" * MAX_OPENPGP_STATUS_BYTES
    ))
    capability_case("empty-signature", lambda value: value.__setitem__(
        "discoverySignature", ""
    ))
    capability_case(
        "manifest-signature-snapshot-mismatch",
        lambda value: value.__setitem__(
            "manifestSignature", "changed-signature\n"
        ),
    )
    capability_case("generation-authority-mismatch", lambda value: value[
        "discovery"
    ]["authority"].update(keyringSha256="0" * 64))

    def record_case(name, callback, accepted=False):
        candidate = changed(record, callback)
        cases.append(fixture(
            name, base, capability=True, record=candidate,
            record_accepted=accepted,
        ))

    record_case("unknown-evidence-field", lambda value: value.update(future=True))
    record_case("missing-evidence-field", lambda value: value.pop("policySha256"))
    record_case("wrong-verification-profile", lambda value: value.update(
        verificationProfile="openpgp-detached-v2"
    ))
    record_case("reordered-documents", lambda value: value[
        "documents"
    ].reverse())
    record_case("duplicate-document", lambda value: value["documents"].__setitem__(
        1, copy.deepcopy(value["documents"][0])
    ))
    record_case("missing-document", lambda value: value["documents"].pop())
    record_case("extra-document", lambda value: value["documents"].append(
        copy.deepcopy(value["documents"][0])
    ))
    record_case(
        "structural-document-hash-change",
        lambda value: value["documents"][0].update(payloadSha256="0" * 64),
        accepted=True,
    )
    record_case("zero-document-size", lambda value: value["documents"][0].update(
        payloadSize=0
    ))
    record_case("wrong-document-primary", lambda value: value[
        "documents"
    ][1].update(primarySigningFingerprint="C" * 40))
    record_case("unknown-document-field", lambda value: value[
        "documents"
    ][0].update(future=True))
    cases.append(fixture(
        "malformed-json", base, capability=True, record_accepted=False,
        raw_record="{",
    ))
    cases.append(fixture(
        "duplicate-json-key", base, capability=True, record_accepted=False,
        raw_record='{"schemaVersion":1,"schemaVersion":1}\n',
    ))
    cases.append(fixture(
        "non-finite-json", base, capability=True, record_accepted=False,
        raw_record='{"schemaVersion":NaN}\n',
    ))
    cases.append(fixture(
        "oversized-record", base, capability=True, record_accepted=False,
        raw_recipe={"text": " ", "count": MAX_EVIDENCE_BYTES + 1},
    ))
    return {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-verifier-evidence-compatibility",
        "limits": {
            "maxEvidenceBytes": MAX_EVIDENCE_BYTES,
            "maxCases": 48,
        },
        "cases": cases,
    }


def main():
    payload = canonical(matrix())
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("verifier-evidence fixture matrix exceeds its bound")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
