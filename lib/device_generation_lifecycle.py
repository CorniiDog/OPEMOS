#!/usr/bin/env python3
"""Inactive installed-device lifecycle for reviewed userspace-lock generations."""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from payload_receipt import verify_receipt
from device_generation_contract import (
    DeviceGenerationContractError,
    validate_health,
    validate_identity as validate_generation_identity,
    validate_result,
    validate_state as validate_state_document,
)
from userspace_lock_bootstrap_contract import (
    BootstrapContractError,
    parse_checkpoint,
    parse_policy,
)
from userspace_lock_generation_contract import (
    DISCOVERY_MAX_BYTES,
    DISCOVERY_FILENAME,
    MANIFEST_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_GENERATION_BYTES,
    MAX_GENERATION_STORAGE_BYTES,
    MAX_LINEAGE_GENERATIONS,
    MAX_OPENPGP_STATUS_BYTES,
    MAX_SEQUENCE,
    GenerationContractError,
    canonical,
    strict_json,
    validate_activation,
    validate_discovery,
    validate_openpgp_status,
    validate_pair,
    validate_target,
)
from userspace_lock_request_plan import (
    MAX_PLAN_BYTES,
    RequestPlanError,
    build_request_plan,
)
from userspace_lock_verifier_evidence import (
    VerifierEvidenceError,
    verify_generation_snapshots,
)


DEFAULT_STORE = Path("/var/lib/opemos/userspace-lock-generations")
DEFAULT_POLICY = Path("/etc/opemos/userspace-lock-generation-policy.json")
DEFAULT_KEYRING = Path("/etc/opemos/opemos-userspace-lock-generations.gpg")
DEFAULT_CHECKPOINT = Path("/etc/opemos/userspace-lock-bootstrap-checkpoint.json")
MAX_POLICY_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_HEALTH_EVIDENCE_BYTES = 64 * 1024
MAX_TARGET_OBSERVATION_BYTES = 64 * 1024
MAX_TRANSPORT_BYTES = 16 * 1024 * 1024
MAX_TRANSPORT_SECONDS = 300
MAX_GENERATIONS = 4
MAX_STORE_ENTRIES = 32
HASH = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")
KERNEL = re.compile(r"[A-Za-z0-9._+\-]{1,192}")
STATE_MARKER_FIELDS = {"schemaVersion", "revision", "stateSha256", "state"}
STATE_MARKERS = ("state-a.json", "state-b.json")
PENDING_ACTIVATION = "pending-activation.json"
HEALTH_ACKNOWLEDGEMENT = "health-acknowledgement.json"
PENDING_HEALTH_ACKNOWLEDGEMENT = "pending-health-acknowledgement.json"
PENDING_ACTIVATION_FIELDS = {
    "schemaVersion", "candidate", "priorRevision", "priorStateSha256",
}
HEALTH_ACKNOWLEDGEMENT_FIELDS = {
    "schemaVersion", "generation", "target", "receiptId",
}
STATE_TEMP_PREFIXES = tuple(
    f".{name}.tmp-" for name in (
        *STATE_MARKERS, PENDING_ACTIVATION, HEALTH_ACKNOWLEDGEMENT,
        PENDING_HEALTH_ACKNOWLEDGEMENT,
    )
)
MAX_STORE_ROOT_ENTRIES = 16
MAX_CACHE_TREE_NODES = MAX_FILES + 16
MAX_CACHE_TREE_BYTES = (
    MAX_GENERATION_STORAGE_BYTES + MAX_STATE_BYTES + MAX_TRANSPORT_BYTES
)
CACHE_SPACE_RESERVE_BYTES = 64 * 1024 * 1024
CACHE_INODE_RESERVE = 128


class DeviceGenerationError(Exception):
    """A bounded installed-device lifecycle failure."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class DeviceGenerationCancelled(Exception):
    """An explicit lifecycle cancellation."""


def fail(reason, message):
    raise DeviceGenerationError(reason, message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def absolute_path(value):
    return Path(os.path.abspath(os.fspath(value)))


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def directory_guard(path, expected_mode, label):
    try:
        info = path.lstat()
    except OSError:
        fail("device_generation_input_changed", f"{label} is unavailable")
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != expected_mode):
        fail("device_generation_input_changed", f"{label} metadata changed")
    return (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
        info.st_uid, stat.S_IMODE(info.st_mode),
    )


def require_directory_guards(guards):
    for path, mode, label, expected in guards:
        if directory_guard(path, mode, label) != expected:
            fail("device_generation_input_changed", f"{label} identity changed")


def trust_file_guard(path, label):
    try:
        info = path.lstat()
    except OSError:
        fail("device_generation_input_changed", f"{label} is unavailable")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("device_generation_input_changed", f"{label} is unsafe")
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, stat.S_IMODE(info.st_mode),
    )


def require_trust_guards(policy):
    for path, label, expected in policy["guards"]:
        if trust_file_guard(path, label) != expected:
            fail("device_generation_input_changed", f"{label} changed")


def snapshot_regular(path, maximum, label,
                     reason="device_generation_input_invalid",
                     changed_reason="device_generation_input_changed",
                     expected_owner=None, expected_mode=None):
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        fail(reason, f"{label} is missing or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= maximum
                or expected_owner is not None and before.st_uid != expected_owner
                or expected_mode is not None
                and stat.S_IMODE(before.st_mode) != expected_mode):
            fail(
                reason,
                f"{label} is not a bounded single-link file",
            )
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
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns, before.st_uid, before.st_gid,
            stat.S_IMODE(before.st_mode), before.st_nlink,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_ctime_ns, after.st_uid, after.st_gid,
            stat.S_IMODE(after.st_mode), after.st_nlink,
        )
        try:
            current = path.lstat()
        except OSError:
            fail(changed_reason, f"{label} changed while read")
        current_identity = (
            current.st_dev, current.st_ino, current.st_size,
            current.st_mtime_ns, current.st_ctime_ns, current.st_uid,
            current.st_gid, stat.S_IMODE(current.st_mode), current.st_nlink,
        )
        if (before_identity != after_identity
                or before_identity != current_identity
                or stat.S_ISLNK(current.st_mode)
                or len(payload) != before.st_size):
            fail(changed_reason, f"{label} changed while read")
        return payload
    finally:
        if descriptor is not None:
            os.close(descriptor)


def snapshot_trust_file(path, maximum, label):
    """Read a trust file and return identity from the same open descriptor."""
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        fail("device_generation_authentication_failed", f"{label} is unavailable")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= maximum):
            fail("device_generation_authentication_failed", f"{label} is unsafe")
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
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns, before.st_uid, stat.S_IMODE(before.st_mode),
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_ctime_ns, after.st_uid, stat.S_IMODE(after.st_mode),
        )
        try:
            current = path.lstat()
        except OSError:
            fail("device_generation_input_changed", f"{label} changed while read")
        current_identity = (
            current.st_dev, current.st_ino, current.st_size,
            current.st_mtime_ns, current.st_ctime_ns, current.st_uid,
            stat.S_IMODE(current.st_mode),
        )
        if (before_identity != after_identity
                or before_identity != current_identity
                or len(payload) != before.st_size
                or stat.S_ISLNK(current.st_mode)):
            fail("device_generation_input_changed", f"{label} changed while read")
        return payload, before_identity
    finally:
        if descriptor is not None:
            os.close(descriptor)


def durable_write(path, payload, mode=0o600):
    if path.parent.is_symlink() or not path.parent.is_dir():
        fail("device_generation_store_invalid", "state parent is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            fail("device_generation_space_insufficient", "state storage is full")
        raise
    finally:
        temporary.unlink(missing_ok=True)


def write_exclusive(path, payload, mode=0o400):
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), mode,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        if error.errno == errno.ENOSPC:
            fail("device_generation_space_insufficient", "generation storage is full")
        raise


def reject_symlink_components(path, label):
    current = path
    while current != current.parent:
        if current.is_symlink():
            fail("device_generation_path_unsafe", f"{label} contains a symlink")
        current = current.parent


def safe_store(store, create=False):
    reject_symlink_components(store, "device generation store")
    if store.is_symlink():
        fail("device_generation_store_invalid", "device generation store is a symlink")
    if create:
        store.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        info = store.lstat()
    except OSError:
        fail("device_generation_store_missing", "device generation store is unavailable")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        fail(
            "device_generation_store_invalid",
            "device generation store must be private and owned by the caller",
        )
    generations = store / "generations"
    if generations.is_symlink():
        fail("device_generation_store_invalid", "generation directory is a symlink")
    if create:
        generations.mkdir(mode=0o700, exist_ok=True)
    elif not generations.exists():
        fail("device_generation_store_missing", "generation directory is unavailable")
    info = generations.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        fail("device_generation_store_invalid", "generation directory is unsafe")
    validate_store_layout(store)
    return generations


def validate_store_layout(store):
    try:
        entries = list(store.iterdir())
    except OSError:
        fail("device_generation_store_invalid", "device generation store is unreadable")
    if len(entries) > MAX_STORE_ROOT_ENTRIES:
        fail("device_generation_store_excessive", "device generation store has too many entries")
    allowed = {
        "generations", ".generation.lock", "state.json", PENDING_ACTIVATION,
        HEALTH_ACKNOWLEDGEMENT, PENDING_HEALTH_ACKNOWLEDGEMENT,
        "downloads", *STATE_MARKERS,
    }
    for entry in entries:
        if entry.name in allowed or any(
                entry.name.startswith(prefix) for prefix in STATE_TEMP_PREFIXES):
            continue
        fail("device_generation_store_invalid", "device generation store layout is unsafe")


def cleanup_staging(generations):
    entries = list(generations.iterdir())
    if len(entries) > MAX_STORE_ENTRIES:
        fail("device_generation_store_excessive", "generation store has too many entries")
    for entry in entries:
        is_stage = entry.name.startswith(".stage-")
        is_acquisition = entry.name.startswith(".acquire-")
        is_prune = (entry.name.startswith(".prune-")
                    and HASH.fullmatch(entry.name[len(".prune-"):])
                    is not None)
        if not is_stage and not is_acquisition and not is_prune:
            continue
        try:
            info = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or entry.is_symlink():
            fail("device_generation_store_invalid", "abandoned staging entry is unsafe")
        remove_confined_generation_tree(entry)
    fsync_directory(generations)


def validate_removal_root_name(name):
    if (re.fullmatch(r"\.stage-[A-Za-z0-9_-]{1,64}", name) is None
            and re.fullmatch(r"\.acquire-[A-Za-z0-9_-]{1,64}", name) is None
            and re.fullmatch(r"\.prune-[0-9a-f]{64}", name) is None):
        fail("device_generation_store_invalid", "generation removal target is unsafe")


def remove_confined_generation_tree(root):
    """Remove the fixed two-level cache shape using directory descriptors."""
    validate_removal_root_name(root.name)
    parent_descriptor = None
    root_descriptor = None
    try:
        parent_descriptor = os.open(
            root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        root_descriptor = os.open(
            root.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_descriptor,
        )
        opened = os.fstat(root_descriptor)
        current = os.stat(root.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            fail("device_generation_store_invalid", "generation removal target changed")
        os.fchmod(root_descriptor, 0o700)
        names = os.listdir(root_descriptor)
        if len(names) + 1 > MAX_CACHE_TREE_NODES:
            fail("device_generation_store_excessive", "generation tree has too many nodes")
        node_count = 1
        logical_bytes = 0
        for name in names:
            info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if name != "payload":
                    fail("device_generation_store_invalid", "generation tree is too deep")
                child_descriptor = os.open(
                    name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_descriptor,
                )
                try:
                    child = os.fstat(child_descriptor)
                    if (child.st_dev, child.st_ino) != (info.st_dev, info.st_ino):
                        fail("device_generation_store_invalid", "payload directory changed")
                    if child.st_uid != os.geteuid():
                        fail("device_generation_store_invalid", "payload directory is unowned")
                    os.fchmod(child_descriptor, 0o700)
                    child_names = os.listdir(child_descriptor)
                    node_count += len(child_names) + 1
                    if node_count > MAX_CACHE_TREE_NODES:
                        fail("device_generation_store_excessive", "generation tree has too many nodes")
                    for child_name in child_names:
                        child_info = os.stat(
                            child_name, dir_fd=child_descriptor,
                            follow_symlinks=False,
                        )
                        if (not stat.S_ISREG(child_info.st_mode)
                                or child_info.st_uid != os.geteuid()
                                or child_info.st_nlink != 1):
                            fail("device_generation_store_invalid", "payload removal entry is unsafe")
                        logical_bytes += child_info.st_size
                        if logical_bytes > MAX_CACHE_TREE_BYTES:
                            fail("device_generation_store_excessive", "generation tree is too large")
                        os.unlink(child_name, dir_fd=child_descriptor)
                    os.fsync(child_descriptor)
                finally:
                    os.close(child_descriptor)
                os.rmdir(name, dir_fd=root_descriptor)
            elif (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                  and info.st_nlink == 1):
                logical_bytes += info.st_size
                if logical_bytes > MAX_CACHE_TREE_BYTES:
                    fail("device_generation_store_excessive", "generation tree is too large")
                os.unlink(name, dir_fd=root_descriptor)
            else:
                fail("device_generation_store_invalid", "generation removal entry is unsafe")
        os.fsync(root_descriptor)
        os.close(root_descriptor)
        root_descriptor = None
        os.rmdir(root.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        fail("device_generation_cleanup_failed", "generation cleanup failed")
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def remove_confined_transport_phase(root, expected_identity=None):
    """Remove bounded untrusted transport output without following links."""
    if re.fullmatch(r"\.transport-phase-[A-Za-z0-9_-]{1,64}", root.name) is None:
        fail("device_generation_store_invalid", "transport cleanup target is unsafe")
    parent_descriptor = root_descriptor = None
    try:
        parent_descriptor = os.open(
            root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        root_descriptor = os.open(
            root.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_descriptor,
        )
        opened = os.fstat(root_descriptor)
        current = os.stat(root.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or expected_identity is not None
                and (opened.st_dev, opened.st_ino, opened.st_uid)
                != expected_identity):
            fail("device_generation_store_invalid", "transport cleanup target changed")
        os.fchmod(root_descriptor, 0o700)
        node_count = 1
        logical_bytes = 0
        with os.scandir(root_descriptor) as entries:
            root_entries = []
            for entry in entries:
                node_count += 1
                if node_count > MAX_CACHE_TREE_NODES:
                    fail(
                        "device_generation_store_excessive",
                        "transport output has too many nodes",
                    )
                root_entries.append((entry.name, entry.stat(follow_symlinks=False)))
        for name, info in root_entries:
            if stat.S_ISDIR(info.st_mode):
                child_descriptor = os.open(
                    name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_descriptor,
                )
                try:
                    child = os.fstat(child_descriptor)
                    if ((child.st_dev, child.st_ino) != (info.st_dev, info.st_ino)
                            or child.st_uid != os.geteuid()):
                        fail(
                            "device_generation_store_invalid",
                            "transport cleanup directory changed",
                        )
                    os.fchmod(child_descriptor, 0o700)
                    with os.scandir(child_descriptor) as child_entries:
                        children = []
                        for child_entry in child_entries:
                            node_count += 1
                            if node_count > MAX_CACHE_TREE_NODES:
                                fail(
                                    "device_generation_store_excessive",
                                    "transport output has too many nodes",
                                )
                            children.append((
                                child_entry.name,
                                child_entry.stat(follow_symlinks=False),
                            ))
                    for child_name, child_info in children:
                        if stat.S_ISDIR(child_info.st_mode):
                            fail(
                                "device_generation_store_invalid",
                                "transport output is too deep",
                            )
                        if stat.S_ISREG(child_info.st_mode):
                            logical_bytes += child_info.st_size
                        if logical_bytes > MAX_CACHE_TREE_BYTES:
                            fail(
                                "device_generation_store_excessive",
                                "transport output is too large",
                            )
                        os.unlink(child_name, dir_fd=child_descriptor)
                    os.fsync(child_descriptor)
                finally:
                    os.close(child_descriptor)
                os.rmdir(name, dir_fd=root_descriptor)
            else:
                if stat.S_ISREG(info.st_mode):
                    logical_bytes += info.st_size
                if logical_bytes > MAX_CACHE_TREE_BYTES:
                    fail(
                        "device_generation_store_excessive",
                        "transport output is too large",
                    )
                os.unlink(name, dir_fd=root_descriptor)
        os.fsync(root_descriptor)
        os.close(root_descriptor)
        root_descriptor = None
        os.rmdir(root.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        fail("device_generation_cleanup_failed", "transport cleanup failed")
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


@contextmanager
def lifecycle_lock(store, create=False):
    generations = safe_store(store, create=create)
    lock_path = store / ".generation.lock"
    try:
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        fail("device_generation_lock_failed", "generation lifecycle lock is unsafe")
    with os.fdopen(descriptor, "a+b") as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            fail("device_generation_lock_failed", "generation lifecycle lock is unsafe")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("device_generation_busy", "another device generation operation is active")
        try:
            current = lock_path.lstat()
        except OSError:
            fail("device_generation_lock_failed", "generation lifecycle lock was replaced")
        opened = os.fstat(lock.fileno())
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("device_generation_lock_failed", "generation lifecycle lock was replaced")
        cleanup_state_temporaries(store)
        cleanup_staging(generations)
        downloads = download_cache(store)
        if downloads is not None:
            cleanup_staging(downloads)
            prune_generations(downloads, empty_state())
        ensure_state_marker(store, generations)
        reconcile_pending_activation(store, generations)
        reconcile_pending_health_acknowledgement(store)
        reconcile_state_markers(store, generations)
        try:
            yield generations
        except DeviceGenerationCancelled:
            cleanup_staging(generations)
            downloads = download_cache(store)
            if downloads is not None:
                cleanup_staging(downloads)
            reconcile_pending_activation(store, generations)
            reconcile_pending_health_acknowledgement(store)
            raise


def download_cache(store, create=False):
    path = store / "downloads"
    if not path.exists() and not path.is_symlink():
        if not create:
            return None
        path.mkdir(mode=0o700)
        fsync_directory(store)
    try:
        info = path.lstat()
    except OSError:
        fail("device_generation_store_invalid", "download cache is unavailable")
    if (not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        fail("device_generation_store_invalid", "download cache is unsafe")
    return path


def empty_state():
    return {
        "schemaVersion": 1,
        "channel": "reviewed-userspace-lock-generations",
        "active": None,
        "lastKnownGood": None,
        "highWaterSequence": 0,
        "healthPending": False,
    }


def validate_state(state):
    try:
        return validate_state_document(state)
    except DeviceGenerationContractError as error:
        fail("device_generation_state_invalid", str(error))


def read_legacy_state(store):
    path = store / "state.json"
    if not path.exists() and not path.is_symlink():
        return None
    payload = snapshot_regular(
        path, MAX_STATE_BYTES, "legacy device generation state",
        "device_generation_state_invalid", "device_generation_state_invalid",
        os.geteuid(), 0o600,
    )
    try:
        state = strict_json(payload, MAX_STATE_BYTES, "legacy device generation state")
    except GenerationContractError as error:
        fail("device_generation_state_invalid", str(error))
    return validate_state(state)


def read_state_marker(path):
    if not path.exists() and not path.is_symlink():
        return None
    payload = snapshot_regular(
        path, MAX_STATE_BYTES, "device generation state marker",
        "device_generation_state_invalid", "device_generation_state_invalid",
        os.geteuid(), 0o600,
    )
    try:
        marker = strict_json(payload, MAX_STATE_BYTES, "device generation state marker")
    except GenerationContractError as error:
        fail("device_generation_state_invalid", str(error))
    if (not isinstance(marker, dict) or set(marker) != STATE_MARKER_FIELDS
            or marker.get("schemaVersion") != 1
            or type(marker.get("revision")) is not int
            or not 1 <= marker["revision"] <= MAX_SEQUENCE
            or not isinstance(marker.get("stateSha256"), str)
            or HASH.fullmatch(marker["stateSha256"]) is None):
        fail("device_generation_state_invalid", "device generation state marker is invalid")
    state = validate_state(marker.get("state"))
    if marker["stateSha256"] != sha256(canonical(state)):
        fail("device_generation_state_invalid", "device generation state marker hash differs")
    return marker


def read_state_record(store):
    markers = [
        marker for marker in (
            read_state_marker(store / name) for name in STATE_MARKERS
        ) if marker is not None
    ]
    if markers:
        revisions = [marker["revision"] for marker in markers]
        if len(revisions) != len(set(revisions)):
            fail("device_generation_state_invalid", "device generation revisions are ambiguous")
        selected = max(markers, key=lambda marker: marker["revision"])
        legacy = read_legacy_state(store)
        if legacy is not None and legacy != selected["state"]:
            fail("device_generation_state_invalid", "legacy state conflicts with state markers")
        return selected["state"], selected["revision"]
    legacy = read_legacy_state(store)
    if legacy is not None:
        return legacy, 0
    generations = store / "generations"
    try:
        has_generations = any(generations.iterdir())
    except OSError:
        fail("device_generation_state_invalid", "generation directory is unreadable")
    if has_generations:
        fail("device_generation_state_invalid", "device generation state marker is missing")
    return empty_state(), 0


def read_state(store):
    return read_state_record(store)[0]


def validate_pending_activation(document):
    if (not isinstance(document, dict)
            or set(document) != PENDING_ACTIVATION_FIELDS
            or document.get("schemaVersion") != 1
            or type(document.get("priorRevision")) is not int
            or not 0 <= document["priorRevision"] <= MAX_SEQUENCE
            or not isinstance(document.get("priorStateSha256"), str)
            or HASH.fullmatch(document["priorStateSha256"]) is None):
        fail(
            "device_generation_state_reconciliation_required",
            "pending activation record is invalid",
        )
    try:
        validate_generation_identity(document.get("candidate"))
    except DeviceGenerationContractError as error:
        fail("device_generation_state_reconciliation_required", str(error))
    return document


def read_pending_activation(store):
    path = store / PENDING_ACTIVATION
    if not path.exists() and not path.is_symlink():
        return None
    payload = snapshot_regular(
        path, MAX_STATE_BYTES, "pending activation record",
        "device_generation_state_reconciliation_required",
        "device_generation_state_reconciliation_required",
        os.geteuid(), 0o600,
    )
    try:
        document = strict_json(payload, MAX_STATE_BYTES, "pending activation record")
    except GenerationContractError as error:
        fail("device_generation_state_reconciliation_required", str(error))
    return validate_pending_activation(document)


def write_pending_activation(store, state, revision, candidate):
    validate_state(state)
    try:
        validate_generation_identity(candidate)
    except DeviceGenerationContractError as error:
        fail("device_generation_state_invalid", str(error))
    if revision < 0 or candidate["sequence"] <= state["highWaterSequence"]:
        fail("device_generation_state_invalid", "activation transition is invalid")
    document = {
        "schemaVersion": 1,
        "candidate": candidate,
        "priorRevision": revision,
        "priorStateSha256": sha256(canonical(state)),
    }
    durable_write(store / PENDING_ACTIVATION, canonical(document))


def clear_pending_activation(store):
    path = store / PENDING_ACTIVATION
    if not path.exists() and not path.is_symlink():
        return
    read_pending_activation(store)
    parent_descriptor = descriptor = None
    try:
        parent_descriptor = os.open(
            store, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            PENDING_ACTIVATION,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        current = os.stat(
            PENDING_ACTIVATION, dir_fd=parent_descriptor, follow_symlinks=False,
        )
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            fail(
                "device_generation_state_reconciliation_required",
                "pending activation record changed",
            )
        os.unlink(PENDING_ACTIVATION, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        fail(
            "device_generation_state_reconciliation_required",
            "pending activation record could not be removed",
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def reconcile_pending_activation(store, generations):
    pending = read_pending_activation(store)
    if pending is None:
        return
    state, revision = read_state_record(store)
    candidate = pending["candidate"]
    identity = candidate["manifestSha256"]
    destination = generations / identity
    if (state["active"] == candidate
            and state["highWaterSequence"] >= candidate["sequence"]):
        manifest = verify_cached_generation(destination, identity)
        if manifest["sequence"] != candidate["sequence"]:
            fail(
                "device_generation_state_reconciliation_required",
                "committed activation identity differs from cached generation",
            )
        clear_pending_activation(store)
        return
    if (revision != pending["priorRevision"]
            or sha256(canonical(state)) != pending["priorStateSha256"]
            or candidate["sequence"] <= state["highWaterSequence"]):
        fail(
            "device_generation_state_reconciliation_required",
            "pending activation does not match durable state",
        )
    if destination.exists() or destination.is_symlink():
        manifest = verify_cached_generation(destination, identity)
        if manifest["sequence"] != candidate["sequence"]:
            fail(
                "device_generation_state_reconciliation_required",
                "pending activation identity differs from cached generation",
            )
        tombstone = generations / f".prune-{identity}"
        if tombstone.exists() or tombstone.is_symlink():
            fail(
                "device_generation_state_reconciliation_required",
                "pending activation cleanup target is unsafe",
            )
        os.rename(destination, tombstone)
        fsync_directory(generations)
        remove_confined_generation_tree(tombstone)
    clear_pending_activation(store)


def validate_health_acknowledgement(document, label):
    if (not isinstance(document, dict)
            or set(document) != HEALTH_ACKNOWLEDGEMENT_FIELDS
            or document.get("schemaVersion") != 1
            or not isinstance(document.get("receiptId"), str)
            or HASH.fullmatch(document["receiptId"]) is None):
        fail(
            "device_generation_state_reconciliation_required",
            f"{label} is invalid",
        )
    try:
        validate_generation_identity(document.get("generation"))
        validate_target(document.get("target"))
    except (DeviceGenerationContractError, GenerationContractError):
        fail(
            "device_generation_state_reconciliation_required",
            f"{label} is invalid",
        )
    return document


def read_health_acknowledgement(store, pending=False, optional=False):
    name = PENDING_HEALTH_ACKNOWLEDGEMENT if pending else HEALTH_ACKNOWLEDGEMENT
    path = store / name
    if not path.exists() and not path.is_symlink():
        if optional:
            return None
        fail(
            "device_generation_health_invalid",
            "target-bound health acknowledgement is unavailable",
        )
    payload = snapshot_regular(
        path, MAX_STATE_BYTES, "target-bound health acknowledgement",
        "device_generation_state_reconciliation_required",
        "device_generation_state_reconciliation_required",
        os.geteuid(), 0o600,
    )
    try:
        document = strict_json(
            payload, MAX_STATE_BYTES, "target-bound health acknowledgement"
        )
    except GenerationContractError:
        fail(
            "device_generation_state_reconciliation_required",
            "target-bound health acknowledgement is invalid",
        )
    return validate_health_acknowledgement(
        document, "target-bound health acknowledgement"
    )


def remove_pending_health_acknowledgement(store):
    path = store / PENDING_HEALTH_ACKNOWLEDGEMENT
    if not path.exists() and not path.is_symlink():
        return
    snapshot_regular(
        path, MAX_STATE_BYTES, "pending health acknowledgement",
        "device_generation_state_reconciliation_required",
        "device_generation_state_reconciliation_required",
        os.geteuid(), 0o600,
    )
    try:
        path.unlink()
        fsync_directory(store)
    except OSError:
        fail(
            "device_generation_state_reconciliation_required",
            "pending health acknowledgement could not be removed",
        )


def promote_pending_health_acknowledgement(store):
    pending = read_health_acknowledgement(store, pending=True)
    current = read_health_acknowledgement(store, optional=True)
    if current is not None and current == pending:
        remove_pending_health_acknowledgement(store)
        return
    try:
        os.replace(
            store / PENDING_HEALTH_ACKNOWLEDGEMENT,
            store / HEALTH_ACKNOWLEDGEMENT,
        )
        fsync_directory(store)
    except OSError:
        fail(
            "device_generation_state_reconciliation_required",
            "health acknowledgement could not be committed",
        )
    if read_health_acknowledgement(store) != pending:
        fail(
            "device_generation_state_reconciliation_required",
            "committed health acknowledgement differs from its intent",
        )


def write_pending_health_acknowledgement(store, state, revision, observation):
    if (state["active"] is None
            or not 0 <= revision < MAX_SEQUENCE):
        fail("device_generation_state_invalid", "health transition is invalid")
    document = {
        "schemaVersion": 1,
        "generation": dict(state["active"]),
        "target": dict(observation["target"]),
        "receiptId": observation["receiptId"],
    }
    validate_health_acknowledgement(document, "health acknowledgement intent")
    # The state revision and hash are encoded in the transaction envelope
    # so a same-generation acknowledgement cannot be confused across a crash.
    envelope = {
        **document,
        "priorRevision": revision,
        "priorStateSha256": sha256(canonical(state)),
    }
    durable_write(
        store / PENDING_HEALTH_ACKNOWLEDGEMENT, canonical(envelope)
    )


def reconcile_pending_health_acknowledgement(store):
    path = store / PENDING_HEALTH_ACKNOWLEDGEMENT
    if not path.exists() and not path.is_symlink():
        return
    payload = snapshot_regular(
        path, MAX_STATE_BYTES, "pending health acknowledgement",
        "device_generation_state_reconciliation_required",
        "device_generation_state_reconciliation_required",
        os.geteuid(), 0o600,
    )
    try:
        envelope = strict_json(
            payload, MAX_STATE_BYTES, "pending health acknowledgement"
        )
    except GenerationContractError:
        fail(
            "device_generation_state_reconciliation_required",
            "pending health acknowledgement is invalid",
        )
    fields = HEALTH_ACKNOWLEDGEMENT_FIELDS | {
        "priorRevision", "priorStateSha256",
    }
    if (not isinstance(envelope, dict) or set(envelope) != fields
            or type(envelope.get("priorRevision")) is not int
            or not 0 <= envelope["priorRevision"] < MAX_SEQUENCE
            or not isinstance(envelope.get("priorStateSha256"), str)
            or HASH.fullmatch(envelope["priorStateSha256"]) is None):
        fail(
            "device_generation_state_reconciliation_required",
            "pending health acknowledgement is invalid",
        )
    document = {
        field: envelope[field] for field in HEALTH_ACKNOWLEDGEMENT_FIELDS
    }
    validate_health_acknowledgement(document, "pending health acknowledgement")
    state, revision = read_state_record(store)
    if revision == envelope["priorRevision"]:
        if sha256(canonical(state)) != envelope["priorStateSha256"]:
            fail(
                "device_generation_state_reconciliation_required",
                "pending health acknowledgement differs from durable state",
            )
        remove_pending_health_acknowledgement(store)
        return
    if (revision != envelope["priorRevision"] + 1
            or state["healthPending"]
            or state["active"] != document["generation"]
            or state["lastKnownGood"] != document["generation"]):
        fail(
            "device_generation_state_reconciliation_required",
            "pending health acknowledgement has ambiguous durable state",
        )
    durable_write(store / PENDING_HEALTH_ACKNOWLEDGEMENT, canonical(document))
    promote_pending_health_acknowledgement(store)


def require_health_acknowledgement(store, generation, observation=None):
    document = read_health_acknowledgement(store, optional=generation is None)
    if generation is None:
        if document is not None:
            fail(
                "device_generation_health_invalid",
                "health acknowledgement exists without a last-known-good generation",
            )
        return
    if document["generation"] != generation:
        fail(
            "device_generation_health_invalid",
            "health acknowledgement does not bind the last-known-good generation",
        )
    if (observation is not None
            and (document["target"] != observation["target"]
                 or document["receiptId"] != observation["receiptId"])):
        fail(
            "device_generation_health_invalid",
            "health acknowledgement does not bind the current target and receipt",
        )


def require_state_health_acknowledgement(store, state):
    expected = state["lastKnownGood"] if state["healthPending"] else state["active"]
    require_health_acknowledgement(store, expected)


def cleanup_state_temporaries(store):
    for entry in list(store.iterdir()):
        if any(entry.name.startswith(prefix) for prefix in STATE_TEMP_PREFIXES):
            try:
                info = entry.lstat()
            except OSError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1):
                fail("device_generation_store_invalid", "state temporary is unsafe")
            entry.unlink()
    marker_paths = [
        store / name for name in STATE_MARKERS
        if (store / name).exists() or (store / name).is_symlink()
    ]
    if marker_paths:
        for path in marker_paths:
            read_state_marker(path)
        legacy = store / "state.json"
        if legacy.exists() or legacy.is_symlink():
            legacy.unlink()
    fsync_directory(store)


def ensure_state_marker(store, generations):
    if (any((store / name).exists() or (store / name).is_symlink()
            for name in STATE_MARKERS)
            or (store / "state.json").exists()
            or (store / "state.json").is_symlink()):
        return
    if any(generations.iterdir()):
        fail("device_generation_state_invalid", "device generation state marker is missing")
    write_state(store, empty_state())


def cached_generation_sequence(generation):
    identity = generation.name
    if HASH.fullmatch(identity) is None or generation.is_symlink() or not generation.is_dir():
        fail("device_generation_store_invalid", "generation store contains an unsafe entry")
    discovery_payload = snapshot_regular(
        generation / DISCOVERY_FILENAME, DISCOVERY_MAX_BYTES, "cached discovery",
        "device_generation_store_invalid", "device_generation_store_invalid",
    )
    try:
        discovery = strict_json(
            discovery_payload, DISCOVERY_MAX_BYTES, "cached discovery"
        )
        validate_discovery(discovery)
    except GenerationContractError as error:
        fail("device_generation_store_invalid", str(error))
    manifest_name = discovery["generation"]["manifestFilename"]
    manifest_payload = snapshot_regular(
        generation / manifest_name, MANIFEST_MAX_BYTES, "cached manifest",
        "device_generation_store_invalid", "device_generation_store_invalid",
    )
    try:
        manifest = strict_json(manifest_payload, MANIFEST_MAX_BYTES, "cached manifest")
        validate_pair(discovery, manifest)
    except GenerationContractError as error:
        fail("device_generation_store_invalid", str(error))
    if sha256(manifest_payload) != identity:
        fail("device_generation_store_invalid", "cached manifest identity changed")
    return manifest["sequence"]


def reconcile_state_markers(store, generations):
    state = read_state(store)
    cached_sequences = [
        cached_generation_sequence(entry) for entry in generations.iterdir()
    ]
    if cached_sequences and max(cached_sequences) > state["highWaterSequence"]:
        fail(
            "device_generation_state_reconciliation_required",
            "cached generation is newer than the durable high-water marker",
        )


def write_state(store, state):
    validate_state(state)
    _current, revision = read_state_record(store)
    if revision >= MAX_SEQUENCE:
        fail("device_generation_state_invalid", "device generation revision is exhausted")
    next_revision = revision + 1
    marker = {
        "schemaVersion": 1,
        "revision": next_revision,
        "stateSha256": sha256(canonical(state)),
        "state": state,
    }
    if (development_override()
            and state["active"] is not None
            and os.environ.get("OPEMOS_GENERATION_TEST_FAIL_PHASE")
            == "state-enospc"):
        fail("device_generation_space_insufficient", "state storage is full")
    name = STATE_MARKERS[(next_revision - 1) % len(STATE_MARKERS)]
    durable_write(store / name, canonical(marker))
    legacy = store / "state.json"
    if legacy.exists() or legacy.is_symlink():
        legacy.unlink()
        fsync_directory(store)


def development_override():
    return os.environ.get("OPEMOS_GENERATION_DEVELOPMENT_TRUST_OVERRIDE") == "1"


def trust_paths(arguments):
    policy = DEFAULT_POLICY
    keyring = DEFAULT_KEYRING
    checkpoint = DEFAULT_CHECKPOINT
    if development_override():
        if arguments.policy:
            policy = absolute_path(arguments.policy)
        if arguments.keyring:
            keyring = absolute_path(arguments.keyring)
        if arguments.checkpoint:
            checkpoint = absolute_path(arguments.checkpoint)
    elif arguments.policy or arguments.keyring or arguments.checkpoint:
        fail(
            "device_generation_authentication_failed",
            "caller-selected trust is permitted only in development mode",
        )
    return policy, keyring, checkpoint


def require_production_anchor(path, label):
    if development_override():
        return
    try:
        info = path.lstat()
    except OSError:
        fail("device_generation_authentication_unconfigured", f"{label} is unavailable")
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022):
        fail("device_generation_authentication_failed", f"{label} is unsafe")


def active_policy(arguments):
    policy_path, keyring_path, checkpoint_path = trust_paths(arguments)
    for path, label in (
            (policy_path, "generation trust policy"),
            (keyring_path, "generation keyring"),
            (checkpoint_path, "generation bootstrap checkpoint")):
        reject_symlink_components(path, label)
    require_production_anchor(policy_path, "generation trust policy")
    require_production_anchor(keyring_path, "generation keyring")
    require_production_anchor(checkpoint_path, "generation bootstrap checkpoint")
    policy_payload, policy_guard = snapshot_trust_file(
        policy_path, MAX_POLICY_BYTES, "generation trust policy"
    )
    keyring_payload, keyring_guard = snapshot_trust_file(
        keyring_path, MAX_KEYRING_BYTES, "generation keyring"
    )
    checkpoint_payload, checkpoint_guard = snapshot_trust_file(
        checkpoint_path, MAX_POLICY_BYTES, "generation bootstrap checkpoint"
    )
    try:
        bootstrap_policy = parse_policy(policy_payload)
        checkpoint_document = parse_checkpoint(checkpoint_payload, policy_payload)
    except BootstrapContractError as error:
        fail("device_generation_authentication_failed", str(error))
    policy_authority = bootstrap_policy["authority"]
    if (policy_authority["keyringFilename"] != keyring_path.name
            or policy_authority["keyringSha256"] != sha256(keyring_payload)):
        fail("device_generation_authentication_failed", "trust policy is unsupported")
    policy = {
        "keyringSha256": policy_authority["keyringSha256"],
        "signingKeyFingerprint": policy_authority[
            "primarySigningFingerprint"
        ],
        "bootstrap": bootstrap_policy,
        "payload": policy_payload,
        "guards": (
            (policy_path, "generation trust policy", policy_guard),
            (keyring_path, "generation keyring", keyring_guard),
            (checkpoint_path, "generation bootstrap checkpoint", checkpoint_guard),
        ),
    }
    authority = {
        "policyId": bootstrap_policy["policyId"],
        "policySchemaVersion": bootstrap_policy["policySchemaVersion"],
        "policySha256": sha256(policy_payload),
        "keyringFilename": policy_authority["keyringFilename"],
        "keyringSha256": policy_authority["keyringSha256"],
        "signingKeyFingerprint": policy_authority[
            "primarySigningFingerprint"
        ],
    }
    checkpoint = {
        "sequence": checkpoint_document["minimumSequence"],
        "manifestSha256": checkpoint_document["minimumManifestSha256"],
    }
    return policy, authority, keyring_payload, checkpoint


def terminate_process(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
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


def verify_signature(document, signature, keyring, fingerprint):
    with tempfile.TemporaryDirectory(prefix="opemos-device-generation-trust-") as name:
        root = Path(name)
        document_path = root / "document.json"
        signature_path = root / "document.json.sig"
        keyring_path = root / "keyring.gpg"
        for path, payload in (
                (document_path, document), (signature_path, signature),
                (keyring_path, keyring)):
            write_exclusive(path, payload)
        command = "/usr/bin/gpgv"
        if development_override():
            command = os.environ.get("OPEMOS_GENERATION_TEST_GPGV", command)
        try:
            process = subprocess.Popen(
                [command, "--status-fd", "1", "--keyring", str(keyring_path),
                 str(signature_path), str(document_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError:
            fail(
                "device_generation_authentication_failed",
                "signature verifier could not be launched",
            )
        selector = selectors.DefaultSelector()
        output = bytearray()
        deadline = time.monotonic() + 60
        try:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    terminate_process(process)
                    fail(
                        "device_generation_authentication_failed",
                        "signature verification timed out",
                    )
                for key, _events in selector.select(min(remaining, 1.0)):
                    chunk = os.read(key.fileobj.fileno(), 16 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > MAX_OPENPGP_STATUS_BYTES:
                        terminate_process(process)
                        fail(
                            "device_generation_authentication_failed",
                            "signature verifier output is excessive",
                        )
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            terminate_process(process)
            fail("device_generation_authentication_failed", "signature verification timed out")
        except BaseException:
            terminate_process(process)
            raise
        finally:
            selector.close()
    if process.returncode:
        fail("device_generation_authentication_failed", "signature is invalid")
    try:
        verified = validate_openpgp_status(bytes(output), fingerprint)
    except GenerationContractError as error:
        fail("device_generation_authentication_failed", str(error))
    return {"exitStatus": process.returncode, "status": bytes(output), **verified}


def load_authenticated_pair(source, keyring, signer, cached=False, lineage=False):
    if cached and lineage:
        fail("device_generation_input_invalid", "generation source role is invalid")
    reject_symlink_components(source, "generation source")
    if source.is_symlink() or not source.is_dir():
        fail("device_generation_input_invalid", "generation source is unsafe")
    discovery = snapshot_regular(
        source / DISCOVERY_FILENAME, DISCOVERY_MAX_BYTES, "discovery descriptor"
    )
    discovery_signature = snapshot_regular(
        source / f"{DISCOVERY_FILENAME}.sig", MAX_SIGNATURE_BYTES,
        "discovery signature",
    )
    verify_signature(discovery, discovery_signature, keyring, signer)
    try:
        discovery_document = strict_json(
            discovery, DISCOVERY_MAX_BYTES, "discovery descriptor"
        )
        validate_discovery(discovery_document)
    except GenerationContractError as error:
        fail("device_generation_contract_invalid", str(error))
    generation = discovery_document["generation"]
    manifest_name = generation["manifestFilename"]
    signature_name = generation["signatureFilename"]
    try:
        names = {entry.name for entry in source.iterdir()}
    except OSError:
        fail("device_generation_input_invalid", "generation source is unreadable")
    expected_names = {
        DISCOVERY_FILENAME, f"{DISCOVERY_FILENAME}.sig", manifest_name,
        signature_name,
    }
    if not lineage:
        expected_names.add("payload")
        if cached:
            expected_names.add("trust.json")
    if names != expected_names:
        fail("device_generation_input_invalid", "generation source layout is ambiguous")
    manifest = snapshot_regular(
        source / manifest_name, MANIFEST_MAX_BYTES, "generation manifest"
    )
    manifest_signature = snapshot_regular(
        source / signature_name, MAX_SIGNATURE_BYTES, "manifest signature"
    )
    verify_signature(manifest, manifest_signature, keyring, signer)
    try:
        manifest_document = strict_json(
            manifest, MANIFEST_MAX_BYTES, "generation manifest"
        )
        validate_pair(discovery_document, manifest_document)
    except GenerationContractError as error:
        fail("device_generation_contract_invalid", str(error))
    if (generation.get("signatureSize") != len(manifest_signature)
            or generation.get("signatureSha256") != sha256(manifest_signature)):
        fail(
            "device_generation_authentication_failed",
            "manifest signature differs from discovery",
        )
    return {
        "discovery": discovery_document,
        "manifest": manifest_document,
        "discoveryPayload": discovery,
        "discoverySignature": discovery_signature,
        "manifestPayload": manifest,
        "manifestSignature": manifest_signature,
        "manifestFilename": manifest_name,
        "signatureFilename": signature_name,
    }


def target_from_arguments(arguments):
    return {
        "steamosVersion": arguments.steamos,
        "kernelVersion": arguments.kernel,
        "nvidiaVersion": arguments.nvidia,
        "architecture": arguments.architecture,
    }


def require_pair_authorization(pair, authority, requested_target):
    if (pair["manifest"]["authority"] != authority
            or not any(record["target"] == requested_target
                       for record in pair["manifest"]["targetLocks"])):
        fail(
            "device_generation_not_authorized",
            "generation does not authorize the requested target or authority",
        )


def require_pair_authority(pair, authority):
    if pair["manifest"]["authority"] != authority:
        fail(
            "device_generation_authentication_failed",
            "cached generation authority differs from installed policy",
        )


def observation_root(arguments):
    value = arguments.target_root
    if value is not None and not development_override():
        fail(
            "device_generation_target_observation_invalid",
            "caller-selected observation roots are permitted only in development mode",
        )
    root = absolute_path(value) if value is not None else Path("/")
    try:
        reject_symlink_components(root, "target observation root")
    except DeviceGenerationError:
        fail(
            "device_generation_target_observation_invalid",
            "target observation root contains a symlink",
        )
    try:
        info = root.lstat()
    except OSError:
        fail(
            "device_generation_target_observation_invalid",
            "target observation root is unavailable",
        )
    expected_owner = os.geteuid() if value is not None else 0
    if (not stat.S_ISDIR(info.st_mode) or root.is_symlink()
            or info.st_uid != expected_owner or stat.S_IMODE(info.st_mode) & 0o022):
        fail(
            "device_generation_target_observation_invalid",
            "target observation root is unsafe",
        )
    return root, (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
        info.st_uid, stat.S_IMODE(info.st_mode),
    )


def require_observation_root(root, expected):
    try:
        info = root.lstat()
    except OSError:
        fail(
            "device_generation_target_observation_changed",
            "target observation root became unavailable",
        )
    actual = (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
        info.st_uid, stat.S_IMODE(info.st_mode),
    )
    if stat.S_ISLNK(info.st_mode) or actual != expected:
        fail(
            "device_generation_target_observation_changed",
            "target observation root identity changed",
        )


def observation_text(root, relative, label, optional=False):
    path = root / relative
    try:
        reject_symlink_components(path, label)
    except DeviceGenerationError:
        fail(
            "device_generation_target_observation_invalid",
            f"{label} contains a symlink",
        )
    if optional and not path.exists() and not path.is_symlink():
        return None
    try:
        info = path.lstat()
    except OSError:
        fail(
            "device_generation_target_observation_invalid",
            f"{label} is unavailable",
        )
    expected_owner = os.geteuid() if root != Path("/") else 0
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_owner
            or stat.S_IMODE(info.st_mode) & 0o022):
        fail(
            "device_generation_target_observation_invalid",
            f"{label} is not an owner-controlled regular file",
        )
    payload = snapshot_regular(
        path, MAX_TARGET_OBSERVATION_BYTES, label,
        "device_generation_target_observation_invalid",
        "device_generation_target_observation_changed",
        expected_owner,
    )
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(
            "device_generation_target_observation_invalid",
            f"{label} is not UTF-8 text",
        )


def os_release_identity(payload):
    selected = {}
    for line in payload.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in {"ID", "VERSION_ID"}:
            continue
        if key in selected:
            fail(
                "device_generation_target_observation_invalid",
                f"target OS release contains duplicate {key}",
            )
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        elif ('"' in value or "'" in value
              or any(character.isspace() for character in value)):
            fail(
                "device_generation_target_observation_invalid",
                f"target OS release has malformed {key}",
            )
        selected[key] = value
    if (selected.get("ID") != "steamos"
            or VERSION.fullmatch(selected.get("VERSION_ID", "")) is None):
        fail(
            "device_generation_target_observation_invalid",
            "target OS identity is missing or malformed",
        )
    return selected["VERSION_ID"]


def observed_steamos(root):
    records = []
    canonical = root / "usr/lib/os-release"
    for relative in ("usr/lib/os-release", "etc/os-release"):
        path = root / relative
        if path.is_symlink():
            if relative != "etc/os-release" or not records:
                fail(
                    "device_generation_target_observation_invalid",
                    "target OS release path is unsafe",
                )
            try:
                resolved = path.resolve(strict=True)
                canonical_resolved = canonical.resolve(strict=True)
            except OSError:
                fail(
                    "device_generation_target_observation_invalid",
                    "target OS release link is invalid",
                )
            if resolved != canonical_resolved:
                fail(
                    "device_generation_target_observation_invalid",
                    "target OS release link is not canonical",
                )
            continue
        payload = observation_text(
            root, relative, "target OS release", optional=True
        )
        if payload is not None:
            records.append(os_release_identity(payload))
    if not records:
        fail(
            "device_generation_target_observation_invalid",
            "target OS release is unavailable",
        )
    if len(set(records)) != 1:
        fail(
            "device_generation_target_observation_invalid",
            "target OS release identities are ambiguous",
        )
    return records[0]


def observed_nvidia(root):
    records = []
    for relative in (
        "var/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
        "var/lib/open-gpu-kernel-modules-steamos-support/installed-nvidia.txt",
        "var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup/nvidia-version",
        "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
    ):
        payload = observation_text(
            root, relative, "installed NVIDIA identity", optional=True
        )
        if payload is None:
            continue
        value = payload.strip()
        if VERSION.fullmatch(value) is None:
            fail(
                "device_generation_target_observation_invalid",
                "installed NVIDIA identity is malformed",
            )
        records.append(value)
    if not records:
        fail(
            "device_generation_target_observation_invalid",
            "installed NVIDIA identity is unavailable",
        )
    if len(set(records)) != 1:
        fail(
            "device_generation_target_observation_invalid",
            "installed NVIDIA identities are ambiguous",
        )
    return records[0]


def observe_current_target(arguments):
    root, guard = observation_root(arguments)
    steamos = observed_steamos(root)
    nvidia = observed_nvidia(root)
    if arguments.target_root is None:
        kernel = os.uname().release
        architecture = os.uname().machine
    else:
        kernel = observation_text(
            root, "proc/sys/kernel/osrelease", "target kernel identity"
        ).strip()
        architecture = observation_text(
            root, "proc/sys/kernel/architecture", "target architecture"
        ).strip()
    if KERNEL.fullmatch(kernel) is None or architecture != "x86_64":
        fail(
            "device_generation_target_observation_invalid",
            "target kernel or architecture identity is malformed or unsupported",
        )
    observed = {
        "steamosVersion": steamos,
        "kernelVersion": kernel,
        "nvidiaVersion": nvidia,
        "architecture": architecture,
    }
    try:
        receipt = verify_receipt(root, allow_live_root=True)
    except (OSError, UnicodeError, ValueError):
        fail(
            "device_generation_target_observation_invalid",
            "current rootfs payload receipt is unavailable or invalid",
        )
    if receipt["target"] != observed:
        fail(
            "device_generation_target_mismatch",
            "current target differs from its rootfs payload receipt",
        )
    require_observation_root(root, guard)
    return {"target": observed, "receiptId": receipt["receiptId"]}


def require_observed_target(pair, observed):
    if not any(
            record["target"] == observed["target"]
            for record in pair["manifest"]["targetLocks"]):
        fail(
            "device_generation_target_mismatch",
            "current installed target is not authorized by the selected generation",
        )


def require_observation_unchanged(arguments, expected):
    if observe_current_target(arguments) != expected:
        fail(
            "device_generation_target_observation_changed",
            "current installed target changed during the lifecycle operation",
        )


def load_lineage_paths(paths, keyring, signer):
    lineage_pairs = []
    for lineage_path in paths:
        item = load_authenticated_pair(
            absolute_path(lineage_path), keyring, signer, lineage=True,
        )
        lineage_pairs.append((item["discovery"], item["manifest"]))
    return lineage_pairs


def load_cached_lineage(downloads, identities, keyring, signer):
    if len(identities) > MAX_LINEAGE_GENERATIONS:
        fail("device_generation_input_invalid", "generation lineage is excessive")
    if len(identities) != len(set(identities)):
        fail("device_generation_input_invalid", "generation lineage is ambiguous")
    lineage_pairs = []
    for identity in identities:
        if HASH.fullmatch(identity or "") is None:
            fail("device_generation_input_invalid", "generation lineage identity is invalid")
        source = downloads / identity
        verify_cached_generation(source, identity)
        item = load_authenticated_pair(source, keyring, signer, cached=True)
        lineage_pairs.append((item["discovery"], item["manifest"]))
    return lineage_pairs


def source_payload_records(source, manifest):
    payload_root = source / "payload"
    reject_symlink_components(payload_root, "generation payload")
    if payload_root.is_symlink() or not payload_root.is_dir():
        fail("device_generation_input_invalid", "generation payload directory is unsafe")
    expected = {record["filename"] for record in manifest["files"]}
    try:
        entries = list(payload_root.iterdir())
    except OSError:
        fail("device_generation_input_invalid", "generation payload is unreadable")
    if len(entries) > len(expected) or {entry.name for entry in entries} != expected:
        fail("device_generation_input_invalid", "generation payload set differs from manifest")
    total = sum(record["size"] for record in manifest["files"])
    if total > MAX_GENERATION_BYTES:
        fail("device_generation_input_invalid", "generation payload is excessive")
    return payload_root, sorted(manifest["files"], key=lambda item: item["filename"])


def copy_verified_payload(source, destination, record):
    source_descriptor = None
    destination_descriptor = None
    try:
        source_descriptor = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(source_descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != record["size"]
                or before.st_size > MAX_FILE_BYTES):
            fail("device_generation_input_invalid", "generation payload metadata is invalid")
        destination_descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o400,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    fail("device_generation_io_failed", "payload copy made no progress")
                view = view[written:]
            digest.update(chunk)
            copied += len(chunk)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        )
        if (before_identity != after_identity or copied != record["size"]
                or digest.hexdigest() != record["sha256"]):
            fail("device_generation_input_changed", "generation payload changed while copied")
    except OSError as error:
        if error.errno == errno.ENOSPC:
            fail("device_generation_space_insufficient", "generation storage is full")
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)


def verify_regular_hash(path, size, expected_hash, label):
    descriptor = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o400
                or before.st_size != size or size > MAX_FILE_BYTES):
            fail("device_generation_store_invalid", f"{label} metadata changed")
        digest = hashlib.sha256()
        read_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read_bytes += len(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        )
        if (before_identity != after_identity or read_bytes != size
                or digest.hexdigest() != expected_hash):
            fail("device_generation_store_invalid", f"{label} changed")
    except OSError:
        fail("device_generation_store_invalid", f"{label} is unreadable")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def seal_generation(path):
    for current, directories, files in os.walk(path, topdown=False, followlinks=False):
        for filename in files:
            os.chmod(Path(current) / filename, 0o400, follow_symlinks=False)
        for directory in directories:
            os.chmod(Path(current) / directory, 0o500, follow_symlinks=False)
    os.chmod(path, 0o500, follow_symlinks=False)


def publish_generation(generations, pair, payload_root, payload_records,
                       policy, authority):
    require_trust_guards(policy)
    identity = pair["discovery"]["generation"]["manifestSha256"]
    destination = generations / identity
    if destination.exists() or destination.is_symlink():
        verify_cached_generation(destination, identity)
        require_trust_guards(policy)
        return destination, False
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=generations))
    try:
        (stage / "payload").mkdir(mode=0o700)
        if development_override() and os.environ.get(
                "OPEMOS_GENERATION_TEST_PAUSE_AFTER_STAGE"):
            time.sleep(float(os.environ["OPEMOS_GENERATION_TEST_PAUSE_AFTER_STAGE"]))
        write_exclusive(stage / DISCOVERY_FILENAME, pair["discoveryPayload"])
        write_exclusive(
            stage / f"{DISCOVERY_FILENAME}.sig", pair["discoverySignature"]
        )
        write_exclusive(stage / pair["manifestFilename"], pair["manifestPayload"])
        write_exclusive(
            stage / pair["signatureFilename"], pair["manifestSignature"]
        )
        for record in payload_records:
            if (development_override()
                    and os.environ.get("OPEMOS_GENERATION_TEST_FAIL_PHASE") == "copy-enospc"):
                fail("device_generation_space_insufficient", "generation storage is full")
            copy_verified_payload(
                payload_root / record["filename"],
                stage / "payload" / record["filename"], record,
            )
        trust = generation_trust_record(policy, authority, pair)
        write_exclusive(stage / "trust.json", canonical(trust))
        seal_generation(stage)
        require_trust_guards(policy)
        os.replace(stage, destination)
        fsync_directory(generations)
        return destination, True
    finally:
        if stage.exists():
            remove_confined_generation_tree(stage)


def generation_trust_record(policy, authority, pair):
    return {
        "schemaVersion": 1,
        "policySha256": authority["policySha256"],
        "keyringSha256": policy["keyringSha256"],
        "signerFingerprint": policy["signingKeyFingerprint"],
        "discoverySignatureSha256": sha256(pair["discoverySignature"]),
        "manifestSignatureSha256": sha256(pair["manifestSignature"]),
    }


def test_admission_value(name, actual):
    if not development_override() or name not in os.environ:
        return actual
    value = os.environ[name]
    if re.fullmatch(r"[0-9]{1,20}", value) is None:
        fail("device_generation_admission_unavailable", "cache admission override is invalid")
    return int(value)


def require_cache_admission(generations, pair, payload_records, policy, authority):
    payload_bytes = sum(record["size"] for record in payload_records)
    metadata_bytes = sum(len(pair[field]) for field in (
        "discoveryPayload", "discoverySignature", "manifestPayload",
        "manifestSignature",
    )) + len(canonical(generation_trust_record(policy, authority, pair)))
    required_bytes = payload_bytes + metadata_bytes + CACHE_SPACE_RESERVE_BYTES
    required_inodes = len(payload_records) + 7 + CACHE_INODE_RESERVE
    try:
        filesystem = os.statvfs(generations)
    except OSError:
        fail("device_generation_admission_unavailable", "cache storage cannot be measured")
    if (filesystem.f_frsize <= 0 or filesystem.f_bavail < 0
            or filesystem.f_files > 0 and filesystem.f_favail < 0):
        fail("device_generation_admission_unavailable", "cache storage report is invalid")
    available_bytes = test_admission_value(
        "OPEMOS_GENERATION_TEST_AVAILABLE_BYTES",
        filesystem.f_bavail * filesystem.f_frsize,
    )
    available_inodes = test_admission_value(
        "OPEMOS_GENERATION_TEST_AVAILABLE_INODES",
        None if filesystem.f_files == 0 else filesystem.f_favail,
    )
    if (available_bytes < required_bytes
            or available_inodes is not None
            and available_inodes < required_inodes):
        fail(
            "device_generation_space_insufficient",
            "device cache lacks conservative bytes or inodes for the generation",
        )


def verify_cached_generation(generation, identity):
    if HASH.fullmatch(identity or "") is None or generation.name != identity:
        fail("device_generation_store_invalid", "cached generation identity is invalid")
    try:
        info = generation.lstat()
    except OSError:
        fail("device_generation_store_invalid", "cached generation is unreadable")
    if (not stat.S_ISDIR(info.st_mode) or generation.is_symlink()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o500):
        fail("device_generation_store_invalid", "cached generation layout is unsafe")
    discovery_payload = snapshot_regular(
        generation / DISCOVERY_FILENAME, DISCOVERY_MAX_BYTES, "cached discovery",
        "device_generation_store_invalid", "device_generation_store_invalid",
    )
    try:
        discovery = strict_json(
            discovery_payload, DISCOVERY_MAX_BYTES, "cached discovery"
        )
        validate_discovery(discovery)
    except GenerationContractError as error:
        fail("device_generation_store_invalid", str(error))
    manifest_name = discovery["generation"]["manifestFilename"]
    signature_name = discovery["generation"]["signatureFilename"]
    expected_names = {
        DISCOVERY_FILENAME, f"{DISCOVERY_FILENAME}.sig", manifest_name,
        signature_name, "payload", "trust.json",
    }
    try:
        names = {entry.name for entry in generation.iterdir()}
    except OSError:
        fail("device_generation_store_invalid", "cached generation is unreadable")
    if names != expected_names:
        fail("device_generation_store_invalid", "cached generation layout is unsafe")
    payload_info = (generation / "payload").lstat()
    if (not stat.S_ISDIR(payload_info.st_mode)
            or (generation / "payload").is_symlink()
            or payload_info.st_uid != os.geteuid()
            or stat.S_IMODE(payload_info.st_mode) != 0o500):
        fail("device_generation_store_invalid", "cached payload metadata is unsafe")
    for name in names - {"payload"}:
        path = generation / name
        item = path.lstat()
        if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
                or item.st_uid != os.geteuid()
                or stat.S_IMODE(item.st_mode) != 0o400):
            fail("device_generation_store_invalid", "cached file metadata is unsafe")
    manifest_payload = snapshot_regular(
        generation / manifest_name, MANIFEST_MAX_BYTES, "cached manifest",
        "device_generation_store_invalid", "device_generation_store_invalid",
    )
    if sha256(manifest_payload) != identity:
        fail("device_generation_store_invalid", "cached manifest identity changed")
    try:
        manifest = strict_json(manifest_payload, MANIFEST_MAX_BYTES, "cached manifest")
        validate_pair(discovery, manifest)
    except GenerationContractError as error:
        fail("device_generation_store_invalid", str(error))
    payload_root = generation / "payload"
    if payload_root.is_symlink() or not payload_root.is_dir():
        fail("device_generation_store_invalid", "cached payload directory is unsafe")
    entries = list(payload_root.iterdir())
    expected = {record["filename"] for record in manifest.get("files", [])}
    if {entry.name for entry in entries} != expected:
        fail("device_generation_store_invalid", "cached payload set changed")
    by_name = {record["filename"]: record for record in manifest["files"]}
    for name in sorted(expected):
        record = by_name[name]
        item = (payload_root / name).lstat()
        if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
                or item.st_uid != os.geteuid()
                or stat.S_IMODE(item.st_mode) != 0o400):
            fail("device_generation_store_invalid", "cached payload metadata changed")
        verify_regular_hash(
            payload_root / name, record["size"], record["sha256"],
            "cached payload file",
        )
    trust_payload = snapshot_regular(
        generation / "trust.json", MAX_STATE_BYTES, "cached trust record",
        "device_generation_store_invalid", "device_generation_store_invalid",
    )
    try:
        trust = strict_json(trust_payload, MAX_STATE_BYTES, "cached trust record")
    except GenerationContractError as error:
        fail("device_generation_store_invalid", str(error))
    if (not isinstance(trust, dict) or set(trust) != {
            "schemaVersion", "policySha256", "keyringSha256",
            "signerFingerprint", "discoverySignatureSha256",
            "manifestSignatureSha256",
            } or trust.get("schemaVersion") != 1
            or any(HASH.fullmatch(trust.get(field, "")) is None for field in (
                "policySha256", "keyringSha256", "discoverySignatureSha256",
                "manifestSignatureSha256",
            ))
            or FINGERPRINT.fullmatch(trust.get("signerFingerprint", "")) is None
            or trust["discoverySignatureSha256"] != sha256(snapshot_regular(
                generation / f"{DISCOVERY_FILENAME}.sig", MAX_SIGNATURE_BYTES,
                "cached discovery signature", "device_generation_store_invalid",
                "device_generation_store_invalid",
            ))
            or trust["manifestSignatureSha256"] != sha256(snapshot_regular(
                generation / signature_name, MAX_SIGNATURE_BYTES,
                "cached manifest signature", "device_generation_store_invalid",
                "device_generation_store_invalid",
            ))
            or trust["policySha256"] != discovery["authority"]["policySha256"]
            or trust["keyringSha256"] != discovery["authority"]["keyringSha256"]
            or trust["signerFingerprint"]
            != discovery["authority"]["signingKeyFingerprint"]):
        fail("device_generation_store_invalid", "cached trust record is invalid")
    return manifest


def commit_state_and_prune(store, generations, state):
    cancellation_after_commit = False
    try:
        write_state(store, state)
        if development_override() and os.environ.get(
                "OPEMOS_GENERATION_TEST_PAUSE_AFTER_STATE"):
            time.sleep(float(os.environ["OPEMOS_GENERATION_TEST_PAUSE_AFTER_STATE"]))
        removed = prune_generations(generations, state)
    except DeviceGenerationCancelled:
        if read_state(store) != state:
            raise
        cleanup_staging(generations)
        cancellation_after_commit = True
        removed = 0
    return removed, cancellation_after_commit


def prune_generations(generations, state, limit=MAX_GENERATIONS):
    if type(limit) is not int or not 2 <= limit <= MAX_GENERATIONS:
        fail("device_generation_store_invalid", "generation retention limit is invalid")
    protected = {
        identity["manifestSha256"] for identity in (
            state["active"], state["lastKnownGood"]
        ) if identity is not None
    }
    candidates = []
    for entry in generations.iterdir():
        if (entry.name.startswith(".stage-")
                or entry.name.startswith(".acquire-")):
            continue
        if HASH.fullmatch(entry.name) is None or entry.is_symlink() or not entry.is_dir():
            fail("device_generation_store_invalid", "generation store contains an unsafe entry")
        manifest = verify_cached_generation(entry, entry.name)
        candidates.append((manifest["sequence"], entry.name, entry))
    candidates.sort(reverse=True)
    retained = set(protected)
    for _sequence, identity, _entry in candidates:
        if len(retained) >= limit:
            break
        retained.add(identity)
    for _sequence, identity, entry in candidates:
        if identity in retained:
            continue
        tombstone = generations / f".prune-{identity}"
        if tombstone.exists() or tombstone.is_symlink():
            fail("device_generation_store_invalid", "generation tombstone is unsafe")
        os.rename(entry, tombstone)
        fsync_directory(generations)
        remove_confined_generation_tree(tombstone)
    fsync_directory(generations)
    return len(candidates) - sum(identity in retained for _, identity, _ in candidates)


def transport_timeout():
    value = os.environ.get("OPEMOS_GENERATION_TEST_TRANSPORT_TIMEOUT")
    if value is None:
        return MAX_TRANSPORT_SECONDS
    if (not development_override()
            or re.fullmatch(r"[1-9][0-9]{0,2}", value) is None
            or int(value) > MAX_TRANSPORT_SECONDS):
        fail("device_generation_transport_failed", "transport timeout is invalid")
    return int(value)


def terminate_transport_watchdog(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=4)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_injected_transport(arguments, destination, request_plan=None):
    if not development_override() or not arguments.transport:
        fail(
            "device_generation_network_inactive",
            "installed-device generation networking is not configured",
        )
    transport = absolute_path(arguments.transport)
    reject_symlink_components(transport, "generation transport")
    try:
        info = transport.lstat()
    except OSError:
        fail("device_generation_transport_unavailable", "generation transport is unavailable")
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or not stat.S_IMODE(info.st_mode) & 0o100):
        fail("device_generation_transport_unavailable", "generation transport is unsafe")
    executable_payload = snapshot_regular(
        transport, MAX_TRANSPORT_BYTES, "generation transport",
        "device_generation_transport_unavailable",
        "device_generation_transport_unavailable", os.geteuid(),
    )
    executable = destination / ".transport"
    write_exclusive(executable, executable_payload, 0o700)
    request_path = destination / ".request-plan.json"
    request_payload = None
    if request_plan is not None:
        request_payload = canonical(request_plan)
        if len(request_payload) > MAX_PLAN_BYTES:
            fail("device_generation_transport_failed", "transport request plan is excessive")
        write_exclusive(request_path, request_payload, 0o400)
    watchdog = Path(__file__).with_name("device_generation_transport_watchdog.py")
    control_read = control_write = None
    try:
        control_read, control_write = os.pipe()
        command = [
            sys.executable, str(watchdog), "--control-fd", str(control_read),
            "--transport", str(executable), "--destination", str(destination),
        ]
        if request_payload is not None:
            command.extend(["--request-plan", str(request_path)])
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            close_fds=True, pass_fds=(control_read,),
        )
    except OSError:
        if control_write is not None:
            os.close(control_write)
            control_write = None
        executable.unlink(missing_ok=True)
        fail("device_generation_transport_unavailable", "generation transport could not start")
    finally:
        if control_read is not None:
            os.close(control_read)
    try:
        process.wait(timeout=transport_timeout())
    except subprocess.TimeoutExpired:
        terminate_transport_watchdog(process)
        fail("device_generation_transport_unavailable", "generation transport timed out")
    except BaseException:
        terminate_transport_watchdog(process)
        raise
    finally:
        if control_write is not None:
            os.close(control_write)
        executable.unlink(missing_ok=True)
        if request_payload is not None:
            preserved = snapshot_regular(
                request_path, MAX_PLAN_BYTES, "transport request plan",
                "device_generation_transport_failed",
                "device_generation_transport_failed", os.geteuid(), 0o400,
            )
            if preserved != request_payload:
                fail(
                    "device_generation_transport_failed",
                    "transport request plan changed during acquisition",
                )
            request_path.unlink(missing_ok=True)
        fsync_directory(destination)
    if process.returncode == 69:
        fail("device_generation_transport_unavailable", "generation source is unavailable")
    if process.returncode == 73:
        fail("device_generation_space_insufficient", "download staging storage is full")
    if process.returncode:
        fail("device_generation_transport_failed", "generation transport failed")


def transport_request_plan(phase, requests):
    if (phase not in {"bootstrap", "manifest", "payload"}
            or not isinstance(requests, list)
            or not 1 <= len(requests) <= MAX_FILES):
        fail("device_generation_transport_failed", "transport request set is invalid")
    return {
        "schemaVersion": 1,
        "kind": "opemos-device-generation-transport-request",
        "phase": phase,
        "redirects": False,
        "requests": requests,
    }


def transport_record(role, filename, url, maximum_size, expected_size=None,
                     expected_sha256=None):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.query
            or parsed.fragment or not parsed.path.startswith("/")):
        fail("device_generation_transport_failed", "transport URL is invalid")
    record = {
        "assetRole": role,
        "filename": filename,
        "path": parsed.path,
        "url": url,
        "maximumSize": maximum_size,
    }
    if expected_size is not None:
        record.update({
            "expectedSize": expected_size,
            "expectedSha256": expected_sha256,
        })
    return record


def bounded_directory_names(path, maximum, label):
    names = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > maximum:
                    fail(
                        "device_generation_transport_output_invalid",
                        f"{label} contains too many entries",
                    )
    except OSError:
        fail(
            "device_generation_transport_output_invalid",
            f"{label} is unreadable",
        )
    return names


def private_directory_identity(path, label):
    try:
        info = path.lstat()
    except OSError:
        fail("device_generation_input_changed", f"{label} is unavailable")
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        fail("device_generation_input_changed", f"{label} is unsafe")
    return info.st_dev, info.st_ino, info.st_uid


def snapshot_transport_output(directory_descriptor, record):
    descriptor = None
    try:
        descriptor = os.open(
            record["filename"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0), dir_fd=directory_descriptor,
        )
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or not 1 <= before.st_size <= record["maximumSize"]):
            fail(
                "device_generation_transport_output_invalid",
                "transport output metadata differs from its request",
            )
        chunks = []
        remaining = record["maximumSize"] + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)
                or len(payload) != before.st_size):
            fail(
                "device_generation_transport_output_invalid",
                "transport output changed while read",
            )
        return payload
    except OSError:
        fail(
            "device_generation_transport_output_invalid",
            "transport output is missing or unsafe",
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)


def copy_transport_output(source_directory_descriptor,
                          destination_directory_descriptor, record):
    """Stream one exact transport response into private immutable staging."""
    source_descriptor = destination_descriptor = None
    try:
        source_descriptor = os.open(
            record["filename"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0), dir_fd=source_directory_descriptor,
        )
        before = os.fstat(source_descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or before.st_size != record["expectedSize"]
                or before.st_size > record["maximumSize"]):
            fail(
                "device_generation_transport_output_invalid",
                "transport output metadata differs from its request",
            )
        destination_descriptor = os.open(
            record["filename"], os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o400,
            dir_fd=destination_directory_descriptor,
        )
        os.fchmod(destination_descriptor, 0o400)
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    fail(
                        "device_generation_io_failed",
                        "transport output copy made no progress",
                    )
                view = view[written:]
            digest.update(chunk)
            copied += len(chunk)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (before_identity != after_identity
                or copied != record["expectedSize"]
                or digest.hexdigest() != record["expectedSha256"]):
            fail(
                "device_generation_transport_output_invalid",
                "transport output differs from its authenticated identity",
            )
    except OSError as error:
        if error.errno == errno.ENOSPC:
            fail(
                "device_generation_space_insufficient",
                "download staging storage is full",
            )
        fail(
            "device_generation_transport_output_invalid",
            "transport output could not be staged",
        )
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)


def fetch_transport_phase(arguments, acquisition, plan, output_root=None):
    acquisition_identity = private_directory_identity(
        acquisition, "download acquisition directory"
    )
    phase = Path(tempfile.mkdtemp(prefix=".transport-phase-", dir=acquisition))
    phase_info = phase.lstat()
    phase_identity = (phase_info.st_dev, phase_info.st_ino, phase_info.st_uid)
    phase_descriptor = None
    try:
        run_injected_transport(arguments, phase, plan)
        if private_directory_identity(
                acquisition, "download acquisition directory"
                ) != acquisition_identity:
            fail(
                "device_generation_input_changed",
                "download acquisition directory changed during transport",
            )
        current_phase = phase.lstat()
        if (not stat.S_ISDIR(current_phase.st_mode)
                or (current_phase.st_dev, current_phase.st_ino,
                    current_phase.st_uid) != phase_identity
                or stat.S_IMODE(current_phase.st_mode) != 0o700):
            fail(
                "device_generation_input_changed",
                "transport output directory changed during acquisition",
            )
        phase_descriptor = os.open(
            phase, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_phase = os.fstat(phase_descriptor)
        if ((opened_phase.st_dev, opened_phase.st_ino, opened_phase.st_uid)
                != phase_identity
                or stat.S_IMODE(opened_phase.st_mode) != 0o700):
            fail(
                "device_generation_input_changed",
                "transport output directory changed before inspection",
            )
        expected = {record["filename"]: record for record in plan["requests"]}
        names = bounded_directory_names(
            phase_descriptor, len(expected), "transport output"
        )
        if set(names) != set(expected) or len(names) != len(expected):
            fail(
                "device_generation_transport_output_invalid",
                "transport output set differs from the Core request plan",
            )
        payloads = {}
        output_descriptor = None
        if output_root is not None:
            try:
                output_root.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                fail(
                    "device_generation_transport_output_invalid",
                    "private payload staging is unavailable",
                )
            else:
                fail(
                    "device_generation_transport_output_invalid",
                    "transport modified private payload staging",
                )
            output_root.mkdir(mode=0o700)
            output_descriptor = os.open(
                output_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        for name in sorted(expected):
            record = expected[name]
            if output_root is not None:
                copy_transport_output(
                    phase_descriptor, output_descriptor, record
                )
                continue
            payload = snapshot_transport_output(phase_descriptor, record)
            if ("expectedSize" in record
                    and (len(payload) != record["expectedSize"]
                         or sha256(payload) != record["expectedSha256"])):
                fail(
                    "device_generation_transport_output_invalid",
                    "transport output differs from its authenticated identity",
                )
            payloads[name] = payload
        if output_descriptor is not None:
            os.fsync(output_descriptor)
            os.close(output_descriptor)
            output_descriptor = None
        return payloads
    finally:
        if "output_descriptor" in locals() and output_descriptor is not None:
            os.close(output_descriptor)
        if phase_descriptor is not None:
            os.close(phase_descriptor)
        try:
            phase.lstat()
        except FileNotFoundError:
            pass
        else:
            remove_confined_transport_phase(phase, phase_identity)


def acquire_planned_pair(arguments, acquisition, policy, authority, keyring):
    bootstrap = policy["bootstrap"]
    channel = bootstrap["channel"]
    origin = channel["origin"]
    discovery_path = channel["discoveryPath"]
    parent = discovery_path.rsplit("/", 1)[0]
    bootstrap_plan = transport_request_plan("bootstrap", [
        transport_record(
            "discovery", channel["discoveryFilename"],
            origin + discovery_path, DISCOVERY_MAX_BYTES,
        ),
        transport_record(
            "discovery-signature", channel["discoverySignatureFilename"],
            origin + parent + "/" + channel["discoverySignatureFilename"],
            MAX_SIGNATURE_BYTES,
        ),
    ])
    metadata = fetch_transport_phase(arguments, acquisition, bootstrap_plan)
    require_trust_guards(policy)
    discovery_payload = metadata[channel["discoveryFilename"]]
    discovery_signature = metadata[channel["discoverySignatureFilename"]]
    verify_signature(
        discovery_payload, discovery_signature, keyring,
        policy["signingKeyFingerprint"],
    )
    try:
        discovery = strict_json(
            discovery_payload, DISCOVERY_MAX_BYTES, "discovery descriptor"
        )
        validate_discovery(discovery)
    except GenerationContractError as error:
        fail("device_generation_contract_invalid", str(error))
    if discovery["authority"] != authority:
        fail(
            "device_generation_not_authorized",
            "generation discovery authority differs from bootstrap policy",
        )
    generation = discovery["generation"]
    release_root = (
        origin + channel["immutableReleasePathPrefix"]
        + generation["releaseTag"] + "/"
    )
    manifest_plan = transport_request_plan("manifest", [
        transport_record(
            "generation-manifest", generation["manifestFilename"],
            release_root + generation["manifestFilename"], MANIFEST_MAX_BYTES,
            generation["manifestSize"], generation["manifestSha256"],
        ),
        transport_record(
            "generation-manifest-signature", generation["signatureFilename"],
            release_root + generation["signatureFilename"], MAX_SIGNATURE_BYTES,
            generation["signatureSize"], generation["signatureSha256"],
        ),
    ])
    manifest_outputs = fetch_transport_phase(arguments, acquisition, manifest_plan)
    require_trust_guards(policy)
    manifest_payload = manifest_outputs[generation["manifestFilename"]]
    manifest_signature = manifest_outputs[generation["signatureFilename"]]

    def verifier(payload, signature, selected_keyring, _role):
        result = verify_signature(
            payload, signature, selected_keyring,
            policy["signingKeyFingerprint"],
        )
        return {"exitStatus": result["exitStatus"], "status": result["status"]}

    try:
        evidence = verify_generation_snapshots(
            policy["payload"], keyring, discovery_payload, discovery_signature,
            manifest_payload, manifest_signature, verifier,
        )
        plan = build_request_plan(
            policy["payload"], discovery_payload, discovery_signature,
            manifest_payload, manifest_signature, evidence,
        )
    except (VerifierEvidenceError, RequestPlanError) as error:
        fail("device_generation_authentication_failed", str(error))
    payload_requests = [
        {**record, "maximumSize": record["expectedSize"]}
        for record in plan["requests"] if record["requestKind"] == "payload"
    ]
    payload_plan = transport_request_plan("payload", payload_requests)
    payload_root = acquisition / "payload"
    fetch_transport_phase(
        arguments, acquisition, payload_plan, output_root=payload_root
    )
    require_trust_guards(policy)
    write_exclusive(acquisition / channel["discoveryFilename"], discovery_payload)
    write_exclusive(
        acquisition / channel["discoverySignatureFilename"], discovery_signature
    )
    write_exclusive(acquisition / generation["manifestFilename"], manifest_payload)
    write_exclusive(acquisition / generation["signatureFilename"], manifest_signature)
    return plan


def acquire(arguments):
    if not development_override() or not arguments.transport:
        fail(
            "device_generation_network_inactive",
            "installed-device generation networking is not configured",
        )
    store = absolute_path(arguments.store)
    policy, authority, keyring, checkpoint = active_policy(arguments)
    if not all((arguments.steamos, arguments.kernel, arguments.nvidia)):
        fail("device_generation_input_invalid", "exact target arguments are required")
    requested_target = target_from_arguments(arguments)
    with lifecycle_lock(store, create=True) as generations:
        state = read_state(store)
        downloads = download_cache(store, create=True)
        cleanup_staging(downloads)
        acquisition = Path(tempfile.mkdtemp(prefix=".acquire-", dir=downloads))
        try:
            acquire_planned_pair(
                arguments, acquisition, policy, authority, keyring
            )
            pair = load_authenticated_pair(
                acquisition, keyring, policy["signingKeyFingerprint"]
            )
            require_pair_authorization(pair, authority, requested_target)
            lineage_pairs = load_lineage_paths(
                arguments.lineage, keyring, policy["signingKeyFingerprint"]
            )
            try:
                validate_activation(
                    pair["discovery"], pair["manifest"], authority,
                    requested_target, state["highWaterSequence"],
                    None if state["active"] is None
                    else state["active"]["manifestSha256"],
                    lineage_pairs, checkpoint,
                    None if state["active"] is None else state["active"]["sequence"],
                )
            except GenerationContractError as error:
                fail("device_generation_not_authorized", str(error))
            payload_root, payload_records = source_payload_records(
                acquisition, pair["manifest"]
            )
            pruned_before = prune_generations(
                downloads, empty_state(), limit=MAX_GENERATIONS - 1
            )
            require_cache_admission(
                downloads, pair, payload_records, policy, authority
            )
            if (development_override()
                    and os.environ.get("OPEMOS_GENERATION_TEST_FAIL_PHASE")
                    == "acquisition-enospc"):
                fail(
                    "device_generation_space_insufficient",
                    "download cache storage is full",
                )
            require_trust_guards(policy)
            _generation, created = publish_generation(
                downloads, pair, payload_root, payload_records, policy, authority
            )
            pruned = prune_generations(downloads, empty_state())
        finally:
            if acquisition.exists():
                remove_confined_generation_tree(acquisition)
        return result(
            "ok", "downloaded", state=state,
            details={
                "generationCreated": created,
                "prunedGenerations": pruned_before + pruned,
            },
        )


def activate_loaded(store, generations, state, prior_revision, source, pair,
                    lineage_pairs, policy, authority, checkpoint,
                    requested_target, source_guards=()):
    require_state_health_acknowledgement(store, state)
    current_identity = pair["discovery"]["generation"]["manifestSha256"]
    if (state["active"] is not None
            and state["active"]["manifestSha256"] == current_identity):
        verify_cached_generation(generations / current_identity, current_identity)
        return result("ok", "already_active", state=state)
    if state["healthPending"]:
        fail(
            "device_generation_health_pending",
            "active generation requires health acknowledgement or rollback",
        )
    try:
        validate_activation(
            pair["discovery"], pair["manifest"], authority,
            requested_target, state["highWaterSequence"],
            None if state["active"] is None else state["active"]["manifestSha256"],
            lineage_pairs, checkpoint,
            None if state["active"] is None else state["active"]["sequence"],
        )
    except GenerationContractError as error:
        fail("device_generation_not_authorized", str(error))
    payload_root, payload_records = source_payload_records(source, pair["manifest"])
    require_directory_guards(source_guards)
    pruned_before = prune_generations(
        generations, state, limit=MAX_GENERATIONS - 1
    )
    require_cache_admission(generations, pair, payload_records, policy, authority)
    previous = state["active"]
    candidate = {
        "sequence": pair["manifest"]["sequence"],
        "manifestSha256": current_identity,
    }
    activated_state = {
        **state,
        "active": candidate,
        "lastKnownGood": previous if previous is not None else state["lastKnownGood"],
        "highWaterSequence": max(state["highWaterSequence"], candidate["sequence"]),
        "healthPending": candidate != state["lastKnownGood"],
    }
    write_pending_activation(store, state, prior_revision, candidate)
    try:
        destination, created = publish_generation(
            generations, pair, payload_root, payload_records, policy, authority
        )
        verify_cached_generation(destination, current_identity)
        require_directory_guards(source_guards)
        if (development_override() and os.environ.get(
                "OPEMOS_GENERATION_TEST_PAUSE_AFTER_PUBLISH")):
            time.sleep(float(os.environ["OPEMOS_GENERATION_TEST_PAUSE_AFTER_PUBLISH"]))
        pruned, cancellation_after_commit = commit_state_and_prune(
            store, generations, activated_state
        )
        clear_pending_activation(store)
    except DeviceGenerationCancelled:
        reconcile_pending_activation(store, generations)
        if read_state(store) != activated_state:
            raise
        cancellation_after_commit = True
        pruned = 0
    except (DeviceGenerationError, OSError):
        reconcile_pending_activation(store, generations)
        raise
    return result(
        "ok", "activated", state=activated_state,
        details={
            "generationCreated": created,
            "prunedGenerations": pruned_before + pruned,
            "cancellationAfterCommit": cancellation_after_commit,
        },
    )


def activate(arguments):
    store = absolute_path(arguments.store)
    source = absolute_path(arguments.source)
    policy, authority, keyring, checkpoint = active_policy(arguments)
    pair = load_authenticated_pair(source, keyring, policy["signingKeyFingerprint"])
    requested_target = target_from_arguments(arguments)
    require_pair_authorization(pair, authority, requested_target)
    lineage_pairs = load_lineage_paths(
        arguments.lineage, keyring, policy["signingKeyFingerprint"]
    )
    with lifecycle_lock(store, create=True) as generations:
        state, prior_revision = read_state_record(store)
        return activate_loaded(
            store, generations, state, prior_revision, source, pair,
            lineage_pairs, policy, authority, checkpoint, requested_target,
        )


def activate_downloaded(arguments):
    store = absolute_path(arguments.store)
    identity = arguments.manifest_sha256
    if HASH.fullmatch(identity or "") is None:
        fail("device_generation_input_invalid", "downloaded generation identity is invalid")
    policy, authority, keyring, checkpoint = active_policy(arguments)
    requested_target = target_from_arguments(arguments)
    with lifecycle_lock(store, create=False) as generations:
        downloads = download_cache(store)
        if downloads is None:
            fail("device_generation_input_invalid", "downloaded generation is unavailable")
        source = downloads / identity
        if not source.exists() and not source.is_symlink():
            fail("device_generation_input_invalid", "downloaded generation is unavailable")
        verify_cached_generation(source, identity)
        source_guards = (
            (downloads, 0o700, "device download cache",
             directory_guard(downloads, 0o700, "device download cache")),
            (source, 0o500, "downloaded generation",
             directory_guard(source, 0o500, "downloaded generation")),
        )
        pair = load_authenticated_pair(
            source, keyring, policy["signingKeyFingerprint"], cached=True
        )
        require_pair_authorization(pair, authority, requested_target)
        lineage_pairs = load_cached_lineage(
            downloads, arguments.lineage_manifest_sha256, keyring,
            policy["signingKeyFingerprint"],
        )
        require_directory_guards(source_guards)
        state, prior_revision = read_state_record(store)
        return activate_loaded(
            store, generations, state, prior_revision, source, pair,
            lineage_pairs, policy, authority, checkpoint, requested_target,
            source_guards,
        )


def validate_health_evidence(arguments, active):
    path = absolute_path(arguments.evidence)
    reject_symlink_components(path, "generation health evidence")
    require_production_anchor(path, "generation health evidence")
    payload = snapshot_regular(
        path, MAX_HEALTH_EVIDENCE_BYTES, "generation health evidence",
        "device_generation_health_invalid", "device_generation_health_invalid",
    )
    try:
        document = strict_json(
            payload, MAX_HEALTH_EVIDENCE_BYTES, "generation health evidence"
        )
    except GenerationContractError as error:
        fail("device_generation_health_invalid", str(error))
    try:
        validate_health(document, active)
    except DeviceGenerationContractError as error:
        fail(
            "device_generation_health_invalid",
            str(error),
        )


def acknowledge(arguments):
    store = absolute_path(arguments.store)
    policy, authority, keyring, _checkpoint = active_policy(arguments)
    with lifecycle_lock(store) as generations:
        state, prior_revision = read_state_record(store)
        if state["active"] is None:
            fail("device_generation_no_active", "no active generation exists")
        identity = state["active"]["manifestSha256"]
        generation = generations / identity
        verify_cached_generation(generation, identity)
        pair = load_authenticated_pair(
            generation, keyring, policy["signingKeyFingerprint"], cached=True
        )
        require_pair_authority(pair, authority)
        if pair["manifest"]["sequence"] != state["active"]["sequence"]:
            fail(
                "device_generation_state_invalid",
                "active generation sequence differs from its cached manifest",
            )
        observed_target = observe_current_target(arguments)
        require_observed_target(pair, observed_target)
        validate_health_evidence(arguments, state["active"])
        require_trust_guards(policy)
        require_observation_unchanged(arguments, observed_target)
        write_pending_health_acknowledgement(
            store, state, prior_revision, observed_target
        )
        if (development_override() and os.environ.get(
                "OPEMOS_GENERATION_TEST_PAUSE_AFTER_HEALTH_INTENT")):
            time.sleep(float(os.environ[
                "OPEMOS_GENERATION_TEST_PAUSE_AFTER_HEALTH_INTENT"
            ]))
        acknowledged_state = {
            **state,
            "lastKnownGood": dict(state["active"]),
            "healthPending": False,
        }
        try:
            require_trust_guards(policy)
            require_observation_unchanged(arguments, observed_target)
            pruned, cancellation_after_commit = commit_state_and_prune(
                store, generations, acknowledged_state
            )
            reconcile_pending_health_acknowledgement(store)
        except (DeviceGenerationCancelled, DeviceGenerationError, OSError):
            reconcile_pending_health_acknowledgement(store)
            raise
        return result(
            "ok", "health_acknowledged", state=acknowledged_state,
            details={
                "prunedGenerations": pruned,
                "cancellationAfterCommit": cancellation_after_commit,
            },
        )


def rollback(arguments):
    store = absolute_path(arguments.store)
    policy, authority, keyring, _checkpoint = active_policy(arguments)
    with lifecycle_lock(store) as generations:
        state = read_state(store)
        if state["lastKnownGood"] is None:
            fail("device_generation_no_rollback", "no last-known-good generation exists")
        identity = state["lastKnownGood"]["manifestSha256"]
        generation = generations / identity
        verify_cached_generation(generation, identity)
        pair = load_authenticated_pair(
            generation, keyring, policy["signingKeyFingerprint"], cached=True
        )
        require_pair_authority(pair, authority)
        observed_target = observe_current_target(arguments)
        require_observed_target(pair, observed_target)
        require_health_acknowledgement(
            store, state["lastKnownGood"], observed_target
        )
        require_trust_guards(policy)
        require_observation_unchanged(arguments, observed_target)
        state = {
            **state,
            "active": dict(state["lastKnownGood"]),
            "healthPending": False,
        }
        pruned, cancellation_after_commit = commit_state_and_prune(
            store, generations, state
        )
        return result(
            "ok", "rolled_back", state=state,
            details={
                "prunedGenerations": pruned,
                "cancellationAfterCommit": cancellation_after_commit,
            },
        )


def status(arguments, check=False):
    store = absolute_path(arguments.store)
    if check:
        policy, authority, keyring, _checkpoint = active_policy(arguments)
        with lifecycle_lock(store) as generations:
            state = read_state(store)
            for identity in (state["active"], state["lastKnownGood"]):
                if identity is not None:
                    verify_cached_generation(
                        generations / identity["manifestSha256"],
                        identity["manifestSha256"],
                    )
            if state["active"] is not None and not state["healthPending"]:
                identity = state["active"]["manifestSha256"]
                pair = load_authenticated_pair(
                    generations / identity, keyring,
                    policy["signingKeyFingerprint"], cached=True,
                )
                require_pair_authority(pair, authority)
                observed_target = observe_current_target(arguments)
                require_observed_target(pair, observed_target)
                require_health_acknowledgement(
                    store, state["active"], observed_target
                )
                require_trust_guards(policy)
                require_observation_unchanged(arguments, observed_target)
            return result("ok", "checked", state=state)
    safe_store(store, create=False)
    if (read_pending_activation(store) is not None
            or (store / PENDING_HEALTH_ACKNOWLEDGEMENT).exists()
            or (store / PENDING_HEALTH_ACKNOWLEDGEMENT).is_symlink()):
        fail(
            "device_generation_state_reconciliation_required",
            "an interrupted lifecycle transition requires locked reconciliation",
        )
    state = read_state(store)
    require_state_health_acknowledgement(store, state)
    return result("ok", "status", state=state)


def prune(arguments):
    store = absolute_path(arguments.store)
    with lifecycle_lock(store) as generations:
        state = read_state(store)
        removed = prune_generations(generations, state)
        return result(
            "ok", "pruned", state=state, details={"prunedGenerations": removed}
        )


def result(status_value, reason, state=None, message=None, details=None):
    document = {
        "schemaVersion": 1,
        "channel": "reviewed-userspace-lock-generations",
        "status": status_value,
        "reason": reason,
    }
    if message is not None:
        document["message"] = message[:512]
    if state is not None:
        document["state"] = state
    if details:
        document.update(details)
    return document


def emit(document):
    try:
        validate_result(document)
    except DeviceGenerationContractError:
        fail("device_generation_result_invalid", "device generation result is invalid")
    payload = canonical(document)
    if len(payload) > MAX_STATE_BYTES:
        fail("device_generation_result_excessive", "device generation result is excessive")
    sys.stdout.buffer.write(payload)


def parser():
    value = argparse.ArgumentParser(
        description="Inactive installed-device userspace-lock generation lifecycle"
    )
    value.add_argument("--store", default=str(DEFAULT_STORE))
    value.add_argument("--policy")
    value.add_argument("--keyring")
    value.add_argument("--checkpoint")
    value.add_argument("--target-root")
    commands = value.add_subparsers(dest="command", required=True)
    activate_parser = commands.add_parser("activate")
    activate_parser.add_argument("--source", required=True)
    activate_parser.add_argument("--lineage", action="append", default=[])
    activate_parser.add_argument("--steamos", required=True)
    activate_parser.add_argument("--kernel", required=True)
    activate_parser.add_argument("--nvidia", required=True)
    activate_parser.add_argument("--architecture", default="x86_64")
    downloaded_parser = commands.add_parser("activate-downloaded")
    downloaded_parser.add_argument("--manifest-sha256", required=True)
    downloaded_parser.add_argument(
        "--lineage-manifest-sha256", action="append", default=[]
    )
    downloaded_parser.add_argument("--steamos", required=True)
    downloaded_parser.add_argument("--kernel", required=True)
    downloaded_parser.add_argument("--nvidia", required=True)
    downloaded_parser.add_argument("--architecture", default="x86_64")
    commands.add_parser("status")
    commands.add_parser("check")
    acknowledge_parser = commands.add_parser("acknowledge-health")
    acknowledge_parser.add_argument("--evidence", required=True)
    commands.add_parser("rollback")
    commands.add_parser("prune")
    for name in ("update", "update-or-repair"):
        update_parser = commands.add_parser(name)
        update_parser.add_argument("--transport")
        update_parser.add_argument("--lineage", action="append", default=[])
        update_parser.add_argument("--steamos")
        update_parser.add_argument("--kernel")
        update_parser.add_argument("--nvidia")
        update_parser.add_argument("--architecture", default="x86_64")
    return value


def handle_signal(_signum, _frame):
    raise DeviceGenerationCancelled()


def main():
    arguments = parser().parse_args()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        if (arguments.target_root is not None
                and not development_override()):
            fail(
                "device_generation_target_observation_invalid",
                "caller-selected observation roots are permitted only in development mode",
            )
        if arguments.command == "activate":
            document = activate(arguments)
        elif arguments.command == "activate-downloaded":
            document = activate_downloaded(arguments)
        elif arguments.command == "status":
            document = status(arguments)
        elif arguments.command == "check":
            document = status(arguments, check=True)
        elif arguments.command == "acknowledge-health":
            document = acknowledge(arguments)
        elif arguments.command == "rollback":
            document = rollback(arguments)
        elif arguments.command == "prune":
            document = prune(arguments)
        else:
            document = acquire(arguments)
    except DeviceGenerationCancelled:
        emit(result("cancelled", "cancelled", message="operation was cancelled"))
        return 130
    except DeviceGenerationError as error:
        emit(result("failed", error.reason, message=str(error)))
        return 1
    except OSError as error:
        reason = "device_generation_space_insufficient" \
            if error.errno == errno.ENOSPC else "device_generation_io_failed"
        emit(result("failed", reason, message="device generation I/O failed"))
        return 1
    emit(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
