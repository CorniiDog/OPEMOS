#!/usr/bin/env python3
"""Deterministically plan immutable userspace-lock generation requests."""

import hashlib
import re
from urllib.parse import urlsplit

from userspace_lock_bootstrap_contract import (
    BootstrapContractError,
    expected_generation_authority,
    parse_policy,
)
from userspace_lock_generation_contract import (
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_FILES,
    MAX_GENERATION_STORAGE_BYTES,
    MAX_SIGNATURE_BYTES,
    GenerationContractError,
    canonical,
    strict_json,
    validate_pair,
)


MAX_REQUESTS = MAX_FILES + 4
MAX_URL_BYTES = 2048
MAX_REQUEST_METADATA_BYTES = 16 * 1024 * 1024
MAX_PLAN_BYTES = 32 * 1024 * 1024
PLAN_KIND = "opemos-userspace-lock-generation-request-plan"
AUTHENTICATION_FIELDS = {
    "schemaVersion", "status", "policySha256", "keyringSha256",
    "primarySigningFingerprint", "discoveryPayloadSha256",
    "discoverySignatureSha256", "discoveryHashAlgorithmId",
    "manifestPayloadSha256", "manifestSignatureSha256",
    "manifestHashAlgorithmId",
}
PLAN_FIELDS = {
    "schemaVersion", "kind", "policySha256", "keyringSha256",
    "primarySigningFingerprint", "discoveryHashAlgorithmId",
    "manifestHashAlgorithmId", "sequence", "releaseTag", "origin",
    "redirects", "requestCount", "aggregateExpectedBytes",
    "aggregateMetadataBytes", "requests",
}
REQUEST_FIELDS = {
    "requestKind", "assetRole", "filename", "path", "url", "expectedSize",
    "expectedSha256",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")


class RequestPlanError(ValueError):
    """Authenticated generation inputs cannot form one canonical plan."""


def fail(message):
    raise RequestPlanError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def parse_generation(payload, maximum, label):
    try:
        return strict_json(payload, maximum, label)
    except GenerationContractError as error:
        fail(str(error))


def validate_authentication(authentication, policy, policy_payload,
                            discovery_payload, discovery_signature,
                            manifest_payload, manifest_signature):
    authority = policy["authority"]
    if (not isinstance(authentication, dict)
            or set(authentication) != AUTHENTICATION_FIELDS
            or authentication.get("schemaVersion") != 1
            or authentication.get("status") != "authenticated"
            or authentication.get("policySha256") != sha256(policy_payload)
            or authentication.get("keyringSha256") != authority["keyringSha256"]
            or authentication.get("primarySigningFingerprint")
            != authority["primarySigningFingerprint"]
            or authentication.get("discoveryPayloadSha256")
            != sha256(discovery_payload)
            or authentication.get("discoverySignatureSha256")
            != sha256(discovery_signature)
            or authentication.get("manifestPayloadSha256")
            != sha256(manifest_payload)
            or authentication.get("manifestSignatureSha256")
            != sha256(manifest_signature)
            or type(authentication.get("discoveryHashAlgorithmId")) is not int
            or authentication.get("discoveryHashAlgorithmId")
            not in authority["allowedHashAlgorithmIds"]
            or type(authentication.get("manifestHashAlgorithmId")) is not int
            or authentication.get("manifestHashAlgorithmId")
            not in authority["allowedHashAlgorithmIds"]):
        fail("request inputs lack exact authenticated-document evidence")
    if any(SHA256.fullmatch(authentication[field]) is None for field in (
            "policySha256", "keyringSha256", "discoveryPayloadSha256",
            "discoverySignatureSha256", "manifestPayloadSha256",
            "manifestSignatureSha256")):
        fail("request authentication hashes are malformed")
    if FINGERPRINT.fullmatch(
            authentication["primarySigningFingerprint"]) is None:
        fail("request authentication signer is malformed")
    return authentication


def request_record(request_kind, asset_role, filename, url, payload):
    try:
        url_size = len(url.encode("ascii"))
    except (AttributeError, UnicodeError):
        fail("request URL is not canonical ASCII")
    parsed = urlsplit(url)
    if (not isinstance(payload, bytes) or not payload
            or url_size > MAX_URL_BYTES or parsed.scheme != "https"
            or parsed.query or parsed.fragment or parsed.username is not None
            or parsed.password is not None):
        fail("request asset or URL is empty or excessive")
    return {
        "requestKind": request_kind,
        "assetRole": asset_role,
        "filename": filename,
        "path": parsed.path,
        "url": url,
        "expectedSize": len(payload),
        "expectedSha256": sha256(payload),
    }


def build_request_plan(policy_payload, discovery_payload, discovery_signature,
                       manifest_payload, manifest_signature, authentication,
                       payloads):
    try:
        policy = parse_policy(policy_payload)
    except BootstrapContractError as error:
        fail(str(error))
    discovery = parse_generation(
        discovery_payload, DISCOVERY_MAX_BYTES, "authenticated discovery"
    )
    manifest = parse_generation(
        manifest_payload, MANIFEST_MAX_BYTES, "authenticated generation manifest"
    )
    try:
        validate_pair(discovery, manifest)
        expected_authority = expected_generation_authority(policy, policy_payload)
    except (GenerationContractError, BootstrapContractError) as error:
        fail(str(error))
    if discovery["authority"] != expected_authority:
        fail("authenticated discovery authority differs from bootstrap policy")
    compatibility = discovery["compatibility"]
    supported = policy["compatibility"]
    for singular, plural in (
            ("discoverySchemaVersion", "discoverySchemaVersions"),
            ("generationManifestSchemaVersion", "generationManifestSchemaVersions"),
            ("userspaceLockSchemaVersion", "userspaceLockSchemaVersions"),
            ("minimumInstallerResultSchemaVersion", "installerResultSchemaVersions")):
        if compatibility[singular] not in supported[plural]:
            fail("authenticated discovery requires an unsupported schema")
    if (not isinstance(discovery_signature, bytes)
            or not 1 <= len(discovery_signature) <= MAX_SIGNATURE_BYTES
            or not isinstance(manifest_signature, bytes)
            or not 1 <= len(manifest_signature) <= MAX_SIGNATURE_BYTES):
        fail("authenticated detached signature is empty or excessive")
    channel = policy["channel"]
    generation = discovery["generation"]
    expected_tag = channel["releaseTagPrefix"] + str(discovery["sequence"])
    if generation["releaseTag"] != expected_tag:
        fail("generation release tag differs from authenticated sequence")
    if (generation["signatureSize"] != len(manifest_signature)
            or generation["signatureSha256"] != sha256(manifest_signature)):
        fail("manifest signature differs from authenticated discovery")
    validate_authentication(
        authentication, policy, policy_payload, discovery_payload,
        discovery_signature, manifest_payload, manifest_signature,
    )
    if not isinstance(payloads, dict) or set(payloads) != {
            record["filename"] for record in manifest["files"]}:
        fail("generation payload set differs from authenticated manifest")
    for filename, payload in payloads.items():
        if not isinstance(filename, str) or not isinstance(payload, bytes):
            fail("generation payload input is invalid")

    origin = channel["origin"]
    discovery_url = origin + channel["discoveryPath"]
    discovery_parent = channel["discoveryPath"].rsplit("/", 1)[0]
    discovery_signature_url = (
        origin + discovery_parent + "/" + channel["discoverySignatureFilename"]
    )
    release_root = (
        origin + channel["immutableReleasePathPrefix"]
        + generation["releaseTag"] + "/"
    )
    requests = [
        request_record(
            "metadata", "discovery", channel["discoveryFilename"],
            discovery_url, discovery_payload,
        ),
        request_record(
            "metadata", "discovery-signature",
            channel["discoverySignatureFilename"], discovery_signature_url,
            discovery_signature,
        ),
        request_record(
            "metadata", "generation-manifest", generation["manifestFilename"],
            release_root + generation["manifestFilename"], manifest_payload,
        ),
        request_record(
            "metadata", "generation-manifest-signature",
            generation["signatureFilename"],
            release_root + generation["signatureFilename"], manifest_signature,
        ),
    ]
    by_name = {record["filename"]: record for record in manifest["files"]}
    for record in manifest["files"]:
        payload = payloads[record["filename"]]
        if len(payload) != record["size"] or sha256(payload) != record["sha256"]:
            fail("generation payload differs from authenticated manifest")
        requests.append(request_record(
            "payload", record["role"], record["filename"],
            release_root + record["filename"], payload,
        ))
    if len(by_name) != len(manifest["files"]):
        fail("generation payload names are ambiguous")
    if not 5 <= len(requests) <= MAX_REQUESTS:
        fail("generation request count is invalid or excessive")
    aggregate_expected = sum(record["expectedSize"] for record in requests)
    aggregate_metadata = sum(
        len(record["filename"].encode("ascii"))
        + len(record["path"].encode("ascii"))
        + len(record["url"].encode("ascii")) for record in requests
    )
    if (aggregate_expected > MAX_GENERATION_STORAGE_BYTES
            or aggregate_metadata > MAX_REQUEST_METADATA_BYTES):
        fail("generation request plan is excessive")
    plan = {
        "schemaVersion": 1,
        "kind": PLAN_KIND,
        "policySha256": sha256(policy_payload),
        "keyringSha256": authentication["keyringSha256"],
        "primarySigningFingerprint": authentication[
            "primarySigningFingerprint"
        ],
        "discoveryHashAlgorithmId": authentication[
            "discoveryHashAlgorithmId"
        ],
        "manifestHashAlgorithmId": authentication["manifestHashAlgorithmId"],
        "sequence": discovery["sequence"],
        "releaseTag": generation["releaseTag"],
        "origin": origin,
        "redirects": False,
        "requestCount": len(requests),
        "aggregateExpectedBytes": aggregate_expected,
        "aggregateMetadataBytes": aggregate_metadata,
        "requests": requests,
    }
    if len(canonical(plan)) > MAX_PLAN_BYTES:
        fail("generation request plan document is excessive")
    return plan


def validate_request_plan(plan, *inputs):
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS:
        fail("generation request plan structure is invalid")
    expected = build_request_plan(*inputs)
    if plan != expected:
        fail("generation request plan differs from authenticated inputs")
    for request in plan["requests"]:
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
            fail("generation request record is invalid")
    return plan


def parse_request_plan(payload, *inputs):
    plan = parse_generation(payload, MAX_PLAN_BYTES, "generation request plan")
    return validate_request_plan(plan, *inputs)
