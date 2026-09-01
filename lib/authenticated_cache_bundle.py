#!/usr/bin/env python3
"""Export/import detached-signature artifacts without weakening their trust."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
MAX_SET_FILES = 64
MAX_SET_BYTES = 8 * 1024 * 1024 * 1024
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


def require_canonical_json(path, document):
    expected = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        if path.read_text(encoding="utf-8") != expected:
            fail("manifest is not canonical JSON")
    except (OSError, UnicodeError):
        fail("manifest is unreadable")


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
        before = os.fstat(reader.fileno())
        source_hash = hashlib.sha256()
        while chunk := reader.read(1024 * 1024):
            source_hash.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())
    if (before.st_size != expected or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or regular(destination, destination.name, maximum) != expected
            or digest(destination) != source_hash.hexdigest()):
        fail("source changed while it was copied")


def seal_generation(root):
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            os.chmod(Path(current) / name, 0o444, follow_symlinks=False)
        for name in directories:
            os.chmod(Path(current) / name, 0o555, follow_symlinks=False)
    os.chmod(root, 0o555, follow_symlinks=False)


@contextmanager
def reserve_output(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    reservation = output.with_name(output.name + ".lock")
    try:
        reservation.mkdir(mode=0o700)
    except FileExistsError:
        fail("output is already reserved by another export")
    try:
        if output.exists() or output.is_symlink():
            fail("output already exists")
        yield
    finally:
        try:
            reservation.rmdir()
        except OSError:
            pass


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


def validate_set_document(document):
    required = {"schemaVersion", "kind", "policy", "provenance", "artifacts", "trust"}
    if not isinstance(document, dict) or set(document) != required:
        fail("bundle manifest has an unsupported shape")
    if document.get("schemaVersion") != 1 or document.get("kind") != "authenticated-artifact-set":
        fail("bundle identity is invalid")
    for label in ("policy", "provenance"):
        record = document[label]
        if (not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}
                or record["path"] != f"metadata/{label}.json"
                or not isinstance(record["size"], int) or record["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"] or "")):
            fail(f"bundle {label} metadata is invalid")
    artifacts = document["artifacts"]
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > MAX_SET_FILES:
        fail("bundle artifact count is invalid")
    names = set()
    payload_paths = set()
    total = 0
    for record in artifacts:
        if (not isinstance(record, dict)
                or set(record) != {"name", "path", "sha256", "size", "signature", "signatureSha256", "signatureSize"}):
            fail("bundle artifact metadata is invalid")
        name = record["name"]
        if (not isinstance(name, str) or not name or len(name.encode()) > 255
                or Path(name).name != name or any(ord(c) < 32 for c in name)
                or name in names):
            fail("bundle contains an invalid or duplicate artifact name")
        names.add(name)
        candidate_paths = {record.get("path"), record.get("signature")}
        if len(candidate_paths) != 2 or payload_paths.intersection(candidate_paths):
            fail("bundle contains ambiguous artifact or signature paths")
        payload_paths.update(candidate_paths)
        if (record["path"] != f"payload/{name}" or record["signature"] != f"payload/{name}.sig"
                or not isinstance(record["size"], int) or not isinstance(record["signatureSize"], int)
                or record["size"] < 0 or record["signatureSize"] < 0
                or record["size"] > MAX_ARTIFACT_BYTES or record["signatureSize"] > MAX_SIGNATURE_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"] or "")
                or not re.fullmatch(r"[0-9a-f]{64}", record["signatureSha256"] or "")):
            fail("bundle artifact metadata is invalid")
        total += record["size"] + record["signatureSize"]
    if total > MAX_SET_BYTES:
        fail("bundle artifact set exceeds its size limit")
    trust = document["trust"]
    if (not isinstance(trust, dict) or set(trust) != {
            "method", "signerFingerprints", "keyringSha256", "reviewedSignersSha256",
            "policySha256", "provenanceSha256"}
            or trust["method"] != "detached-signatures+reviewed-policy+provenance"
            or not isinstance(trust["signerFingerprints"], list)
            or len(trust["signerFingerprints"]) != len(artifacts)
            or any(not FINGERPRINT.fullmatch(value or "") for value in trust["signerFingerprints"])
            or any(not re.fullmatch(r"[0-9a-f]{64}", trust[key] or "") for key in (
                "keyringSha256", "reviewedSignersSha256", "policySha256", "provenanceSha256"))):
        fail("bundle trust metadata is invalid")


def verify_set(root, document, keyring, signers):
    validate_set_document(document)
    expected_root = {"manifest.json", "metadata", "payload"}
    expected_metadata = {"policy.json", "provenance.json"}
    expected_payload = {Path(record["path"]).name for record in document["artifacts"]} | \
                       {Path(record["signature"]).name for record in document["artifacts"]}
    try:
        if ({entry.name for entry in root.iterdir()} != expected_root
                or {entry.name for entry in (root / "metadata").iterdir()} != expected_metadata
                or {entry.name for entry in (root / "payload").iterdir()} != expected_payload):
            fail("bundle layout contains missing or unexpected entries")
    except OSError:
        fail("bundle layout is unreadable")
    regular(keyring, "trusted keyring", MAX_KEYRING_BYTES)
    policy = root / document["policy"]["path"]
    provenance = root / document["provenance"]["path"]
    for label, path in (("policy", policy), ("provenance", provenance)):
        size = regular(path, f"bundle {label}", MAX_MANIFEST_BYTES)
        if size != document[label]["size"] or digest(path) != document[label]["sha256"]:
            fail(f"bundle {label} does not match its manifest")
        strict_json(path)
    trust = document["trust"]
    if digest(keyring) != trust["keyringSha256"] or digest(signers) != trust["reviewedSignersSha256"]:
        fail("current trust anchors differ from the exported trust policy")
    if digest(policy) != trust["policySha256"] or digest(provenance) != trust["provenanceSha256"]:
        fail("bundle policy or provenance binding is inconsistent")
    for index, record in enumerate(document["artifacts"]):
        artifact, signature = root / record["path"], root / record["signature"]
        if (regular(artifact, "bundle artifact", MAX_ARTIFACT_BYTES) != record["size"]
                or digest(artifact) != record["sha256"]
                or regular(signature, "bundle signature", MAX_SIGNATURE_BYTES) != record["signatureSize"]
                or digest(signature) != record["signatureSha256"]):
            fail("bundle artifact does not match its manifest")
        fingerprint = signer_from_gpgv(artifact, signature, keyring)
        if fingerprint != trust["signerFingerprints"][index]:
            fail("current signature signer differs from the exported signer")
        reviewed_signer(signers, fingerprint)


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
    with reserve_output(args.output):
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
    require_canonical_json(args.bundle / "manifest.json", document)
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
            require_canonical_json(destination / "manifest.json", existing)
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


def load_set_spec(path):
    document = strict_json(path)
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "artifacts"} \
            or document.get("schemaVersion") != 1 or not isinstance(document["artifacts"], list) \
            or not document["artifacts"] or len(document["artifacts"]) > MAX_SET_FILES:
        fail("artifact-set specification has an unsupported shape")
    result = []
    names = set()
    payload_names = set()
    for item in document["artifacts"]:
        if not isinstance(item, dict) or set(item) != {"artifact", "signature"}:
            fail("artifact-set specification entry is invalid")
        artifact, signature = Path(item["artifact"]), Path(item["signature"])
        if (artifact.name in names or artifact.name + ".sig" != signature.name
                or artifact.name in payload_names or signature.name in payload_names):
            fail("artifact-set specification has duplicate names or a mismatched signature")
        names.add(artifact.name)
        payload_names.update((artifact.name, signature.name))
        result.append((artifact, signature))
    return result


def export_set(args):
    regular(args.keyring, "trusted keyring", MAX_KEYRING_BYTES)
    strict_json(args.policy)
    strict_json(args.provenance)
    items = load_set_spec(args.spec)
    with reserve_output(args.output):
        temporary = Path(tempfile.mkdtemp(prefix=".cache-set-export-", dir=args.output.parent))
        try:
            (temporary / "payload").mkdir()
            (temporary / "metadata").mkdir()
            copy_regular(args.policy, temporary / "metadata/policy.json", MAX_MANIFEST_BYTES)
            copy_regular(args.provenance, temporary / "metadata/provenance.json", MAX_MANIFEST_BYTES)
            records, fingerprints = [], []
            for artifact, signature in items:
                target = temporary / "payload" / artifact.name
                target_signature = temporary / "payload" / signature.name
                copy_regular(artifact, target, MAX_ARTIFACT_BYTES)
                copy_regular(signature, target_signature, MAX_SIGNATURE_BYTES)
                fingerprint = signer_from_gpgv(target, target_signature, args.keyring)
                reviewed_signer(args.reviewed_signers, fingerprint)
                records.append({"name": artifact.name, "path": f"payload/{artifact.name}",
                                "sha256": digest(target), "size": regular(target, artifact.name, MAX_ARTIFACT_BYTES),
                                "signature": f"payload/{signature.name}", "signatureSha256": digest(target_signature),
                                "signatureSize": regular(target_signature, signature.name, MAX_SIGNATURE_BYTES)})
                fingerprints.append(fingerprint)
            policy_hash = digest(temporary / "metadata/policy.json")
            provenance_hash = digest(temporary / "metadata/provenance.json")
            document = {"schemaVersion": 1, "kind": "authenticated-artifact-set",
                        "policy": {"path": "metadata/policy.json", "sha256": policy_hash,
                                   "size": regular(temporary / "metadata/policy.json", "policy", MAX_MANIFEST_BYTES)},
                        "provenance": {"path": "metadata/provenance.json", "sha256": provenance_hash,
                                       "size": regular(temporary / "metadata/provenance.json", "provenance", MAX_MANIFEST_BYTES)},
                        "artifacts": records,
                        "trust": {"method": "detached-signatures+reviewed-policy+provenance",
                                  "signerFingerprints": fingerprints, "keyringSha256": digest(args.keyring),
                                  "reviewedSignersSha256": digest(args.reviewed_signers),
                                  "policySha256": policy_hash, "provenanceSha256": provenance_hash}}
            (temporary / "manifest.json").write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
            verify_set(temporary, document, args.keyring, args.reviewed_signers)
            os.replace(temporary, args.output)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def import_set(args):
    if args.bundle.is_symlink() or not args.bundle.is_dir() or args.store.is_symlink():
        fail("bundle and store must be real directories")
    document = strict_json(args.bundle / "manifest.json")
    require_canonical_json(args.bundle / "manifest.json", document)
    verify_set(args.bundle, document, args.keyring, args.reviewed_signers)
    identity = digest(args.bundle / "manifest.json")
    args.store.mkdir(parents=True, exist_ok=True)
    lease_path = None
    with (args.store / ".import.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = args.store / identity
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                fail("cache generation has an unsafe type")
            existing = strict_json(destination / "manifest.json")
            require_canonical_json(destination / "manifest.json", existing)
            verify_set(destination, existing, args.keyring, args.reviewed_signers)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".cache-set-import-", dir=args.store))
            try:
                (temporary / "payload").mkdir()
                (temporary / "metadata").mkdir()
                for record in document["artifacts"]:
                    copy_regular(args.bundle / record["path"], temporary / record["path"], MAX_ARTIFACT_BYTES)
                    copy_regular(args.bundle / record["signature"], temporary / record["signature"], MAX_SIGNATURE_BYTES)
                copy_regular(args.bundle / "metadata/policy.json", temporary / "metadata/policy.json", MAX_MANIFEST_BYTES)
                copy_regular(args.bundle / "metadata/provenance.json", temporary / "metadata/provenance.json", MAX_MANIFEST_BYTES)
                copy_regular(args.bundle / "manifest.json", temporary / "manifest.json", MAX_MANIFEST_BYTES)
                verify_set(temporary, document, args.keyring, args.reviewed_signers)
                seal_generation(temporary)
                os.replace(temporary, destination)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        if args.lease_token:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.lease_token):
                fail("lease token is invalid")
            leases = args.store / ".leases"
            leases.mkdir(mode=0o700, exist_ok=True)
            if leases.is_symlink() or not leases.is_dir():
                fail("lease directory has an unsafe type")
            lease_path = leases / f"{identity}.{args.lease_token}"
            descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as lease:
                lease.write(identity + "\n")
                lease.flush()
                os.fsync(lease.fileno())
        current_temporary = args.store / f".current.{os.getpid()}.tmp"
        descriptor = os.open(current_temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as current:
            current.write(identity + "\n")
            current.flush()
            os.fsync(current.fileno())
        os.replace(current_temporary, args.store / ".current")
    print(json.dumps({"schemaVersion": 1, "status": "verified", "cacheId": identity,
                      "generation": str(destination), "artifactCount": len(document["artifacts"]),
                      "policySha256": document["trust"]["policySha256"],
                      "provenanceSha256": document["trust"]["provenanceSha256"],
                      "lease": str(lease_path) if lease_path else None}, sort_keys=True))


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
    export_group = commands.add_parser("export-set")
    import_group = commands.add_parser("import-set")
    for command in (export_group, import_group):
        command.add_argument("--keyring", required=True, type=Path)
        command.add_argument("--reviewed-signers", required=True, type=Path)
    export_group.add_argument("--spec", required=True, type=Path)
    export_group.add_argument("--policy", required=True, type=Path)
    export_group.add_argument("--provenance", required=True, type=Path)
    export_group.add_argument("--output", required=True, type=Path)
    import_group.add_argument("--bundle", required=True, type=Path)
    import_group.add_argument("--store", required=True, type=Path)
    import_group.add_argument("--lease-token")
    return parser.parse_args()


def main():
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    args = arguments()
    {"export": export, "import": import_bundle, "export-set": export_set,
     "import-set": import_set}[args.command](args)


if __name__ == "__main__":
    main()
