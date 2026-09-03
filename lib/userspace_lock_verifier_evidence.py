#!/usr/bin/env python3
"""Create non-serializable capabilities for exact OpenPGP-verified snapshots."""

import hashlib
import re

from userspace_lock_bootstrap_contract import (
    BootstrapContractError,
    KEYRING_FILENAME,
    MAX_KEYRING_BYTES,
    expected_generation_authority,
    parse_policy,
)
from userspace_lock_generation_contract import (
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_OPENPGP_STATUS_BYTES,
    MAX_SIGNATURE_BYTES,
    GenerationContractError,
    canonical,
    strict_json,
    validate_discovery,
    validate_openpgp_status,
    validate_pair,
)


EVIDENCE_KIND = "opemos-userspace-lock-verifier-evidence"
VERIFICATION_PROFILE = "openpgp-detached-validsig-v1"
MAX_EVIDENCE_BYTES = 64 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
EVIDENCE_FIELDS = {
    "schemaVersion", "kind", "status", "verificationProfile",
    "policySha256", "keyringFilename", "keyringSha256",
    "primarySigningFingerprint", "documents",
}
DOCUMENT_FIELDS = {
    "role", "payloadSha256", "payloadSize", "signatureSha256",
    "signatureSize", "signingFingerprint", "primarySigningFingerprint",
    "hashAlgorithmId",
}
_CAPABILITY_SEAL = object()


class VerifierEvidenceError(ValueError):
    """Exact snapshot verification could not create trusted evidence."""


def fail(message):
    raise VerifierEvidenceError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


class VerifiedGenerationEvidence:
    """Verifier-owned capability; serialized evidence cannot recreate it."""

    __slots__ = ("_payload", "_seal")

    def __init__(self, payload, seal):
        if seal is not _CAPABILITY_SEAL:
            raise TypeError("verified evidence capabilities are verifier-created")
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(self, "_seal", seal)

    def __setattr__(self, _name, _value):
        raise AttributeError("verified evidence capabilities are immutable")

    def record(self):
        return parse_evidence_record(self._payload)


def validate_evidence_record(record):
    if (not isinstance(record, dict) or set(record) != EVIDENCE_FIELDS
            or record.get("schemaVersion") != 1
            or record.get("kind") != EVIDENCE_KIND
            or record.get("status") != "authenticated"
            or record.get("verificationProfile") != VERIFICATION_PROFILE
            or SHA256.fullmatch(record.get("policySha256", "")) is None
            or record.get("keyringFilename") != KEYRING_FILENAME
            or SHA256.fullmatch(record.get("keyringSha256", "")) is None
            or FINGERPRINT.fullmatch(
                record.get("primarySigningFingerprint", "")
            ) is None):
        fail("verifier evidence identity is invalid")
    documents = record.get("documents")
    if not isinstance(documents, list) or len(documents) != 2:
        fail("verifier evidence document set is invalid")
    for index, role in enumerate(("discovery", "generation-manifest")):
        document = documents[index]
        maximum = DISCOVERY_MAX_BYTES if index == 0 else MANIFEST_MAX_BYTES
        if (not isinstance(document, dict) or set(document) != DOCUMENT_FIELDS
                or document.get("role") != role
                or SHA256.fullmatch(document.get("payloadSha256", "")) is None
                or type(document.get("payloadSize")) is not int
                or not 1 <= document["payloadSize"] <= maximum
                or SHA256.fullmatch(
                    document.get("signatureSha256", "")
                ) is None
                or type(document.get("signatureSize")) is not int
                or not 1 <= document["signatureSize"] <= MAX_SIGNATURE_BYTES
                or FINGERPRINT.fullmatch(
                    document.get("signingFingerprint", "")
                ) is None
                or document.get("primarySigningFingerprint")
                != record["primarySigningFingerprint"]
                or type(document.get("hashAlgorithmId")) is not int
                or document["hashAlgorithmId"] not in (8, 9, 10)):
            fail("verifier evidence document identity is invalid")
    return record


def parse_evidence_record(payload):
    """Parse audit evidence only; this deliberately returns no capability."""
    try:
        record = strict_json(payload, MAX_EVIDENCE_BYTES, "verifier evidence")
    except GenerationContractError as error:
        fail(str(error))
    return validate_evidence_record(record)


def verifier_result(callback, payload, signature, keyring, primary, role):
    try:
        result = callback(payload, signature, keyring, role)
    except Exception as error:
        raise VerifierEvidenceError("detached signature verifier failed") from error
    if (not isinstance(result, dict) or set(result) != {"exitStatus", "status"}
            or type(result.get("exitStatus")) is not int
            or result["exitStatus"] != 0
            or not isinstance(result.get("status"), bytes)
            or not 1 <= len(result["status"]) <= MAX_OPENPGP_STATUS_BYTES):
        fail("detached signature verifier did not report bounded success")
    try:
        return validate_openpgp_status(result["status"], primary)
    except GenerationContractError as error:
        fail(str(error))


def document_record(role, payload, signature, verified):
    return {
        "role": role,
        "payloadSha256": sha256(payload),
        "payloadSize": len(payload),
        "signatureSha256": sha256(signature),
        "signatureSize": len(signature),
        "signingFingerprint": verified["signingFingerprint"],
        "primarySigningFingerprint": verified["primaryFingerprint"],
        "hashAlgorithmId": verified["hashAlgorithmId"],
    }


def verify_generation_snapshots(policy_payload, keyring_payload,
                                discovery_payload, discovery_signature,
                                manifest_payload, manifest_signature,
                                verify_detached):
    """Invoke one verifier on exact snapshots and return a sealed capability."""
    try:
        policy = parse_policy(policy_payload)
        expected_authority = expected_generation_authority(policy, policy_payload)
    except BootstrapContractError as error:
        fail(str(error))
    authority = policy["authority"]
    if (not isinstance(keyring_payload, bytes)
            or not 1 <= len(keyring_payload) <= MAX_KEYRING_BYTES
            or sha256(keyring_payload) != authority["keyringSha256"]):
        fail("verifier inputs differ from installed bootstrap authority")
    if (not isinstance(discovery_payload, bytes)
            or not 1 <= len(discovery_payload) <= DISCOVERY_MAX_BYTES
            or not isinstance(manifest_payload, bytes)
            or not 1 <= len(manifest_payload) <= MANIFEST_MAX_BYTES
            or not isinstance(discovery_signature, bytes)
            or not 1 <= len(discovery_signature) <= MAX_SIGNATURE_BYTES
            or not isinstance(manifest_signature, bytes)
            or not 1 <= len(manifest_signature) <= MAX_SIGNATURE_BYTES):
        fail("document or detached signature snapshot is empty or excessive")
    primary = authority["primarySigningFingerprint"]
    discovery_verified = verifier_result(
        verify_detached, discovery_payload, discovery_signature,
        keyring_payload, primary, "discovery",
    )
    try:
        discovery = strict_json(
            discovery_payload, DISCOVERY_MAX_BYTES, "discovery descriptor"
        )
        validate_discovery(discovery)
    except GenerationContractError as error:
        fail(str(error))
    if discovery["authority"] != expected_authority:
        fail("verifier inputs differ from installed bootstrap authority")
    generation = discovery["generation"]
    if (generation["signatureSize"] != len(manifest_signature)
            or generation["signatureSha256"] != sha256(manifest_signature)):
        fail("manifest signature differs from authenticated discovery")
    manifest_verified = verifier_result(
        verify_detached, manifest_payload, manifest_signature,
        keyring_payload, primary, "generation-manifest",
    )
    try:
        manifest = strict_json(
            manifest_payload, MANIFEST_MAX_BYTES, "generation manifest"
        )
        validate_pair(discovery, manifest)
    except GenerationContractError as error:
        fail(str(error))
    allowed = authority["allowedHashAlgorithmIds"]
    if (discovery_verified["hashAlgorithmId"] not in allowed
            or manifest_verified["hashAlgorithmId"] not in allowed):
        fail("detached signature hash algorithm is not authorized")
    record = {
        "schemaVersion": 1,
        "kind": EVIDENCE_KIND,
        "status": "authenticated",
        "verificationProfile": VERIFICATION_PROFILE,
        "policySha256": sha256(policy_payload),
        "keyringFilename": authority["keyringFilename"],
        "keyringSha256": authority["keyringSha256"],
        "primarySigningFingerprint": primary,
        "documents": [
            document_record(
                "discovery", discovery_payload, discovery_signature,
                discovery_verified,
            ),
            document_record(
                "generation-manifest", manifest_payload, manifest_signature,
                manifest_verified,
            ),
        ],
    }
    payload = canonical(validate_evidence_record(record))
    if len(payload) > MAX_EVIDENCE_BYTES:
        fail("verifier evidence is excessive")
    return VerifiedGenerationEvidence(payload, _CAPABILITY_SEAL)


def validate_evidence_capability(evidence, policy_payload, discovery_payload,
                                 discovery_signature, manifest_payload,
                                 manifest_signature):
    if (not isinstance(evidence, VerifiedGenerationEvidence)
            or evidence._seal is not _CAPABILITY_SEAL):
        fail("planner requires verifier-created evidence capability")
    record = evidence.record()
    expected = (
        ("discovery", discovery_payload, discovery_signature),
        ("generation-manifest", manifest_payload, manifest_signature),
    )
    if record["policySha256"] != sha256(policy_payload):
        fail("verifier evidence belongs to another bootstrap policy")
    for stored, (role, payload, signature) in zip(record["documents"], expected):
        if (stored["role"] != role
                or stored["payloadSize"] != len(payload)
                or stored["payloadSha256"] != sha256(payload)
                or stored["signatureSize"] != len(signature)
                or stored["signatureSha256"] != sha256(signature)):
            fail("verifier evidence belongs to different snapshots")
    return record
