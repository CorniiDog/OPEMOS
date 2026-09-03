#!/usr/bin/env python3
"""Inactive trust/bootstrap contract for reviewed userspace-lock generations."""

import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

from userspace_lock_generation_contract import (
    DISCOVERY_FILENAME,
    DISCOVERY_SIGNATURE_FILENAME,
    DISCOVERY_SIGNATURE_SCHEME,
    MAX_LINEAGE_GENERATIONS,
    OPENPGP_HASH_ALGORITHM_IDS,
    GenerationContractError,
    canonical,
    strict_json,
    validate_activation,
    validate_pair,
)


MAX_POLICY_BYTES = 64 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
POLICY_KIND = "opemos-userspace-lock-bootstrap-policy"
CHECKPOINT_KIND = "opemos-userspace-lock-bootstrap-checkpoint"
POLICY_ID = "opemos-userspace-lock-generations"
KEYRING_FILENAME = "opemos-userspace-lock-generations.gpg"
RELEASE_TAG_PREFIX = "opemos-userspace-lock-generation-v1-s"
HASH = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
ORIGIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
URL_SEGMENT = re.compile(r"[a-z0-9._~-]{1,128}")
MUTABLE_SEGMENTS = {"head", "latest", "main", "master", "refs", "heads"}
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class BootstrapContractError(ValueError):
    """An inactive bootstrap policy or checkpoint is invalid."""


def fail(message):
    raise BootstrapContractError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def validate_origin(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        fail("channel origin is invalid")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        fail("channel origin is invalid")
    labels = [] if not parsed.hostname else parsed.hostname.split(".")
    try:
        ipaddress.ip_address(parsed.hostname or "")
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.hostname != parsed.hostname.lower()
            or len(parsed.hostname) > 253
            or is_ip_literal
            or len(labels) < 2
            or re.search(r"[a-z]", labels[-1]) is None
            or any(ORIGIN_LABEL.fullmatch(label) is None for label in labels)
            or any(label.startswith("xn--") for label in labels)
            or parsed.username is not None or parsed.password is not None
            or port is not None or parsed.path or parsed.query or parsed.fragment
            or value != f"https://{parsed.hostname}"):
        fail("channel origin must be a canonical HTTPS origin")
    return value


def validate_channel_path(value, label, filename=None, prefix=False):
    if (not isinstance(value, str) or not 2 <= len(value) <= 1024
            or not value.startswith("/") or "//" in value
            or (prefix and not value.endswith("/"))
            or (not prefix and value.endswith("/"))):
        fail(f"{label} is invalid")
    segments = value[1:-1].split("/") if prefix else value[1:].split("/")
    if (not segments or any(URL_SEGMENT.fullmatch(item) is None for item in segments)
            or any(item in {".", ".."} for item in segments)
            or any(item.startswith(".") or item.endswith(".") for item in segments)
            or any(item.split(".", 1)[0].lower() in WINDOWS_RESERVED_NAMES
                   for item in segments)
            or any(item.lower() in MUTABLE_SEGMENTS for item in segments)):
        fail(f"{label} is unsafe or mutable")
    if filename is not None and segments[-1] != filename:
        fail(f"{label} has a noncanonical asset name")
    return value


def validate_policy(policy):
    fields = {
        "schemaVersion", "kind", "status", "policyId", "policySchemaVersion",
        "authority", "channel", "compatibility", "replayPolicy",
    }
    authority_fields = {
        "keyringFilename", "keyringSha256", "primarySigningFingerprint",
        "signatureScheme", "allowedHashAlgorithmIds",
    }
    channel_fields = {
        "origin", "discoveryPath", "discoveryFilename",
        "discoverySignatureFilename", "immutableReleasePathPrefix",
        "releaseTagPrefix", "allowRedirects",
    }
    compatibility_fields = {
        "discoverySchemaVersions", "generationManifestSchemaVersions",
        "userspaceLockSchemaVersions", "installerResultSchemaVersions",
    }
    replay_fields = {
        "requireMonotonicHighWater", "requireImmediatePredecessor",
        "allowAuthenticatedLineageCatchup", "maximumLineageGenerations",
    }
    if (not isinstance(policy, dict) or set(policy) != fields
            or policy.get("schemaVersion") != 1
            or policy.get("kind") != POLICY_KIND
            or policy.get("status") != "active"
            or policy.get("policyId") != POLICY_ID
            or policy.get("policySchemaVersion") != 1):
        fail("bootstrap policy identity is unsupported")
    authority = policy.get("authority")
    if (not isinstance(authority, dict) or set(authority) != authority_fields
            or authority.get("keyringFilename") != KEYRING_FILENAME
            or HASH.fullmatch(authority.get("keyringSha256", "")) is None
            or FINGERPRINT.fullmatch(
                authority.get("primarySigningFingerprint", "")
            ) is None
            or authority.get("signatureScheme") != DISCOVERY_SIGNATURE_SCHEME
            or authority.get("allowedHashAlgorithmIds")
            != list(OPENPGP_HASH_ALGORITHM_IDS)):
        fail("bootstrap signing authority is unsupported")
    channel = policy.get("channel")
    if (not isinstance(channel, dict) or set(channel) != channel_fields
            or channel.get("discoveryFilename") != DISCOVERY_FILENAME
            or channel.get("discoverySignatureFilename")
            != DISCOVERY_SIGNATURE_FILENAME
            or channel.get("releaseTagPrefix") != RELEASE_TAG_PREFIX
            or channel.get("allowRedirects") is not False):
        fail("bootstrap channel identity is unsupported")
    validate_origin(channel["origin"])
    validate_channel_path(
        channel["discoveryPath"], "discovery path", DISCOVERY_FILENAME
    )
    validate_channel_path(
        channel["immutableReleasePathPrefix"], "immutable release namespace",
        prefix=True,
    )
    compatibility = policy.get("compatibility")
    if (not isinstance(compatibility, dict)
            or set(compatibility) != compatibility_fields
            or any(compatibility.get(field) != [1]
                   for field in compatibility_fields)):
        fail("bootstrap schema compatibility is unsupported")
    replay = policy.get("replayPolicy")
    if (not isinstance(replay, dict) or set(replay) != replay_fields
            or replay.get("requireMonotonicHighWater") is not True
            or replay.get("requireImmediatePredecessor") is not True
            or replay.get("allowAuthenticatedLineageCatchup") is not True
            or replay.get("maximumLineageGenerations")
            != MAX_LINEAGE_GENERATIONS):
        fail("bootstrap replay policy is unsupported")
    return policy


def parse_policy(payload):
    try:
        policy = strict_json(payload, MAX_POLICY_BYTES, "bootstrap policy")
    except GenerationContractError as error:
        fail(str(error))
    return validate_policy(policy)


def validate_checkpoint(checkpoint, policy_payload):
    fields = {
        "schemaVersion", "kind", "policySha256", "minimumSequence",
        "minimumManifestSha256",
    }
    if (not isinstance(policy_payload, bytes)
            or not 1 <= len(policy_payload) <= MAX_POLICY_BYTES):
        fail("bootstrap policy payload is invalid")
    parse_policy(policy_payload)
    if (not isinstance(checkpoint, dict) or set(checkpoint) != fields
            or checkpoint.get("schemaVersion") != 1
            or checkpoint.get("kind") != CHECKPOINT_KIND
            or checkpoint.get("policySha256") != sha256(policy_payload)
            or type(checkpoint.get("minimumSequence")) is not int
            or not 1 <= checkpoint["minimumSequence"] <= 2**64 - 1
            or HASH.fullmatch(checkpoint.get("minimumManifestSha256", ""))
            is None):
        fail("bootstrap checkpoint is invalid or belongs to another policy")
    return checkpoint


def parse_checkpoint(payload, policy_payload):
    try:
        checkpoint = strict_json(
            payload, MAX_CHECKPOINT_BYTES, "bootstrap checkpoint"
        )
    except GenerationContractError as error:
        fail(str(error))
    return validate_checkpoint(checkpoint, policy_payload)


def expected_generation_authority(policy, policy_payload):
    validate_policy(policy)
    if canonical(policy) != policy_payload:
        fail("bootstrap policy payload is not canonical or differs from policy")
    return {
        "policyId": policy["policyId"],
        "policySchemaVersion": policy["policySchemaVersion"],
        "policySha256": sha256(policy_payload),
        "keyringFilename": policy["authority"]["keyringFilename"],
        "keyringSha256": policy["authority"]["keyringSha256"],
        "signingKeyFingerprint": policy["authority"]["primarySigningFingerprint"],
    }


def validate_bootstrap_activation(policy, policy_payload, keyring_payload,
                                  checkpoint, discovery, manifest,
                                  expected_target, high_water_sequence=0,
                                  active_manifest_sha256=None,
                                  active_sequence=None, lineage=None):
    """Authorize already-signature-authenticated documents under bootstrap policy."""
    validate_policy(policy)
    if canonical(policy) != policy_payload:
        fail("bootstrap policy payload is not canonical or differs from policy")
    validate_checkpoint(checkpoint, policy_payload)
    if (not isinstance(keyring_payload, bytes)
            or not 1 <= len(keyring_payload) <= MAX_KEYRING_BYTES
            or sha256(keyring_payload) != policy["authority"]["keyringSha256"]):
        fail("bootstrap keyring identity differs from policy")
    try:
        validate_pair(discovery, manifest)
    except GenerationContractError as error:
        fail(str(error))
    compatibility = discovery["compatibility"]
    supported = policy["compatibility"]
    for singular, plural in (
            ("discoverySchemaVersion", "discoverySchemaVersions"),
            ("generationManifestSchemaVersion", "generationManifestSchemaVersions"),
            ("userspaceLockSchemaVersion", "userspaceLockSchemaVersions"),
            ("minimumInstallerResultSchemaVersion", "installerResultSchemaVersions")):
        if compatibility[singular] not in supported[plural]:
            fail("generation requires an unsupported schema version")
    expected_authority = expected_generation_authority(policy, policy_payload)
    bootstrap = {
        "sequence": checkpoint["minimumSequence"],
        "manifestSha256": checkpoint["minimumManifestSha256"],
    }
    try:
        return validate_activation(
            discovery, manifest, expected_authority, expected_target,
            high_water_sequence, active_manifest_sha256, lineage, bootstrap,
            active_sequence,
        )
    except GenerationContractError as error:
        fail(str(error))
