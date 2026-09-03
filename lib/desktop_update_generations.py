#!/usr/bin/env python3
"""Authenticated, crash-safe A/B generations for the native OPEMOS companion."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "trust/desktop-update-signers.json"
DEFAULT_KEYRING = ROOT / "trust/keyrings/opemos-desktop-updates.gpg"
MAX_BINARY_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_POLICY_BYTES = 64 * 1024
IDENTITY = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"[0-9A-F]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_TAG = re.compile(r"opemos-desktop-v[0-9]+\.[0-9]+\.[0-9]+")
MANIFEST_FIELDS = {
    "schemaVersion", "kind", "releaseTag", "version", "architecture",
    "filename", "size", "sha256", "supportRevision", "minimumGuardianSchema",
}


class UpdateError(Exception):
    """A bounded, user-safe update contract failure."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class UpdateCancelled(Exception):
    """An explicit lifecycle cancellation, distinct from retryable EINTR."""


def fail(message, reason="desktop_update_validation_failed"):
    raise UpdateError(reason, message)


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def strict_json_bytes(payload, label):
    def unique(values):
        result = {}
        for key, value in values:
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


def snapshot_regular(path, maximum, label):
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError:
        fail(f"{label} is missing, unreadable, or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size < 1 or before.st_size > maximum):
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
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after or len(payload) != before.st_size:
            fail(f"{label} changed while it was being staged")
        return payload
    finally:
        os.close(descriptor)


def require_production_anchor(path, label):
    if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") == "1":
        return
    try:
        info = path.lstat()
    except OSError:
        fail(f"{label} is missing or unreadable", "desktop_update_authentication_failed")
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022):
        fail(f"{label} is not a root-owned immutable trust anchor",
             "desktop_update_authentication_failed")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def now_epoch():
    if os.environ.get("OPEMOS_TEST_NOW"):
        if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") != "1":
            fail("test clock override is not permitted")
        return int(os.environ["OPEMOS_TEST_NOW"])
    return int(time.time())


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_write(path, payload, mode=0o600):
    if path.parent.is_symlink() or not path.parent.is_dir():
        fail("update state parent is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def durable_unlink(path):
    if path.is_symlink():
        fail("update state marker is a symbolic link")
    try:
        path.unlink()
    except FileNotFoundError:
        return
    fsync_directory(path.parent)


def safe_store(store, create=False):
    if store.is_symlink():
        fail("update store must not be a symbolic link")
    if create:
        store.mkdir(parents=True, exist_ok=True)
    info = store.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022):
        fail("update store must be a real directory")
    generations = store / "generations"
    if generations.is_symlink():
        fail("update generation directory must not be a symbolic link")
    generations.mkdir(mode=0o755, exist_ok=True)
    generation_info = generations.lstat()
    if (not stat.S_ISDIR(generation_info.st_mode)
            or generation_info.st_uid != os.getuid()
            or stat.S_IMODE(generation_info.st_mode) & 0o022):
        fail("update generation directory has an unsafe type")
    return generations


@contextmanager
def update_lock(store, create=False):
    safe_store(store, create=create)
    path = store / ".update.lock"
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        fail("update lifecycle lock is unsafe or unavailable", "desktop_update_lock_failed")
    with os.fdopen(descriptor, "a+b") as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            fail("update lifecycle lock must be a single-link regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another desktop update operation is active", "desktop_update_busy")
        cleanup_staging(store / "generations")
        yield


def cleanup_staging(generations):
    entries = []
    with os.scandir(generations) as iterator:
        for entry in iterator:
            if len(entries) >= 64:
                fail("update generation store exceeds its entry limit")
            entries.append(Path(entry.path))
    for entry in entries:
        if not entry.name.startswith(".stage-"):
            continue
        if entry.is_symlink() or not entry.is_dir():
            fail("abandoned update staging entry has an unsafe type")
        shutil.rmtree(entry)
    fsync_directory(generations)


def policy_paths():
    policy, keyring = DEFAULT_POLICY, DEFAULT_KEYRING
    if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") == "1":
        policy = Path(os.environ.get("OPEMOS_DESKTOP_UPDATE_POLICY", policy))
        keyring = Path(os.environ.get("OPEMOS_DESKTOP_UPDATE_KEYRING", keyring))
    return policy, keyring


def active_policy():
    policy_path, keyring_path = policy_paths()
    require_production_anchor(policy_path, "desktop update trust policy")
    policy_payload = snapshot_regular(policy_path, MAX_POLICY_BYTES, "desktop update trust policy")
    policy = strict_json_bytes(policy_payload, "desktop update trust policy")
    if (set(policy) != {"schemaVersion", "status", "keyringSha256", "signers"}
            or type(policy.get("schemaVersion")) is not int
            or policy.get("schemaVersion") != 1 or policy.get("status") != "active"
            or not re.fullmatch(r"[0-9a-f]{64}", policy.get("keyringSha256") or "")
            or not isinstance(policy.get("signers"), list)
            or not 1 <= len(policy["signers"]) <= 16):
        fail("desktop update trust policy is not configured for production",
             "desktop_update_authentication_failed")
    signers = set()
    for record in policy["signers"]:
        if (not isinstance(record, dict)
                or set(record) != {"fingerprint", "status", "scope"}
                or record.get("status") != "active"
                or record.get("scope") != "opemos-desktop-update"
                or not FINGERPRINT.fullmatch(record.get("fingerprint", ""))
                or record["fingerprint"] in signers):
            fail("desktop update trust policy contains an invalid signer")
        signers.add(record["fingerprint"])
    require_production_anchor(keyring_path, "desktop update keyring")
    keyring = snapshot_regular(keyring_path, 16 * 1024 * 1024, "desktop update keyring")
    if sha256(keyring) != policy["keyringSha256"]:
        fail("desktop update keyring differs from the reviewed policy",
             "desktop_update_authentication_failed")
    return policy, policy_payload, keyring


def require_runtime_architecture():
    architecture = platform.machine().lower()
    if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") == "1":
        architecture = os.environ.get("OPEMOS_TEST_ARCHITECTURE", architecture).lower()
    if architecture not in ("x86_64", "amd64"):
        fail("desktop updates require an x86_64 SteamOS or Arch system")


def validate_manifest(document, payload):
    if canonical(document) != payload:
        fail("desktop update manifest is not canonical JSON")
    if (set(document) != MANIFEST_FIELDS
            or type(document.get("schemaVersion")) is not int
            or document.get("schemaVersion") != 1
            or document.get("kind") != "opemos-desktop-update"
            or document.get("filename") != "opemos-recovery-status"
            or document.get("architecture") != "x86_64"
            or not VERSION.fullmatch(document.get("version", ""))
            or len(document.get("version", "")) > 64
            or document.get("releaseTag") != f"opemos-desktop-v{document.get('version')}"
            or not SAFE_TAG.fullmatch(document.get("releaseTag", ""))
            or not COMMIT.fullmatch(document.get("supportRevision", ""))
            or type(document.get("minimumGuardianSchema")) is not int
            or document.get("minimumGuardianSchema") != 1
            or type(document.get("size")) is not int
            or not 1 <= document["size"] <= MAX_BINARY_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", document.get("sha256", ""))):
        fail("desktop update manifest has an unsupported identity")
    expected_revision = os.environ.get("OPEMOS_TEST_SUPPORT_REVISION") \
        if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") == "1" else None
    if expected_revision is None:
        revision_path = ROOT / "support-revision"
        require_production_anchor(revision_path, "installed support revision")
        revision = snapshot_regular(revision_path, 128, "installed support revision")
        try:
            expected_revision = revision.decode("ascii").strip()
        except UnicodeError:
            fail("installed support revision is malformed")
    if not COMMIT.fullmatch(expected_revision or "") or document["supportRevision"] != expected_revision:
        fail("desktop update manifest does not match the installed support revision")


def validate_elf(binary):
    if (len(binary) < 64 or binary[:4] != b"\x7fELF" or binary[4] != 2
            or binary[5] != 1 or int.from_bytes(binary[18:20], "little") != 62):
        fail("desktop update payload is not an x86_64 little-endian ELF executable")


def verify_signature(manifest_payload, signature_payload, keyring_payload, allowed_signers):
    with tempfile.TemporaryDirectory(prefix="opemos-update-trust-") as temporary_name:
        temporary = Path(temporary_name)
        manifest = temporary / "manifest.json"
        signature = temporary / "manifest.json.sig"
        keyring = temporary / "reviewed-keyring.gpg"
        write_file(manifest, manifest_payload, 0o400)
        write_file(signature, signature_payload, 0o400)
        write_file(keyring, keyring_payload, 0o400)
        gpgv = "/usr/bin/gpgv"
        if os.environ.get("OPEMOS_DEVELOPMENT_TRUST_OVERRIDE") == "1":
            gpgv = os.environ.get("OPEMOS_TEST_GPGV", gpgv)
        try:
            process = subprocess.Popen(
                [gpgv, "--status-fd", "1", "--keyring", str(keyring),
                 str(signature), str(manifest)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
        except OSError:
            fail("desktop update signature verification could not complete",
                 "desktop_update_authentication_failed")
        try:
            stdout, _stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            terminate_process_group(process)
            fail("desktop update signature verification could not complete",
                 "desktop_update_authentication_failed")
        except BaseException:
            terminate_process_group(process)
            raise
    if process.returncode:
        fail("desktop update signature is cryptographically invalid",
             "desktop_update_authentication_failed")
    fingerprints = []
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            fingerprints.append(fields[2].upper())
    if len(fingerprints) != 1 or not FINGERPRINT.fullmatch(fingerprints[0]):
        fail("desktop update signature did not identify exactly one signer",
             "desktop_update_authentication_failed")
    if fingerprints[0] not in allowed_signers:
        fail("desktop update signature is not from an active reviewed signer",
             "desktop_update_authentication_failed")
    return fingerprints[0]


def terminate_process_group(process):
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


def write_file(path, payload, mode):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def verify_generation(store, identity):
    if not IDENTITY.fullmatch(identity or ""):
        fail("desktop update generation identity is invalid")
    generation = store / "generations" / identity
    generation_info = generation.lstat() if generation.exists() else None
    if (generation_info is None or generation.is_symlink()
            or not stat.S_ISDIR(generation_info.st_mode)
            or generation_info.st_uid != os.getuid()):
        fail("desktop update generation is missing or unsafe")
    expected = {
        "manifest.json", "manifest.json.sig", "opemos-recovery-status", "trust.json"
    }
    names = []
    with os.scandir(generation) as iterator:
        for entry in iterator:
            if len(names) >= 5:
                fail("desktop update generation contains excessive entries")
            names.append(entry.name)
    if set(names) != expected:
        fail("desktop update generation contains missing or unexpected entries")
    if stat.S_IMODE(generation.stat().st_mode) != 0o555:
        fail("desktop update generation permissions are unsafe")
    manifest_path = generation / "manifest.json"
    signature_path = generation / "manifest.json.sig"
    binary_path = generation / "opemos-recovery-status"
    trust_path = generation / "trust.json"
    if (stat.S_IMODE(manifest_path.stat().st_mode) != 0o444
            or stat.S_IMODE(signature_path.stat().st_mode) != 0o444
            or stat.S_IMODE(binary_path.stat().st_mode) != 0o555
            or stat.S_IMODE(trust_path.stat().st_mode) != 0o444):
        fail("desktop update generation file permissions are unsafe")
    manifest = snapshot_regular(manifest_path, MAX_MANIFEST_BYTES, "generation manifest")
    signature = snapshot_regular(signature_path, MAX_SIGNATURE_BYTES, "generation signature")
    binary = snapshot_regular(binary_path, MAX_BINARY_BYTES, "generation executable")
    trust_payload = snapshot_regular(trust_path, MAX_MANIFEST_BYTES, "generation trust record")
    document = strict_json_bytes(manifest, "generation manifest")
    validate_manifest(document, manifest)
    validate_elf(binary)
    if sha256(manifest) != identity or len(binary) != document["size"] or sha256(binary) != document["sha256"]:
        fail("desktop update generation differs from its authenticated manifest")
    trust = strict_json_bytes(trust_payload, "generation trust record")
    if (canonical(trust) != trust_payload or set(trust) != {
            "schemaVersion", "manifestSha256", "signatureSha256",
            "signerFingerprint", "keyringSha256", "policySha256"
            } or type(trust.get("schemaVersion")) is not int or trust["schemaVersion"] != 1):
        fail("desktop update generation trust record is malformed")
    policy, policy_payload, keyring = active_policy()
    signer = verify_signature(
        manifest, signature, keyring,
        {item["fingerprint"] for item in policy["signers"]},
    )
    expected_trust = {
        "schemaVersion": 1,
        "manifestSha256": sha256(manifest),
        "signatureSha256": sha256(signature),
        "signerFingerprint": signer,
        "keyringSha256": sha256(keyring),
        "policySha256": sha256(policy_payload),
    }
    if trust != expected_trust:
        fail("desktop update generation trust record differs from reviewed inputs",
             "desktop_update_authentication_failed")
    return generation, document, signer


def open_generation_executable(generation, document):
    path = generation / document["filename"]
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError:
        fail("active desktop executable could not be opened safely",
             "desktop_update_launch_failed")
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o555
                or info.st_size != document["size"]):
            fail("active desktop executable metadata changed before launch",
                 "desktop_update_launch_failed")
        digest = hashlib.sha256()
        header = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if len(header) < 64:
                header += chunk[:64 - len(header)]
            digest.update(chunk)
        if digest.hexdigest() != document["sha256"]:
            fail("active desktop executable changed before launch",
                 "desktop_update_launch_failed")
        validate_elf(header)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def sealed_execution_copy(source, document):
    if not hasattr(os, "memfd_create"):
        fail("Linux sealed-memory execution is unavailable", "desktop_update_launch_failed")
    close_on_exec = getattr(os, "MFD_CLOEXEC", 0x0001)
    allow_sealing = getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    try:
        destination = os.memfd_create(
            "opemos-recovery-status", close_on_exec | allow_sealing
        )
    except OSError:
        fail("Linux sealed-memory execution is unavailable", "desktop_update_launch_failed")
    try:
        os.lseek(source, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        header = b""
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            if len(header) < 64:
                header += chunk[:64 - len(header)]
            digest.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    fail("sealed desktop executable copy was incomplete",
                         "desktop_update_launch_failed")
                view = view[written:]
        if total != document["size"] or digest.hexdigest() != document["sha256"]:
            fail("active desktop executable changed during sealed launch staging",
                 "desktop_update_launch_failed")
        validate_elf(header)
        os.fchmod(destination, 0o555)
        seal_seal = getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        seals = (seal_seal
                 | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                 | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                 | getattr(fcntl, "F_SEAL_WRITE", 0x0008))
        add_seals = getattr(fcntl, "F_ADD_SEALS", 1033)
        get_seals = getattr(fcntl, "F_GET_SEALS", 1034)
        fcntl.fcntl(destination, add_seals, seals)
        if fcntl.fcntl(destination, get_seals) & seals != seals:
            fail("sealed desktop executable could not be made immutable",
                 "desktop_update_launch_failed")
        os.lseek(destination, 0, os.SEEK_SET)
        return destination
    except OSError:
        os.close(destination)
        fail("sealed desktop executable preparation failed",
             "desktop_update_launch_failed")
    except BaseException:
        os.close(destination)
        raise


def stage(args):
    require_runtime_architecture()
    manifest_payload = snapshot_regular(args.manifest, MAX_MANIFEST_BYTES, "desktop update manifest")
    signature_payload = snapshot_regular(args.signature, MAX_SIGNATURE_BYTES, "desktop update signature")
    binary_payload = snapshot_regular(args.binary, MAX_BINARY_BYTES, "desktop update executable")
    document = strict_json_bytes(manifest_payload, "desktop update manifest")
    validate_manifest(document, manifest_payload)
    validate_elf(binary_payload)
    if len(binary_payload) != document["size"] or sha256(binary_payload) != document["sha256"]:
        fail("desktop update executable differs from its manifest")
    policy, policy_payload, keyring = active_policy()
    allowed = {item["fingerprint"] for item in policy["signers"]}
    identity = sha256(manifest_payload)
    with update_lock(args.store, create=True):
        destination = args.store / "generations" / identity
        if destination.exists() or destination.is_symlink():
            verify_generation(args.store, identity)
            result("verified", "generation_already_staged", generation=identity,
                   version=document["version"])
            return
        temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=args.store / "generations"))
        try:
            write_file(temporary / "manifest.json", manifest_payload, 0o444)
            write_file(temporary / "manifest.json.sig", signature_payload, 0o444)
            write_file(temporary / "opemos-recovery-status", binary_payload, 0o555)
            signer = verify_signature(
                manifest_payload, signature_payload, keyring, allowed,
            )
            trust = {
                "schemaVersion": 1,
                "manifestSha256": sha256(manifest_payload),
                "signatureSha256": sha256(signature_payload),
                "signerFingerprint": signer,
                "keyringSha256": sha256(keyring),
                "policySha256": sha256(policy_payload),
            }
            write_file(temporary / "trust.json", canonical(trust), 0o444)
            fsync_directory(temporary)
            os.chmod(temporary, 0o555)
            os.replace(temporary, destination)
            fsync_directory(destination.parent)
            verify_generation(args.store, identity)
        finally:
            if temporary.exists():
                os.chmod(temporary, 0o700)
                shutil.rmtree(temporary)
        result("verified", "generation_staged", generation=identity,
               version=document["version"], signerFingerprint=signer)


def read_marker(store, name, optional=True):
    path = store / name
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        fail(f"desktop update {name} marker is missing")
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 65):
        fail(f"desktop update {name} marker has an unsafe type")
    try:
        identity = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        fail(f"desktop update {name} marker is unreadable")
    if not IDENTITY.fullmatch(identity):
        fail(f"desktop update {name} marker is invalid")
    return identity


def write_marker(store, name, identity):
    if not IDENTITY.fullmatch(identity):
        fail("desktop update marker identity is invalid")
    durable_write(store / name, (identity + "\n").encode("ascii"))


def read_pending(store):
    path = store / "pending.json"
    try:
        payload = snapshot_regular(path, MAX_MANIFEST_BYTES, "pending activation")
    except UpdateError as error:
        if not path.exists() and not path.is_symlink():
            return None
        raise error
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        fail("pending desktop activation state has unsafe permissions")
    document = strict_json_bytes(payload, "pending activation")
    if (canonical(document) != payload
            or set(document) != {"schemaVersion", "candidate", "previous", "activatedAt", "deadline"}
            or type(document.get("schemaVersion")) is not int
            or document.get("schemaVersion") != 1
            or not IDENTITY.fullmatch(document.get("candidate", ""))
            or (document.get("previous") is not None
                and not IDENTITY.fullmatch(document.get("previous", "")))
            or type(document.get("activatedAt")) is not int
            or type(document.get("deadline")) is not int
            or document["deadline"] <= document["activatedAt"]):
        fail("pending desktop activation state is malformed")
    return document


def activate(args):
    require_runtime_architecture()
    if not 5 <= args.timeout <= 300:
        fail("desktop update startup timeout must be between 5 and 300 seconds")
    with update_lock(args.store):
        _, document, _ = verify_generation(args.store, args.generation)
        if read_pending(args.store) is not None:
            fail("a desktop update activation is already pending", "desktop_update_state_conflict")
        current = read_marker(args.store, "current")
        if current == args.generation:
            fail("desktop update generation is already active", "desktop_update_state_conflict")
        last_good = read_marker(args.store, "last-known-good")
        if current is None:
            if not args.initial:
                fail("initial desktop activation requires --initial", "desktop_update_state_conflict")
            if last_good is not None:
                fail("initial desktop activation conflicts with retained healthy state",
                     "desktop_update_state_conflict")
        elif args.initial:
            fail("--initial is valid only when no desktop generation is active",
                 "desktop_update_state_conflict")
        if current is not None:
            _, current_document, _ = verify_generation(args.store, current)
            candidate_version = tuple(map(int, document["version"].split(".")))
            current_version = tuple(map(int, current_document["version"].split(".")))
            if candidate_version <= current_version:
                fail("desktop update activation must advance the installed version",
                     "desktop_update_version_not_newer")
        now = now_epoch()
        pending = {"schemaVersion": 1, "candidate": args.generation,
                   "previous": current, "activatedAt": now,
                   "deadline": now + args.timeout}
        durable_write(args.store / "pending.json", canonical(pending))
        write_marker(args.store, "current", args.generation)
        result("pending", "generation_activated_pending_health",
               generation=args.generation, previousGeneration=current,
               version=document["version"], healthDeadline=pending["deadline"])


def acknowledge(args):
    require_runtime_architecture()
    with update_lock(args.store):
        pending = read_pending(args.store)
        current = read_marker(args.store, "current", optional=False)
        if pending is None or pending["candidate"] != args.generation or current != args.generation:
            fail("desktop update health acknowledgement does not match the pending generation",
                 "desktop_update_state_conflict")
        if now_epoch() > pending["deadline"]:
            recover_locked(args.store, force=True)
            fail("desktop update health acknowledgement arrived after its deadline",
                 "desktop_update_health_timeout")
        verify_generation(args.store, current)
        write_marker(args.store, "last-known-good", current)
        durable_unlink(args.store / "pending.json")
        result("healthy", "generation_health_acknowledged", generation=current)


def recover_locked(store, force=False, now=None):
    pending = read_pending(store)
    if pending is None:
        return {"status": "stable", "reason": "no_pending_activation",
                "generation": read_marker(store, "current")}
    current = read_marker(store, "current")
    if current != pending["candidate"]:
        if current is not None:
            verify_generation(store, current)
        durable_unlink(store / "pending.json")
        return {"status": "recovered", "reason": "uncommitted_activation_discarded",
                "generation": current}
    last_good = read_marker(store, "last-known-good")
    try:
        verify_generation(store, current)
    except UpdateError:
        force = True
    if last_good == current:
        if not force:
            durable_unlink(store / "pending.json")
            return {"status": "healthy", "reason": "health_acknowledgement_finalized",
                    "generation": current}
    now = now_epoch() if now is None else now
    if not force and now <= pending["deadline"]:
        return {"status": "pending", "reason": "startup_health_pending",
                "generation": current, "healthDeadline": pending["deadline"]}
    previous = pending["previous"]
    if previous is None:
        durable_unlink(store / "current")
    else:
        verify_generation(store, previous)
        write_marker(store, "current", previous)
        write_marker(store, "last-known-good", previous)
    durable_unlink(store / "pending.json")
    return {"status": "rolled-back", "reason": "startup_health_failed",
            "generation": previous, "failedGeneration": current}


def recover(args):
    require_runtime_architecture()
    with update_lock(args.store):
        document = recover_locked(args.store, force=args.force)
        result(document.pop("status"), document.pop("reason"), **document)


def resolve(args):
    require_runtime_architecture()
    with update_lock(args.store):
        recovery = recover_locked(args.store)
        identity = read_marker(args.store, "current", optional=False)
        generation, document, _ = verify_generation(args.store, identity)
        result("verified", "active_generation_resolved", generation=identity,
               version=document["version"], executable=str(generation / document["filename"]),
               recovery=recovery)


def launch(args):
    require_runtime_architecture()
    if platform.system() != "Linux":
        fail("desktop generation launch requires Linux procfs execution",
             "desktop_update_launch_failed")
    descriptor = None
    try:
        with update_lock(args.store):
            recover_locked(args.store)
            identity = read_marker(args.store, "current", optional=False)
            generation, document, _ = verify_generation(args.store, identity)
            source = open_generation_executable(generation, document)
            try:
                descriptor = sealed_execution_copy(source, document)
            finally:
                os.close(source)
            proc_path = f"/proc/self/fd/{descriptor}"
            if not Path(proc_path).exists():
                fail("Linux procfs is unavailable for race-free desktop launch",
                     "desktop_update_launch_failed")
            environment = dict(os.environ)
            for name in (
                "OPEMOS_DEVELOPMENT_TRUST_OVERRIDE", "OPEMOS_DESKTOP_UPDATE_POLICY",
                "OPEMOS_DESKTOP_UPDATE_KEYRING", "OPEMOS_TEST_ARCHITECTURE",
                "OPEMOS_TEST_GPGV", "OPEMOS_TEST_NOW", "OPEMOS_TEST_SUPPORT_REVISION",
            ):
                environment.pop(name, None)
            environment["OPEMOS_UPDATE_STORE"] = str(args.store)
            environment["OPEMOS_UPDATE_GENERATION"] = identity
            try:
                os.execve(proc_path, ["opemos-recovery-status"], environment)
            except OSError:
                fail("sealed desktop executable could not be launched",
                     "desktop_update_launch_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def show_status(args):
    require_runtime_architecture()
    with update_lock(args.store):
        current = read_marker(args.store, "current")
        last_good = read_marker(args.store, "last-known-good")
        pending = read_pending(args.store)
        if current is not None:
            _, document, _ = verify_generation(args.store, current)
            version = document["version"]
        else:
            version = None
        if last_good is not None and last_good != current:
            verify_generation(args.store, last_good)
        if pending is not None and pending["previous"] is not None \
                and pending["previous"] not in (current, last_good):
            verify_generation(args.store, pending["previous"])
        result("pending" if pending else "stable", "desktop_update_status",
               generation=current, lastKnownGood=last_good,
               pendingGeneration=pending["candidate"] if pending else None,
               version=version)


def result(status, reason, **fields):
    document = {"schemaVersion": 1, "status": status, "reason": reason, **fields}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


def parser():
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--store", required=True, type=Path)
    stage_parser.add_argument("--manifest", required=True, type=Path)
    stage_parser.add_argument("--signature", required=True, type=Path)
    stage_parser.add_argument("--binary", required=True, type=Path)
    stage_parser.set_defaults(operation=stage)
    activate_parser = commands.add_parser("activate")
    activate_parser.add_argument("--store", required=True, type=Path)
    activate_parser.add_argument("--generation", required=True)
    activate_parser.add_argument("--timeout", type=int, default=90)
    activate_parser.add_argument("--initial", action="store_true")
    activate_parser.set_defaults(operation=activate)
    acknowledge_parser = commands.add_parser("acknowledge")
    acknowledge_parser.add_argument("--store", required=True, type=Path)
    acknowledge_parser.add_argument("--generation", required=True)
    acknowledge_parser.set_defaults(operation=acknowledge)
    recover_parser = commands.add_parser("recover")
    recover_parser.add_argument("--store", required=True, type=Path)
    recover_parser.add_argument("--force", action="store_true")
    recover_parser.set_defaults(operation=recover)
    resolve_parser = commands.add_parser("resolve")
    resolve_parser.add_argument("--store", required=True, type=Path)
    resolve_parser.set_defaults(operation=resolve)
    launch_parser = commands.add_parser("launch")
    launch_parser.add_argument("--store", required=True, type=Path)
    launch_parser.set_defaults(operation=launch)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--store", required=True, type=Path)
    status_parser.set_defaults(operation=show_status)
    return value


def main():
    def interrupted(_signum, _frame):
        raise UpdateCancelled
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    args = parser().parse_args()
    try:
        args.operation(args)
    except UpdateCancelled:
        result("cancelled", "desktop_update_cancelled")
        return 130
    except UpdateError as error:
        result("failed", error.reason, message=str(error)[:512])
        return 1
    except (OSError, ValueError, subprocess.SubprocessError):
        result("failed", "desktop_update_failed",
               message="The desktop update lifecycle encountered a bounded local operation failure.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
