#!/usr/bin/env python3
"""Prepare exact installer userspace inputs from an authenticated EXE handoff.

This command is intentionally development/test-only until Core has an installed
production generation authority and checkpoint.  It reauthenticates the
generation inside the appliance; the host handoff receipt is transport
integrity, never trust evidence.
"""

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from userspace_lock_bootstrap_contract import (
    expected_generation_authority,
    parse_checkpoint,
    parse_policy,
)
from userspace_lock_generation_contract import (
    DISCOVERY_FILENAME,
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_GENERATION_STORAGE_BYTES,
    MAX_OPENPGP_STATUS_BYTES,
    MAX_SIGNATURE_BYTES,
    canonical,
    strict_json,
    validate_activation,
    validate_openpgp_status,
    validate_pair,
)
from userspace_lock_verifier_evidence import (
    MAX_EVIDENCE_BYTES,
    parse_evidence_record,
)


HANDOFF_FILENAME = "opemos-core-generation-handoff-v1.json"
HANDOFF_KIND = "opemos-core-appliance-generation-handoff"
EVIDENCE_FILENAME = "opemos-userspace-lock-verifier-evidence-v1.json"
HANDOFF_MAX_BYTES = 512 * 1024
RESULT_MAX_BYTES = 512 * 1024
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
PORTABLE = re.compile(r"[A-Za-z0-9@._+~-]{1,255}")
PACKAGE_NAME = re.compile(r"[A-Za-z0-9@._+:-]{1,255}")
PACKAGE_FIELDS = {
    "name", "filename", "signatureFilename", "version", "architecture",
    "packageSha256", "signatureSha256", "signerFingerprint",
    "installedSize", "dependencies", "provides",
}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ConsumerError(ValueError):
    pass


class Cancelled(Exception):
    pass


def fail(message):
    raise ConsumerError(message)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail("JSON contains a duplicate key")
        value[key] = item
    return value


def reject_constant(_value):
    fail("JSON contains a non-finite number")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def read_regular(path, maximum, label, expected_owner=None):
    try:
        before = path.lstat()
    except OSError:
        fail(f"{label} is unavailable")
    owner = os.geteuid() if expected_owner is None else expected_owner
    if (path.is_symlink() or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1 or before.st_uid != owner
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= maximum):
        fail(f"{label} is unsafe or excessive")
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            payload = source.read(maximum + 1)
            after = os.fstat(source.fileno())
    except OSError:
        fail(f"{label} is unreadable")
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns, value.st_uid, stat.S_IMODE(value.st_mode),
    )
    if (identity(before) != identity(opened) or identity(opened) != identity(after)
            or len(payload) != before.st_size or len(payload) > maximum):
        fail(f"{label} changed while read")
    return payload


def parse_json(payload, maximum, label, canonical_required=False):
    if not 1 <= len(payload) <= maximum:
        fail(f"{label} is empty or excessive")
    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError):
        fail(f"{label} is malformed")
    if canonical_required and payload != canonical(document):
        fail(f"{label} is not canonical JSON")
    return document


def safe_portable_name(value):
    if (not isinstance(value, str) or PORTABLE.fullmatch(value) is None
            or value in {".", ".."} or value.endswith(".")):
        return False
    return value.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES


def safe_package_name(value):
    return (isinstance(value, str) and PACKAGE_NAME.fullmatch(value) is not None
            and value not in {".", ".."} and not value.endswith("."))


def validate_handoff(document, expected_operation, expected_target):
    if not isinstance(document, dict) or set(document) != {
            "schemaVersion", "kind", "operationId", "identity", "target",
            "lineageManifestSha256", "files",
            }:
        fail("appliance handoff fields are not canonical")
    identity = document["identity"]
    if (document["schemaVersion"] != 1 or document["kind"] != HANDOFF_KIND
            or OPERATION.fullmatch(document.get("operationId", "")) is None
            or (expected_operation is not None
                and document["operationId"] != expected_operation)
            or not isinstance(identity, dict) or set(identity) != {
                "sequence", "generationId", "manifestSha256"
            }
            or type(identity["sequence"]) is not int
            or not 1 <= identity["sequence"] <= 2**64 - 1
            or HASH.fullmatch(identity.get("generationId", "")) is None
            or HASH.fullmatch(identity.get("manifestSha256", "")) is None
            or identity["generationId"] != identity["manifestSha256"]
            or document["target"] != expected_target):
        fail("appliance handoff identity or target is invalid")
    lineage = document["lineageManifestSha256"]
    if (not isinstance(lineage, list) or len(lineage) > 64
            or any(HASH.fullmatch(item or "") is None for item in lineage)
            or len(lineage) != len(set(lineage))):
        fail("appliance handoff lineage is invalid")
    files = document["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES + 5:
        fail("appliance handoff file set is invalid")
    previous = ""
    records = {}
    total_size = 0
    for record in files:
        if (not isinstance(record, dict)
                or set(record) != {"filename", "size", "sha256"}
                or not safe_portable_name(record.get("filename"))
                or record["filename"] <= previous
                or record["filename"].lower() in {
                    name.lower() for name in records
                }
                or type(record.get("size")) is not int
                or not 1 <= record["size"] <= MAX_FILE_BYTES
                or HASH.fullmatch(record.get("sha256", "")) is None):
            fail("appliance handoff file record is invalid")
        previous = record["filename"]
        records[record["filename"]] = record
        total_size += record["size"]
        if total_size > MAX_GENERATION_STORAGE_BYTES:
            fail("appliance handoff aggregate size is excessive")
    return identity, records


def hash_regular(path, maximum, label):
    try:
        before = path.lstat()
    except OSError:
        fail(f"{label} is unavailable")
    if (path.is_symlink() or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1 or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= maximum):
        fail(f"{label} is unsafe or excessive")
    value = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    fail(f"{label} is unsafe or excessive")
                value.update(chunk)
            after = os.fstat(source.fileno())
    except OSError:
        fail(f"{label} is unreadable")
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns, item.st_uid, stat.S_IMODE(item.st_mode),
    )
    if (identity(before) != identity(opened) or identity(opened) != identity(after)
            or size != before.st_size):
        fail(f"{label} changed while read")
    return size, value.hexdigest()


def verify_inventory(root, records):
    try:
        names = sorted(item.name for item in root.iterdir())
    except OSError:
        fail("appliance handoff is unreadable")
    expected = sorted([*records, HANDOFF_FILENAME])
    if names != expected:
        fail("appliance handoff inventory is not exact")
    for name, record in records.items():
        size, payload_hash = hash_regular(
            root / name, record["size"], "handoff file"
        )
        if size != record["size"] or payload_hash != record["sha256"]:
            fail("appliance handoff file differs from its receipt")


def read_received_payload(root, records, name, maximum, label):
    record = records.get(name)
    if record is None or record["size"] > maximum:
        fail(f"{label} is absent or excessive")
    payload = read_regular(root / name, maximum, label)
    if len(payload) != record["size"] or digest(payload) != record["sha256"]:
        fail(f"{label} differs from its receipt")
    return payload


def verify_signature(gpgv, keyring, payload, signature, fingerprint, label):
    with tempfile.TemporaryDirectory(prefix="opemos-guest-generation-trust-") as name:
        root = Path(name)
        document = root / "document.json"
        detached = root / "document.json.sig"
        ring = root / "keyring.gpg"
        document.write_bytes(payload)
        detached.write_bytes(signature)
        ring.write_bytes(keyring)
        for path in (document, detached, ring):
            path.chmod(0o400)
        process = None
        selector = selectors.DefaultSelector()
        status_payload = bytearray()
        try:
            process = subprocess.Popen(
                [str(gpgv), "--status-fd", "1", "--keyring", str(ring),
                 str(detached), str(document)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 60
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(str(gpgv), 60)
                for key, _mask in selector.select(min(remaining, 1.0)):
                    chunk = os.read(key.fileobj.fileno(), 16 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    status_payload.extend(chunk)
                    if len(status_payload) > MAX_OPENPGP_STATUS_BYTES:
                        raise ConsumerError(f"{label} signature status is excessive")
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except Cancelled:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            raise
        except (OSError, subprocess.TimeoutExpired, ConsumerError):
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            fail(f"{label} verification could not complete safely")
        finally:
            selector.close()
    if returncode != 0:
        fail(f"{label} signature is invalid")
    try:
        validate_openpgp_status(bytes(status_payload), fingerprint)
    except ValueError as error:
        fail(f"{label} signature status is invalid: {error}")


def validate_lock(document, target):
    if (not isinstance(document, dict)
            or document.get("schemaVersion") != 1
            or document.get("status") != "reviewed"
            or document.get("missingReview") != []
            or document.get("target") != {
                "steamosVersion": target["steamosVersion"],
                "nvidiaVersion": target["nvidiaVersion"],
                "architecture": target["architecture"],
            }):
        fail("userspace lock is not reviewed for the exact target")
    keyring = document.get("keyring")
    if (not isinstance(keyring, dict) or set(keyring) != {
            "filename", "sha256", "provenance"
            } or not safe_portable_name(keyring.get("filename"))
            or HASH.fullmatch(keyring.get("sha256", "")) is None
            or not isinstance(keyring.get("provenance"), dict)):
        fail("userspace lock keyring identity is invalid")
    packages = document.get("packages")
    if not isinstance(packages, list) or not 2 <= len(packages) <= 64:
        fail("userspace lock package set is invalid")
    names = set()
    filenames = set()
    for package in packages:
        if (not isinstance(package, dict) or set(package) != PACKAGE_FIELDS
                or not safe_package_name(package.get("filename"))
                or not safe_package_name(package.get("signatureFilename"))
                or package["signatureFilename"] != package["filename"] + ".sig"
                or not safe_package_name(package.get("name"))
                or package["name"] in names
                or HASH.fullmatch(package.get("packageSha256", "")) is None
                or HASH.fullmatch(package.get("signatureSha256", "")) is None
                or FINGERPRINT.fullmatch(package.get("signerFingerprint", "")) is None
                or type(package.get("installedSize")) is not int
                or not 0 <= package["installedSize"] <= 2**63 - 1
                or package.get("architecture") not in {"x86_64", "any"}
                or not isinstance(package.get("dependencies"), list)
                or not isinstance(package.get("provides"), list)
                or len(package["dependencies"]) > 256
                or len(package["provides"]) > 256
                or len(package["dependencies"]) != len(set(package["dependencies"]))
                or len(package["provides"]) != len(set(package["provides"]))
                or any(not isinstance(item, str) or not item or len(item) > 512
                       for field in ("dependencies", "provides")
                       for item in package[field])):
            fail("userspace lock contains an invalid package record")
        package_files = {package["filename"], package["signatureFilename"]}
        if filenames & package_files:
            fail("userspace lock contains duplicate package filenames")
        filenames.update(package_files)
        names.add(package["name"])
    if not {"nvidia-utils", "lib32-nvidia-utils"} <= names:
        fail("userspace lock lacks the required NVIDIA packages")
    return keyring, packages


def validate_signer_policy(document, packages):
    if (not isinstance(document, dict) or set(document) != {
            "schemaVersion", "signers"} or document.get("schemaVersion") != 1
            or not isinstance(document.get("signers"), list)
            or not 1 <= len(document["signers"]) <= 64):
        fail("package signer policy is invalid")
    authorization = set()
    fingerprints = set()
    for signer in document["signers"]:
        if (not isinstance(signer, dict) or set(signer) != {
                "fingerprint", "status", "packages", "reviewedAt", "evidence"
                } or FINGERPRINT.fullmatch(signer.get("fingerprint", "")) is None
                or signer.get("status") != "active"
                or not isinstance(signer.get("packages"), list)
                or not 1 <= len(signer["packages"]) <= 64
                or len(signer["packages"]) != len(set(signer["packages"]))
                or any(not safe_package_name(item) for item in signer["packages"])
                or not isinstance(signer.get("reviewedAt"), str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", signer["reviewedAt"])
                is None
                or not isinstance(signer.get("evidence"), str)
                or not 1 <= len(signer["evidence"]) <= 512
                or signer["fingerprint"] in fingerprints):
            fail("package signer policy contains an invalid record")
        fingerprints.add(signer["fingerprint"])
        authorization.update(
            (name, signer["fingerprint"]) for name in signer["packages"]
        )
    for package in packages:
        if (package["name"], package["signerFingerprint"]) not in authorization:
            fail("reviewed lock package signer is not authorized by policy")
    return document


def authenticate_lineage(root, records, lineage_hashes, gpgv, keyring,
                         fingerprint, compatibility):
    authenticated = []
    consumed = set()
    for expected_hash in lineage_hashes:
        matches = [name for name, record in records.items()
                   if record["sha256"] == expected_hash
                   and name.endswith(".manifest.json")]
        if len(matches) != 1:
            fail("lineage manifest is missing or ambiguous")
        manifest_name = matches[0]
        signature_name = manifest_name + ".sig"
        signature_record = records.get(signature_name)
        if signature_record is None:
            fail("lineage manifest signature is missing")
        manifest_payload = read_received_payload(
            root, records, manifest_name, MANIFEST_MAX_BYTES, "lineage manifest"
        )
        signature_payload = read_received_payload(
            root, records, signature_name, MAX_SIGNATURE_BYTES,
            "lineage manifest signature",
        )
        verify_signature(
            gpgv, keyring, manifest_payload, signature_payload,
            fingerprint, "lineage manifest",
        )
        manifest = strict_json(
            manifest_payload, MANIFEST_MAX_BYTES, "lineage manifest"
        )
        release_tag = manifest_name[:-len(".manifest.json")]
        discovery = {
            "schemaVersion": 1,
            "kind": "opemos-userspace-lock-discovery",
            "channel": manifest.get("channel"),
            "sequence": manifest.get("sequence"),
            "publishedAt": manifest.get("publishedAt"),
            "authority": manifest.get("authority"),
            "compatibility": compatibility,
            "generation": {
                "releaseTag": release_tag,
                "manifestFilename": manifest_name,
                "manifestSha256": expected_hash,
                "manifestSize": len(manifest_payload),
                "signatureFilename": signature_name,
                "signatureSha256": digest(signature_payload),
                "signatureSize": len(signature_payload),
                "previousManifestSha256": manifest.get("previousManifestSha256"),
            },
            "targets": manifest.get("targetLocks"),
        }
        try:
            validate_pair(discovery, manifest)
        except ValueError as error:
            fail(f"lineage generation is invalid: {error}")
        authenticated.append((discovery, manifest))
        consumed.update((manifest_name, signature_name))
    return authenticated, consumed


def validate_verifier_evidence(payload, policy_payload, keyring_payload,
                               discovery_payload, discovery_signature,
                               manifest_payload, manifest_signature):
    try:
        record = parse_evidence_record(payload)
    except ValueError as error:
        fail(f"verifier evidence is invalid: {error}")
    expected = (
        ("discovery", discovery_payload, discovery_signature),
        ("generation-manifest", manifest_payload, manifest_signature),
    )
    if (record["policySha256"] != digest(policy_payload)
            or record["keyringSha256"] != digest(keyring_payload)):
        fail("verifier evidence belongs to another trust snapshot")
    for stored, (role, document, signature) in zip(record["documents"], expected):
        if (stored["role"] != role
                or stored["payloadSize"] != len(document)
                or stored["payloadSha256"] != digest(document)
                or stored["signatureSize"] != len(signature)
                or stored["signatureSha256"] != digest(signature)):
            fail("verifier evidence belongs to different generation bytes")
    return record


def validate_private_directory(path, label):
    try:
        info = path.lstat()
    except OSError:
        fail(f"{label} is unavailable")
    if (path.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        fail(f"{label} is not a private owned directory")
    return (info.st_dev, info.st_ino)


def validate_development_verifier(path):
    try:
        info = path.lstat()
    except OSError:
        fail("development verifier is unavailable")
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022
            or stat.S_IMODE(info.st_mode) & 0o111 == 0):
        fail("development verifier is unsafe")


def unique_role_records(manifest, roles):
    result = {role: [] for role in roles}
    for record in manifest["files"]:
        if record["role"] in result:
            result[record["role"]].append(record)
    return result


def record_by_hash(records, expected_hash, label):
    matches = [record for record in records if record["sha256"] == expected_hash]
    if len(matches) != 1:
        fail(f"generation does not contain exactly one {label}")
    return matches[0]


def copy_exact(source, destination, size, expected_hash):
    before = source.lstat()
    with source.open("rb") as reader, destination.open("xb") as writer:
        value = hashlib.sha256()
        copied = 0
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            value.update(chunk)
            copied += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())
    destination.chmod(0o400)
    if (copied != size or value.hexdigest() != expected_hash
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        fail("generation payload changed while preparing installer inputs")


def prepare(arguments):
    root = arguments.handoff.resolve(strict=True)
    info = arguments.handoff.lstat()
    if (arguments.handoff.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022):
        fail("appliance handoff directory is unsafe")
    target = {
        "steamosVersion": arguments.steamos,
        "kernelVersion": arguments.kernel,
        "nvidiaVersion": arguments.nvidia,
        "architecture": arguments.architecture,
    }
    handoff_payload = read_regular(
        root / HANDOFF_FILENAME, HANDOFF_MAX_BYTES, "appliance handoff record"
    )
    handoff = parse_json(
        handoff_payload, HANDOFF_MAX_BYTES, "appliance handoff record", True
    )
    identity, records = validate_handoff(handoff, arguments.operation_id, target)
    verify_inventory(root, records)

    policy_payload = read_regular(arguments.policy, 64 * 1024, "generation policy")
    keyring_payload = read_regular(
        arguments.keyring, MAX_KEYRING_BYTES, "generation keyring"
    )
    checkpoint_payload = read_regular(
        arguments.checkpoint, 16 * 1024, "generation checkpoint"
    )
    try:
        policy = parse_policy(policy_payload)
        checkpoint = parse_checkpoint(checkpoint_payload, policy_payload)
        authority = expected_generation_authority(policy, policy_payload)
    except ValueError as error:
        fail(f"generation trust configuration is invalid: {error}")
    if (policy["authority"]["keyringFilename"] != arguments.keyring.name
            or policy["authority"]["keyringSha256"] != digest(keyring_payload)):
        fail("generation keyring differs from policy")

    discovery_name = policy["channel"]["discoveryFilename"]
    discovery_signature_name = policy["channel"]["discoverySignatureFilename"]
    discovery_payload = read_received_payload(
        root, records, discovery_name, DISCOVERY_MAX_BYTES, "generation discovery"
    )
    discovery_signature = read_received_payload(
        root, records, discovery_signature_name, MAX_SIGNATURE_BYTES,
        "generation discovery signature",
    )
    verify_signature(
        arguments.gpgv, keyring_payload, discovery_payload,
        discovery_signature,
        policy["authority"]["primarySigningFingerprint"], "discovery",
    )
    discovery = strict_json(discovery_payload, DISCOVERY_MAX_BYTES, "discovery")
    generation = discovery["generation"]
    manifest_name = generation["manifestFilename"]
    signature_name = generation["signatureFilename"]
    manifest_payload = read_received_payload(
        root, records, manifest_name, MANIFEST_MAX_BYTES, "generation manifest"
    )
    manifest_signature = read_received_payload(
        root, records, signature_name, MAX_SIGNATURE_BYTES,
        "generation manifest signature",
    )
    verify_signature(
        arguments.gpgv, keyring_payload, manifest_payload, manifest_signature,
        policy["authority"]["primarySigningFingerprint"], "manifest",
    )
    manifest = strict_json(manifest_payload, MANIFEST_MAX_BYTES, "manifest")
    lineage, lineage_files = authenticate_lineage(
        root, records, handoff["lineageManifestSha256"], arguments.gpgv,
        keyring_payload, policy["authority"]["primarySigningFingerprint"],
        discovery["compatibility"],
    )
    try:
        validate_pair(discovery, manifest)
        validate_activation(
            discovery, manifest, authority, target, 0, None, lineage,
            {
                "sequence": checkpoint["minimumSequence"],
                "manifestSha256": checkpoint["minimumManifestSha256"],
            }, None,
        )
    except ValueError as error:
        fail(f"generation is not authorized for this target: {error}")
    if (identity["sequence"] != manifest["sequence"]
            or identity["manifestSha256"] != generation["manifestSha256"]):
        fail("handoff identity differs from authenticated generation")

    evidence_payload = read_received_payload(
        root, records, EVIDENCE_FILENAME, MAX_EVIDENCE_BYTES, "verifier evidence"
    )
    validate_verifier_evidence(
        evidence_payload, policy_payload, keyring_payload,
        discovery_payload, discovery_signature, manifest_payload,
        manifest_signature,
    )

    manifest_by_name = {record["filename"]: record for record in manifest["files"]}
    for name, record in manifest_by_name.items():
        receipt = records.get(name)
        if (receipt is None or receipt["size"] != record["size"]
                or receipt["sha256"] != record["sha256"]):
            fail("handoff differs from generation manifest")
    expected_handoff_files = {
        discovery_name, discovery_signature_name, manifest_name, signature_name,
        EVIDENCE_FILENAME, *manifest_by_name, *lineage_files,
    }
    if set(records) != expected_handoff_files:
        fail("appliance handoff contains an unexpected or missing file")
    roles = unique_role_records(
        manifest, {"userspace-lock", "package", "package-signature", "keyring",
                   "signer-policy"},
    )
    selected = [record for record in manifest["targetLocks"] if record["target"] == target]
    if len(manifest["targetLocks"]) != 1 or len(selected) != 1:
        fail("appliance generation must contain exactly one exact target lock")
    lock_identity = selected[0]["lock"]
    lock_record = manifest_by_name.get(lock_identity["filename"])
    if (lock_record is None or lock_record["role"] != "userspace-lock"):
        fail("exact target lock is absent from generation")
    lock_payload = read_received_payload(
        root, records, lock_record["filename"], MAX_LOCK_BYTES, "userspace lock"
    )
    lock = parse_json(lock_payload, MAX_LOCK_BYTES, "userspace lock")
    lock_keyring, packages = validate_lock(lock, target)
    package_keyring = record_by_hash(
        roles["keyring"], lock_keyring["sha256"], "package keyring"
    )
    if package_keyring["filename"] != lock_keyring["filename"]:
        fail("generation package keyring filename differs from lock")
    if len(roles["signer-policy"]) != 1:
        fail("generation does not contain one package signer policy")
    signer_policy = roles["signer-policy"][0]
    signer_policy_payload = read_received_payload(
        root, records, signer_policy["filename"], 256 * 1024,
        "package signer policy",
    )
    signer_policy_document = parse_json(
        signer_policy_payload, 256 * 1024, "package signer policy"
    )
    validate_signer_policy(signer_policy_document, packages)

    package_records = []
    used = set()
    for package in packages:
        artifact = record_by_hash(
            roles["package"], package["packageSha256"],
            f"package payload for {package['name']}",
        )
        signature = record_by_hash(
            roles["package-signature"], package["signatureSha256"],
            f"package signature for {package['name']}",
        )
        used.update((artifact["filename"], signature["filename"]))
        package_records.append((package, artifact, signature))
    if (used != {record["filename"] for role in ("package", "package-signature")
                for record in roles[role]}):
        fail("generation package set differs from reviewed lock")

    output = arguments.output
    if output.exists() or output.is_symlink():
        fail("installer input output already exists")
    if not safe_portable_name(output.name):
        fail("installer input output name is unsafe")
    parent_identity = validate_private_directory(
        output.parent, "installer input output parent"
    )
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        fail("installer input output already exists")
    stage = output
    complete = False
    try:
        for package, artifact, signature in package_records:
            copy_exact(
                root / artifact["filename"], stage / package["filename"],
                artifact["size"], artifact["sha256"],
            )
            copy_exact(
                root / signature["filename"], stage / package["signatureFilename"],
                signature["size"], signature["sha256"],
            )
        copy_exact(
            root / lock_record["filename"], stage / lock_identity["filename"],
            lock_record["size"], lock_record["sha256"],
        )
        copy_exact(
            root / package_keyring["filename"], stage / lock_keyring["filename"],
            package_keyring["size"], package_keyring["sha256"],
        )
        copy_exact(
            root / signer_policy["filename"], stage / signer_policy["filename"],
            signer_policy["size"], signer_policy["sha256"],
        )
        result = {
            "schemaVersion": 1,
            "status": "prepared",
            "reason": "development_generation_prepared",
            "trust": "development-test-only",
            "operationId": handoff["operationId"],
            "generation": identity,
            "target": target,
            "userspaceLock": lock_identity["filename"],
            "packageKeyring": lock_keyring["filename"],
            "packageSignerPolicy": signer_policy["filename"],
            "packages": [{
                "name": package["name"],
                "filename": package["filename"],
                "signatureFilename": package["signatureFilename"],
                "packageSha256": package["packageSha256"],
                "signatureSha256": package["signatureSha256"],
            } for package, _artifact, _signature in package_records],
        }
        encoded = canonical(result)
        if len(encoded) > RESULT_MAX_BYTES:
            fail("installer input descriptor is excessive")
        descriptor = stage / "installer-inputs-v1.json"
        descriptor.write_bytes(encoded)
        descriptor.chmod(0o400)
        for path in stage.iterdir():
            if not path.is_file() or path.is_symlink():
                fail("prepared installer input layout is unsafe")
        if validate_private_directory(output.parent, "installer input output parent") \
                != parent_identity:
            fail("installer input output parent changed during preparation")
        stage.chmod(0o500)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        complete = True
        return result
    finally:
        if not complete and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prepare installer inputs from one development/test Core generation handoff"
    )
    parser.add_argument("--development-test", action="store_true")
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--keyring", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--gpgv", required=True, type=Path)
    parser.add_argument("--steamos", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--architecture", default="x86_64")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main():
    arguments = parse_args()
    if not arguments.development_test:
        fail("production appliance generation trust is not configured")
    if not arguments.gpgv.is_absolute():
        fail("development verifier path must be absolute")
    validate_development_verifier(arguments.gpgv)
    document = prepare(arguments)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(Cancelled()))
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Cancelled()))
        main()
    except Cancelled:
        raise SystemExit(130)
    except (ConsumerError, OSError) as error:
        raise SystemExit(f"consume_appliance_generation.py: {error}") from None
