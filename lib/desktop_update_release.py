#!/usr/bin/env python3
"""Create and validate canonical OPEMOS desktop update release inputs."""

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BINARY_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
CANONICAL_REPOSITORY = "CorniiDog/OPEMOS"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"[0-9A-F]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MANIFEST_FIELDS = {
    "schemaVersion", "kind", "releaseTag", "version", "architecture",
    "filename", "size", "sha256", "supportRevision", "minimumGuardianSchema",
}


class ReleaseError(Exception):
    pass


class ReleaseCancelled(Exception):
    pass


def fail(message):
    raise ReleaseError(message)


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def snapshot(path, maximum, label):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        fail(f"{label} is missing, unreadable, or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= maximum):
            fail(f"{label} is not a bounded single-link regular file")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(after) or len(payload) != before.st_size:
            fail(f"{label} changed while it was being read")
        return payload
    finally:
        os.close(descriptor)


def strict_json(payload, label):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(f"{label} contains a duplicate key")
            result[key] = value
        return result
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError):
        fail(f"{label} is malformed")
    if not isinstance(document, dict):
        fail(f"{label} has an unsupported shape")
    return document


def validate_elf(payload):
    if (len(payload) < 64 or payload[:4] != b"\x7fELF" or payload[4] != 2
            or payload[5] != 1 or int.from_bytes(payload[18:20], "little") != 62):
        fail("desktop executable is not an x86_64 little-endian ELF")


def manifest_document(binary, version, revision):
    if (not isinstance(version, str) or not isinstance(revision, str)
            or not VERSION.fullmatch(version) or not COMMIT.fullmatch(revision)):
        fail("version or support revision is invalid")
    validate_elf(binary)
    return {
        "schemaVersion": 1,
        "kind": "opemos-desktop-update",
        "releaseTag": f"opemos-desktop-v{version}",
        "version": version,
        "architecture": "x86_64",
        "filename": "opemos-recovery-status",
        "size": len(binary),
        "sha256": sha256(binary),
        "supportRevision": revision,
        "minimumGuardianSchema": 1,
    }


def validate_manifest(document, payload, binary):
    if canonical(document) != payload or set(document) != MANIFEST_FIELDS:
        fail("desktop manifest is not canonical schema-1 JSON")
    expected = manifest_document(binary, document.get("version"), document.get("supportRevision"))
    if document != expected:
        fail("desktop manifest differs from the executable or canonical identity")
    return expected


def validate_policy(policy, payload, keyring):
    if canonical(policy) != payload or set(policy) != {
            "schemaVersion", "status", "keyringSha256", "signers"}:
        fail("desktop update signer policy is not canonical schema-1 JSON")
    if (type(policy.get("schemaVersion")) is not int or policy["schemaVersion"] != 1
            or policy.get("status") != "active"
            or policy.get("keyringSha256") != sha256(keyring)
            or not isinstance(policy.get("signers"), list)
            or not 1 <= len(policy["signers"]) <= 16):
        fail("desktop update signer policy is inactive or does not bind the keyring")
    fingerprints = []
    for signer in policy["signers"]:
        if (not isinstance(signer, dict)
                or set(signer) != {"fingerprint", "status", "scope"}
                or signer.get("status") != "active"
                or signer.get("scope") != "opemos-desktop-update"
                or not isinstance(signer.get("fingerprint"), str)
                or not FINGERPRINT.fullmatch(signer["fingerprint"])):
            fail("desktop update signer policy contains an invalid signer")
        fingerprints.append(signer["fingerprint"])
    if len(fingerprints) != len(set(fingerprints)):
        fail("desktop update signer policy contains duplicate signers")
    return set(fingerprints)


def terminate(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_bounded(command, timeout=60):
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except OSError:
        fail("signature tool is unavailable")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate(process)
        fail("signature tool timed out")
    except BaseException:
        terminate(process)
        raise
    return process.returncode, stdout[:65536], stderr[:4096]


def verify_signature(manifest, signature, keyring, allowed):
    with tempfile.TemporaryDirectory(prefix="opemos-release-verify-") as name:
        root = Path(name)
        paths = []
        for filename, payload in (("manifest.json", manifest), ("manifest.json.sig", signature),
                                  ("keyring.gpg", keyring)):
            path = root / filename
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
            paths.append(path)
        executable = "/usr/bin/gpgv"
        if os.environ.get("OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE") == "1":
            executable = os.environ.get("OPEMOS_TEST_GPGV", executable)
        code, stdout, _stderr = run_bounded([
            executable, "--status-fd", "1", "--keyring", str(paths[2]),
            str(paths[1]), str(paths[0]),
        ])
    if code:
        fail("desktop manifest signature is cryptographically invalid")
    signers = []
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            signers.append(fields[2].upper())
    if len(signers) != 1 or not FINGERPRINT.fullmatch(signers[0]):
        fail("desktop manifest signature did not identify exactly one signer")
    if signers[0] not in allowed:
        fail("desktop manifest signer is not approved by the reviewed policy")
    return signers[0]


def safe_asset(path, expected, label):
    path = Path(path)
    if path.name != expected:
        fail(f"{label} filename must be {expected}")
    return path


def write_create_only(path, payload, mode=0o644):
    path = Path(path)
    if path.name in ("", ".", "..") or path.parent.is_symlink() or not path.parent.is_dir():
        fail("output path is unsafe")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    except OSError:
        fail("output already exists or cannot be created safely")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def command_manifest(args):
    binary_path = safe_asset(args.binary, "opemos-recovery-status", "desktop executable")
    binary = snapshot(binary_path, MAX_BINARY_BYTES, "desktop executable")
    payload = canonical(manifest_document(binary, args.version, args.support_revision))
    if args.output:
        expected = f"opemos-desktop-v{args.version}.manifest.json"
        write_create_only(safe_asset(args.output, expected, "manifest output"), payload)
    sys.stdout.buffer.write(payload)


def command_policy(args):
    keyring = snapshot(args.keyring, MAX_KEYRING_BYTES, "desktop update public keyring")
    fingerprint = (args.signer or "").upper()
    if not FINGERPRINT.fullmatch(fingerprint):
        fail("signer fingerprint is invalid")
    gpg = "/usr/bin/gpg"
    if os.environ.get("OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE") == "1":
        gpg = os.environ.get("OPEMOS_TEST_GPG", gpg)
    with tempfile.TemporaryDirectory(prefix="opemos-keyring-review-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir(mode=0o700)
        staged_keyring = Path(temporary) / "keyring.gpg"
        write_create_only(staged_keyring, keyring, 0o400)
        code, stdout, _stderr = run_bounded([
            gpg, "--homedir", str(home), "--batch", "--no-options", "--no-default-keyring",
            "--keyring", str(staged_keyring), "--with-colons", "--fingerprint",
            "--list-keys",
        ])
    primary_fingerprints = []
    awaiting_primary_fingerprint = False
    unsafe_secret_record = False
    for line in stdout.splitlines():
        fields = line.split(":")
        record = fields[0] if fields else ""
        if record in ("sec", "ssb"):
            unsafe_secret_record = True
        if record == "pub":
            if len(fields) < 2 or fields[1] in ("r", "e", "d", "i"):
                fail("desktop update public key is revoked, expired, or invalid")
            awaiting_primary_fingerprint = True
        elif record == "fpr" and awaiting_primary_fingerprint:
            if len(fields) <= 9 or not FINGERPRINT.fullmatch(fields[9].upper()):
                fail("desktop update public key fingerprint is malformed")
            primary_fingerprints.append(fields[9].upper())
            awaiting_primary_fingerprint = False
    if code or unsafe_secret_record or primary_fingerprints != [fingerprint]:
        fail("public keyring must contain exactly the one reviewed primary signer")
    policy = {
        "schemaVersion": 1,
        "status": "active",
        "keyringSha256": sha256(keyring),
        "signers": [{"fingerprint": fingerprint, "status": "active",
                     "scope": "opemos-desktop-update"}],
    }
    payload = canonical(policy)
    if args.output:
        write_create_only(args.output, payload)
    sys.stdout.buffer.write(payload)


def command_plan(args):
    if not REPOSITORY.fullmatch(args.repository or ""):
        fail("repository identity is invalid")
    if args.repository != CANONICAL_REPOSITORY and not args.development_repository:
        fail("noncanonical repositories require the explicit development override")
    binary_path = safe_asset(args.binary, "opemos-recovery-status", "desktop executable")
    binary = snapshot(binary_path, MAX_BINARY_BYTES, "desktop executable")
    manifest_name = f"opemos-desktop-v{args.version}.manifest.json"
    manifest_path = safe_asset(args.manifest, manifest_name, "desktop manifest")
    signature_path = safe_asset(args.signature, manifest_name + ".sig", "desktop signature")
    manifest = snapshot(manifest_path, MAX_DOCUMENT_BYTES, "desktop manifest")
    signature = snapshot(signature_path, MAX_SIGNATURE_BYTES, "desktop signature")
    keyring = snapshot(args.keyring, MAX_KEYRING_BYTES, "desktop update public keyring")
    policy_payload = snapshot(args.policy, MAX_DOCUMENT_BYTES, "desktop update signer policy")
    document = strict_json(manifest, "desktop manifest")
    validate_manifest(document, manifest, binary)
    if document["version"] != args.version:
        fail("requested version differs from the manifest")
    policy = strict_json(policy_payload, "desktop update signer policy")
    allowed = validate_policy(policy, policy_payload, keyring)
    signer = verify_signature(manifest, signature, keyring, allowed)
    tag = document["releaseTag"]
    title = f"OPEMOS.EXE Desktop Companion {document['version']}"
    notes = "\n".join([
        f"Authenticated OPEMOS desktop companion update {document['version']}.",
        "",
        f"Architecture: {document['architecture']}",
        f"Support revision: {document['supportRevision']}",
        f"Executable SHA-256: {document['sha256']}",
        f"Manifest SHA-256: {sha256(manifest)}",
        f"Signature SHA-256: {sha256(signature)}",
        f"Signer fingerprint: {signer}",
        f"Reviewed keyring SHA-256: {sha256(keyring)}",
        f"Trust policy SHA-256: {sha256(policy_payload)}",
    ]) + "\n"
    plan = {
        "schemaVersion": 1,
        "status": "validated",
        "kind": "opemos-desktop-update-publication",
        "repository": args.repository,
        "tag": tag,
        "targetCommit": document["supportRevision"],
        "title": title,
        "notes": notes,
        "signerFingerprint": signer,
        "keyringSha256": sha256(keyring),
        "policySha256": sha256(policy_payload),
        "assets": [
            {"name": binary_path.name, "sha256": sha256(binary), "size": len(binary)},
            {"name": manifest_path.name, "sha256": sha256(manifest), "size": len(manifest)},
            {"name": signature_path.name, "sha256": sha256(signature), "size": len(signature)},
        ],
    }
    sys.stdout.buffer.write(canonical(plan))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="Create a canonical manifest")
    manifest.add_argument("--binary", required=True, type=Path)
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--support-revision", required=True)
    manifest.add_argument("--output", type=Path)
    manifest.set_defaults(handler=command_manifest)
    policy = commands.add_parser("trust-policy", help="Create a reviewed public-key policy")
    policy.add_argument("--keyring", required=True, type=Path)
    policy.add_argument("--signer", required=True)
    policy.add_argument("--output", type=Path)
    policy.set_defaults(handler=command_policy)
    plan = commands.add_parser("plan", help="Validate inputs and emit a publication plan")
    plan.add_argument("--binary", required=True, type=Path)
    plan.add_argument("--manifest", required=True, type=Path)
    plan.add_argument("--signature", required=True, type=Path)
    plan.add_argument("--policy", required=True, type=Path)
    plan.add_argument("--keyring", required=True, type=Path)
    plan.add_argument("--version", required=True)
    plan.add_argument("--repository", default=CANONICAL_REPOSITORY)
    plan.add_argument("--development-repository", action="store_true")
    plan.set_defaults(handler=command_plan)
    return result


def main():
    def interrupted(_signum, _frame):
        raise ReleaseCancelled
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        args = parser().parse_args()
        args.handler(args)
    except ReleaseCancelled:
        print("desktop_update_release.py: cancelled", file=sys.stderr)
        raise SystemExit(130)
    except ReleaseError as error:
        print(f"desktop_update_release.py: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("desktop_update_release.py: bounded local operation failed", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
