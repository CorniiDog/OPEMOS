#!/usr/bin/env python3
"""Export/import detached-signature artifacts without weakening their trust."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
FINGERPRINT = re.compile(r"[0-9A-F]{40}")


def fail(message):
    raise SystemExit(message)


def regular(path, label, maximum):
    try:
        value = path.lstat()
    except OSError:
        fail(f"{label} is missing or unreadable")
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        fail(f"{label} must be a single-link regular file")
    if value.st_size > maximum:
        fail(f"{label} exceeds its size limit")
    return value.st_size


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def strict_json(path):
    regular(path, "manifest", MAX_MANIFEST_BYTES)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                fail("manifest contains a duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError):
        fail("manifest is not bounded canonical JSON")


def signer_from_gpgv(artifact, signature, keyring):
    try:
        completed = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring),
             str(signature), str(artifact)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("detached-signature verification could not complete")
    if completed.returncode:
        fail("detached-signature verification failed")
    fingerprints = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            fingerprints.append(fields[2].upper())
    if len(fingerprints) != 1 or not FINGERPRINT.fullmatch(fingerprints[0]):
        fail("signature did not yield one full signer fingerprint")
    return fingerprints[0]


def reviewed_signer(manifest, fingerprint):
    document = strict_json(manifest)
    if (not isinstance(document, dict) or document.get("schemaVersion") != 1
            or not isinstance(document.get("signers"), list)):
        fail("reviewed signer manifest has an unsupported shape")
    matches = [item for item in document["signers"] if isinstance(item, dict)
               and item.get("fingerprint", "").upper() == fingerprint]
    if len(matches) != 1 or matches[0].get("status") != "active":
        fail("signature is not from one active reviewed signer")


def copy_regular(source, destination, maximum):
    expected = regular(source, source.name, maximum)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    if regular(destination, destination.name, maximum) != expected:
        fail("source changed while it was copied")


def validate_document(document):
    required = {"schemaVersion", "kind", "originalFilename", "artifact", "signature", "trust"}
    if not isinstance(document, dict) or set(document) != required:
        fail("bundle manifest has an unsupported shape")
    name = document.get("originalFilename")
    if (document.get("schemaVersion") != 1 or document.get("kind") != "detached-signature-artifact"
            or not isinstance(name, str) or not name or len(name.encode()) > 255
            or any(ord(character) < 32 for character in name) or Path(name).name != name):
        fail("bundle identity is invalid")
    for label in ("artifact", "signature"):
        record = document[label]
        if (not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}
                or record["path"] != f"payload/{'artifact' if label == 'artifact' else 'artifact.sig'}"
                or not isinstance(record["size"], int) or record["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"] or "")):
            fail(f"bundle {label} metadata is invalid")
    trust = document["trust"]
    if (not isinstance(trust, dict)
            or set(trust) != {"method", "signerFingerprint", "keyringSha256", "reviewedSignersSha256"}
            or trust["method"] != "detached-signature+reviewed-signer"
            or not FINGERPRINT.fullmatch(trust["signerFingerprint"] or "")
            or any(not re.fullmatch(r"[0-9a-f]{64}", trust[key] or "")
                   for key in ("keyringSha256", "reviewedSignersSha256"))):
        fail("bundle trust metadata is invalid")


def verify_payload(root, document, keyring, signers):
    regular(keyring, "trusted keyring", MAX_KEYRING_BYTES)
    artifact, signature = root / "payload/artifact", root / "payload/artifact.sig"
    for label, path, maximum in (("artifact", artifact, MAX_ARTIFACT_BYTES),
                                 ("signature", signature, MAX_SIGNATURE_BYTES)):
        size = regular(path, f"bundle {label}", maximum)
        if size != document[label]["size"] or digest(path) != document[label]["sha256"]:
            fail(f"bundle {label} does not match its manifest")
    if digest(keyring) != document["trust"]["keyringSha256"]:
        fail("trusted keyring does not match the exported trust anchor")
    if digest(signers) != document["trust"]["reviewedSignersSha256"]:
        fail("reviewed signer policy does not match the exported policy")
    fingerprint = signer_from_gpgv(artifact, signature, keyring)
    if fingerprint != document["trust"]["signerFingerprint"]:
        fail("current signature signer differs from the exported signer")
    reviewed_signer(signers, fingerprint)


def export(args):
    regular(args.keyring, "trusted keyring", MAX_KEYRING_BYTES)
    fingerprint = signer_from_gpgv(args.artifact, args.signature, args.keyring)
    reviewed_signer(args.reviewed_signers, fingerprint)
    if args.output.exists() or args.output.is_symlink():
        fail("output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".cache-export-", dir=args.output.parent))
    try:
        (temporary / "payload").mkdir()
        copy_regular(args.artifact, temporary / "payload/artifact", MAX_ARTIFACT_BYTES)
        copy_regular(args.signature, temporary / "payload/artifact.sig", MAX_SIGNATURE_BYTES)
        document = {
            "schemaVersion": 1, "kind": "detached-signature-artifact",
            "originalFilename": args.artifact.name,
            "artifact": {"path": "payload/artifact", "size": regular(temporary / "payload/artifact", "artifact", MAX_ARTIFACT_BYTES), "sha256": digest(temporary / "payload/artifact")},
            "signature": {"path": "payload/artifact.sig", "size": regular(temporary / "payload/artifact.sig", "signature", MAX_SIGNATURE_BYTES), "sha256": digest(temporary / "payload/artifact.sig")},
            "trust": {"method": "detached-signature+reviewed-signer", "signerFingerprint": fingerprint,
                      "keyringSha256": digest(args.keyring), "reviewedSignersSha256": digest(args.reviewed_signers)},
        }
        (temporary / "manifest.json").write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        verify_payload(temporary, document, args.keyring, args.reviewed_signers)
        os.replace(temporary, args.output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def import_bundle(args):
    if args.bundle.is_symlink() or not args.bundle.is_dir():
        fail("bundle root must be a real directory")
    if args.store.is_symlink():
        fail("cache store must not be a symbolic link")
    document = strict_json(args.bundle / "manifest.json")
    validate_document(document)
    verify_payload(args.bundle, document, args.keyring, args.reviewed_signers)
    identity = digest(args.bundle / "manifest.json")
    args.store.mkdir(parents=True, exist_ok=True)
    lock_path = args.store / ".import.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = args.store / identity
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                fail("cache generation has an unsafe type")
            existing = strict_json(destination / "manifest.json")
            validate_document(existing)
            verify_payload(destination, existing, args.keyring, args.reviewed_signers)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".cache-import-", dir=args.store))
            try:
                (temporary / "payload").mkdir()
                copy_regular(args.bundle / "payload/artifact", temporary / "payload/artifact", MAX_ARTIFACT_BYTES)
                copy_regular(args.bundle / "payload/artifact.sig", temporary / "payload/artifact.sig", MAX_SIGNATURE_BYTES)
                copy_regular(args.bundle / "manifest.json", temporary / "manifest.json", MAX_MANIFEST_BYTES)
                verify_payload(temporary, document, args.keyring, args.reviewed_signers)
                os.replace(temporary, destination)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
    print(json.dumps({"schemaVersion": 1, "status": "verified", "cacheId": identity,
                      "artifact": str(destination / "payload/artifact"),
                      "originalFilename": document["originalFilename"]}, sort_keys=True))


def arguments():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        command = commands.add_parser(name)
        command.add_argument("--keyring", required=True, type=Path)
        command.add_argument("--reviewed-signers", required=True, type=Path)
        if name == "export":
            command.add_argument("--artifact", required=True, type=Path)
            command.add_argument("--signature", required=True, type=Path)
            command.add_argument("--output", required=True, type=Path)
        else:
            command.add_argument("--bundle", required=True, type=Path)
            command.add_argument("--store", required=True, type=Path)
    return parser.parse_args()


def main():
    args = arguments()
    export(args) if args.command == "export" else import_bundle(args)


if __name__ == "__main__":
    main()
