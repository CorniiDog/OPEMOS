#!/usr/bin/env python3
"""Validate inactive schema-1 reviewed userspace-lock generation contracts."""

import datetime
import hashlib
import json
import os
import re
import stat
from pathlib import Path


DISCOVERY_MAX_BYTES = 256 * 1024
MANIFEST_MAX_BYTES = 2 * 1024 * 1024
MAX_TARGETS = 256
MAX_FILES = 4096
MAX_LOCK_BYTES = 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_GENERATION_BYTES = 8 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_SEQUENCE = 2**64 - 1
MAX_LINEAGE_GENERATIONS = 64
POLICY_ID = "opemos-userspace-lock-generations"
KIND_DISCOVERY = "opemos-userspace-lock-discovery"
KIND_MANIFEST = "opemos-userspace-lock-generation"
CHANNEL = "reviewed"
SHA256 = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
PLAIN_FILENAME = re.compile(r"[A-Za-z0-9@._+~-]{1,255}")
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")
KERNEL = re.compile(r"[A-Za-z0-9._+~-]{1,255}")
RELEASE_TAG = re.compile(r"opemos-userspace-lock-generation-v1-s([1-9][0-9]{0,19})")
FILE_ROLES = {
    "userspace-lock", "package", "package-signature", "keyring",
    "signer-policy", "target-policy", "gaming-profile", "provenance",
}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
AUTHORITY_FIELDS = {
    "policyId", "policySchemaVersion", "policySha256", "keyringFilename",
    "keyringSha256", "signingKeyFingerprint",
}
COMPATIBILITY_FIELDS = {
    "discoverySchemaVersion", "generationManifestSchemaVersion",
    "userspaceLockSchemaVersion", "minimumInstallerResultSchemaVersion",
}
TARGET_FIELDS = {
    "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture",
}
LOCK_FIELDS = {"filename", "schemaVersion", "sha256", "size"}
GENERATION_FIELDS = {
    "releaseTag", "manifestFilename", "manifestSha256", "manifestSize",
    "signatureFilename", "signatureSha256", "signatureSize",
    "previousManifestSha256",
}
DISCOVERY_FIELDS = {
    "schemaVersion", "kind", "channel", "sequence", "publishedAt",
    "authority", "compatibility", "generation", "targets",
}
MANIFEST_FIELDS = {
    "schemaVersion", "kind", "channel", "sequence", "publishedAt",
    "authority", "previousManifestSha256", "targetLocks", "files",
}
FILE_FIELDS = {"role", "filename", "size", "sha256"}


class GenerationContractError(ValueError):
    pass


def canonical(value):
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ) + "\n").encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GenerationContractError("document contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise GenerationContractError("document contains a non-finite number")


def strict_json(payload, maximum, label):
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum:
        raise GenerationContractError(f"{label} is empty or exceeds its size limit")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GenerationContractError(f"{label} is malformed") from error
    if canonical(value) != payload:
        raise GenerationContractError(f"{label} is not canonical JSON")
    return value


def read_bounded_regular(path, maximum, label):
    path = Path(path)
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise GenerationContractError(f"{label} is missing, unreadable, or unsafe") from error
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= maximum):
            raise GenerationContractError(f"{label} is not a bounded single-link file")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        )
        if identity(before) != identity(after) or len(payload) != before.st_size:
            raise GenerationContractError(f"{label} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def _plain_filename(value):
    if (not isinstance(value, str) or PLAIN_FILENAME.fullmatch(value) is None
            or Path(value).name != value or value in {".", ".."}
            or value.endswith(".")):
        return False
    stem = value.split(".", 1)[0].upper()
    return stem not in WINDOWS_RESERVED_NAMES


def _positive_integer(value, maximum):
    return type(value) is int and 1 <= value <= maximum


def _sha256(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        return False
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def validate_authority(authority):
    if (not isinstance(authority, dict) or set(authority) != AUTHORITY_FIELDS
            or authority.get("policyId") != POLICY_ID
            or authority.get("policySchemaVersion") != 1
            or not _sha256(authority.get("policySha256"))
            or not _plain_filename(authority.get("keyringFilename"))
            or not _sha256(authority.get("keyringSha256"))
            or not isinstance(authority.get("signingKeyFingerprint"), str)
            or FINGERPRINT.fullmatch(authority["signingKeyFingerprint"]) is None):
        raise GenerationContractError("generation authority is invalid")
    return authority


def validate_target(target):
    if (not isinstance(target, dict) or set(target) != TARGET_FIELDS
            or not isinstance(target.get("steamosVersion"), str)
            or VERSION.fullmatch(target["steamosVersion"]) is None
            or not isinstance(target.get("kernelVersion"), str)
            or KERNEL.fullmatch(target["kernelVersion"]) is None
            or not isinstance(target.get("nvidiaVersion"), str)
            or VERSION.fullmatch(target["nvidiaVersion"]) is None
            or target.get("architecture") != "x86_64"):
        raise GenerationContractError("generation target is invalid")
    return target


def target_identity(target):
    return tuple(target[field] for field in (
        "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture"
    ))


def validate_lock(lock):
    if (not isinstance(lock, dict) or set(lock) != LOCK_FIELDS
            or not _plain_filename(lock.get("filename"))
            or not lock["filename"].endswith(".json")
            or lock.get("schemaVersion") != 1
            or not _sha256(lock.get("sha256"))
            or not _positive_integer(lock.get("size"), MAX_LOCK_BYTES)):
        raise GenerationContractError("generation lock identity is invalid")
    return lock


def validate_target_records(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_TARGETS:
        raise GenerationContractError("generation target set is invalid")
    identities = []
    lock_names = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"target", "lock"}:
            raise GenerationContractError("generation target record is invalid")
        validate_target(record.get("target"))
        validate_lock(record.get("lock"))
        identities.append(target_identity(record["target"]))
        lock_names.append(record["lock"]["filename"])
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise GenerationContractError("generation targets are unsorted or duplicated")
    if len(lock_names) != len({name.casefold() for name in lock_names}):
        raise GenerationContractError("generation lock filenames are duplicated")
    return records


def validate_predecessor(sequence, predecessor):
    if sequence == 1:
        if predecessor is not None:
            raise GenerationContractError("first generation cannot name a predecessor")
    elif not _sha256(predecessor):
        raise GenerationContractError("generation predecessor is missing or invalid")


def validate_discovery(document):
    if (not isinstance(document, dict) or set(document) != DISCOVERY_FIELDS
            or document.get("schemaVersion") != 1
            or document.get("kind") != KIND_DISCOVERY
            or document.get("channel") != CHANNEL
            or not _positive_integer(document.get("sequence"), MAX_SEQUENCE)
            or not _timestamp(document.get("publishedAt"))):
        raise GenerationContractError("discovery descriptor identity is invalid")
    validate_authority(document.get("authority"))
    compatibility = document.get("compatibility")
    if (not isinstance(compatibility, dict)
            or set(compatibility) != COMPATIBILITY_FIELDS
            or any(compatibility.get(field) != 1 for field in COMPATIBILITY_FIELDS)):
        raise GenerationContractError("discovery compatibility is unsupported")
    generation = document.get("generation")
    if not isinstance(generation, dict) or set(generation) != GENERATION_FIELDS:
        raise GenerationContractError("discovery generation identity is invalid")
    sequence = document["sequence"]
    tag = generation.get("releaseTag")
    match = RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) else None
    if (match is None or int(match.group(1)) != sequence
            or generation.get("manifestFilename") != f"{tag}.manifest.json"
            or generation.get("signatureFilename") != f"{tag}.manifest.json.sig"
            or not _sha256(generation.get("manifestSha256"))
            or not _positive_integer(generation.get("manifestSize"), MANIFEST_MAX_BYTES)
            or not _sha256(generation.get("signatureSha256"))
            or not _positive_integer(
                generation.get("signatureSize"), MAX_SIGNATURE_BYTES
            )):
        raise GenerationContractError("discovery generation identity is invalid")
    validate_predecessor(sequence, generation.get("previousManifestSha256"))
    validate_target_records(document.get("targets"))
    return document


def validate_file_records(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FILES:
        raise GenerationContractError("generation file set is invalid")
    identities = []
    total = 0
    for record in records:
        if (not isinstance(record, dict) or set(record) != FILE_FIELDS
                or record.get("role") not in FILE_ROLES
                or not _plain_filename(record.get("filename"))
                or not _positive_integer(record.get("size"), MAX_FILE_BYTES)
                or not _sha256(record.get("sha256"))):
            raise GenerationContractError("generation file record is invalid")
        identities.append((record["role"], record["filename"]))
        total += record["size"]
    filenames = [record["filename"] for record in records]
    if (identities != sorted(identities)
            or len(filenames) != len({name.casefold() for name in filenames})
            or total > MAX_GENERATION_BYTES):
        raise GenerationContractError("generation files are unsorted, duplicated, or excessive")
    return records


def validate_manifest(document):
    if (not isinstance(document, dict) or set(document) != MANIFEST_FIELDS
            or document.get("schemaVersion") != 1
            or document.get("kind") != KIND_MANIFEST
            or document.get("channel") != CHANNEL
            or not _positive_integer(document.get("sequence"), MAX_SEQUENCE)
            or not _timestamp(document.get("publishedAt"))):
        raise GenerationContractError("generation manifest identity is invalid")
    validate_authority(document.get("authority"))
    validate_predecessor(document["sequence"], document.get("previousManifestSha256"))
    targets = validate_target_records(document.get("targetLocks"))
    files = validate_file_records(document.get("files"))
    by_name = {record["filename"]: record for record in files}
    for target in targets:
        lock = target["lock"]
        stored = by_name.get(lock["filename"])
        if (stored is None or stored["role"] != "userspace-lock"
                or stored["size"] != lock["size"]
                or stored["sha256"] != lock["sha256"]):
            raise GenerationContractError("target lock is absent from generation files")
    expected_locks = {
        (record["lock"]["filename"], record["lock"]["size"],
         record["lock"]["sha256"])
        for record in targets
    }
    actual_locks = {
        (record["filename"], record["size"], record["sha256"])
        for record in files if record["role"] == "userspace-lock"
    }
    if actual_locks != expected_locks:
        raise GenerationContractError("generation lock files differ from target locks")
    return document


def validate_pair(discovery, manifest):
    validate_discovery(discovery)
    validate_manifest(manifest)
    generation = discovery["generation"]
    reserved_generation_names = {
        generation["manifestFilename"].casefold(),
        generation["signatureFilename"].casefold(),
    }
    if any(
            record["filename"].casefold() in reserved_generation_names
            for record in manifest["files"]):
        raise GenerationContractError(
            "generation payload filename collides with its manifest"
        )
    if (manifest["sequence"] != discovery["sequence"]
            or manifest["publishedAt"] != discovery["publishedAt"]
            or manifest["authority"] != discovery["authority"]
            or manifest["previousManifestSha256"]
            != generation["previousManifestSha256"]
            or manifest["targetLocks"] != discovery["targets"]
            or len(canonical(manifest)) != generation["manifestSize"]
            or hashlib.sha256(canonical(manifest)).hexdigest()
            != generation["manifestSha256"]):
        raise GenerationContractError("discovery and generation manifest do not match")
    return discovery, manifest


def validate_activation(discovery, manifest, expected_authority, expected_target,
                        high_water_sequence, active_manifest_sha256,
                        intermediate_generations=None, bootstrap_checkpoint=None,
                        active_sequence=None):
    """Authorize already signature-authenticated generation documents."""
    validate_pair(discovery, manifest)
    lineage = [] if intermediate_generations is None else intermediate_generations
    if not isinstance(lineage, list) or len(lineage) > MAX_LINEAGE_GENERATIONS:
        raise GenerationContractError("generation lineage is invalid or excessive")
    if discovery["authority"] != expected_authority:
        raise GenerationContractError("generation authority differs from trust root")
    if active_sequence is None and active_manifest_sha256 is not None:
        active_sequence = high_water_sequence
    if (not type(high_water_sequence) is int
            or not 0 <= high_water_sequence <= MAX_SEQUENCE
            or active_manifest_sha256 is not None
            and not _sha256(active_manifest_sha256)
            or (high_water_sequence == 0) != (active_manifest_sha256 is None)
            or (active_sequence is None) != (active_manifest_sha256 is None)
            or active_sequence is not None
            and (not _positive_integer(active_sequence, MAX_SEQUENCE)
                 or active_sequence > high_water_sequence)):
        raise GenerationContractError("activation state is invalid")
    if discovery["sequence"] <= high_water_sequence:
        raise GenerationContractError("generation is a replay or downgrade")
    current_hash = hashlib.sha256(canonical(manifest)).hexdigest()
    if high_water_sequence == 0:
        if (not isinstance(bootstrap_checkpoint, dict)
                or set(bootstrap_checkpoint) != {"sequence", "manifestSha256"}
                or not _positive_integer(
                    bootstrap_checkpoint.get("sequence"), MAX_SEQUENCE
                )
                or not _sha256(bootstrap_checkpoint.get("manifestSha256"))):
            raise GenerationContractError("fresh bootstrap checkpoint is invalid")
        checkpoint_sequence = bootstrap_checkpoint["sequence"]
        checkpoint_hash = bootstrap_checkpoint["manifestSha256"]
        if discovery["sequence"] < checkpoint_sequence:
            raise GenerationContractError("fresh bootstrap is older than its checkpoint")
        if discovery["sequence"] == checkpoint_sequence:
            if lineage or current_hash != checkpoint_hash:
                raise GenerationContractError(
                    "fresh bootstrap differs from its exact checkpoint"
                )
            previous_sequence = None
            previous_hash = None
        else:
            previous_sequence = checkpoint_sequence
            previous_hash = checkpoint_hash
    else:
        previous_sequence = active_sequence
        previous_hash = active_manifest_sha256
    for generation in lineage:
        if (not isinstance(generation, (tuple, list)) or len(generation) != 2):
            raise GenerationContractError("generation lineage record is invalid")
        older_discovery, older_manifest = generation
        validate_pair(older_discovery, older_manifest)
        if older_discovery["authority"] != expected_authority:
            raise GenerationContractError("generation lineage authority differs")
        if (previous_sequence is None
                or older_discovery["sequence"] <= previous_sequence
                or older_discovery["sequence"] >= discovery["sequence"]
                or older_discovery["generation"]["previousManifestSha256"]
                != previous_hash):
            raise GenerationContractError("generation lineage is broken or unordered")
        previous_sequence = older_discovery["sequence"]
        previous_hash = hashlib.sha256(canonical(older_manifest)).hexdigest()
    predecessor = discovery["generation"]["previousManifestSha256"]
    if previous_hash is not None and predecessor != previous_hash:
        raise GenerationContractError("generation predecessor differs from lineage")
    validate_target(expected_target)
    wanted = target_identity(expected_target)
    if wanted not in [target_identity(item["target"]) for item in discovery["targets"]]:
        raise GenerationContractError("generation does not contain the exact target")
    return discovery, manifest


def load_discovery(path):
    return validate_discovery(strict_json(
        read_bounded_regular(path, DISCOVERY_MAX_BYTES, "discovery descriptor"),
        DISCOVERY_MAX_BYTES, "discovery descriptor",
    ))


def load_manifest(path):
    return validate_manifest(strict_json(
        read_bounded_regular(path, MANIFEST_MAX_BYTES, "generation manifest"),
        MANIFEST_MAX_BYTES, "generation manifest",
    ))
