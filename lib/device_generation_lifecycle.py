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

from device_generation_contract import (
    DeviceGenerationContractError,
    validate_health,
    validate_identity as validate_generation_identity,
    validate_result,
    validate_state as validate_state_document,
)
from userspace_lock_generation_contract import (
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_GENERATION_BYTES,
    MAX_LINEAGE_GENERATIONS,
    MAX_SEQUENCE,
    GenerationContractError,
    canonical,
    strict_json,
    validate_activation,
    validate_discovery,
    validate_pair,
)


DEFAULT_STORE = Path("/var/lib/opemos/userspace-lock-generations")
DEFAULT_POLICY = Path("/etc/opemos/userspace-lock-generation-policy.json")
DEFAULT_KEYRING = Path("/etc/opemos/opemos-userspace-lock-generations.gpg")
DEFAULT_CHECKPOINT = Path("/etc/opemos/userspace-lock-bootstrap-checkpoint.json")
MAX_POLICY_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_SIGNATURE_STATUS_BYTES = 64 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_HEALTH_EVIDENCE_BYTES = 64 * 1024
MAX_GENERATIONS = 4
MAX_STORE_ENTRIES = 32
DISCOVERY_FILENAME = "opemos-userspace-lock-discovery-v1.json"
HASH = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})")
POLICY_FIELDS = {
    "schemaVersion", "status", "policyId", "policySchemaVersion",
    "keyringFilename", "keyringSha256", "signingKeyFingerprint",
}
CHECKPOINT_FIELDS = {
    "schemaVersion", "policySha256", "sequence", "manifestSha256",
}
STATE_MARKER_FIELDS = {"schemaVersion", "revision", "stateSha256", "state"}
STATE_MARKERS = ("state-a.json", "state-b.json")
PENDING_ACTIVATION = "pending-activation.json"
PENDING_ACTIVATION_FIELDS = {
    "schemaVersion", "candidate", "priorRevision", "priorStateSha256",
}
STATE_TEMP_PREFIXES = tuple(
    f".{name}.tmp-" for name in (*STATE_MARKERS, PENDING_ACTIVATION)
)
MAX_STORE_ROOT_ENTRIES = 16
MAX_CACHE_TREE_NODES = MAX_FILES + 16
MAX_CACHE_TREE_BYTES = (
    MAX_GENERATION_BYTES + DISCOVERY_MAX_BYTES + MANIFEST_MAX_BYTES
    + 2 * MAX_SIGNATURE_BYTES + MAX_STATE_BYTES
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
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        )
        if before_identity != after_identity or len(payload) != before.st_size:
            fail(changed_reason, f"{label} changed while read")
        return payload
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
        *STATE_MARKERS,
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
        is_prune = (entry.name.startswith(".prune-")
                    and HASH.fullmatch(entry.name[len(".prune-"):])
                    is not None)
        if not is_stage and not is_prune:
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
        ensure_state_marker(store, generations)
        reconcile_pending_activation(store, generations)
        reconcile_state_markers(store, generations)
        try:
            yield generations
        except DeviceGenerationCancelled:
            cleanup_staging(generations)
            reconcile_pending_activation(store, generations)
            raise


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
    policy_payload = snapshot_regular(
        policy_path, MAX_POLICY_BYTES, "trust policy",
        "device_generation_authentication_failed",
        "device_generation_authentication_failed",
    )
    keyring_payload = snapshot_regular(
        keyring_path, MAX_KEYRING_BYTES, "keyring",
        "device_generation_authentication_failed",
        "device_generation_authentication_failed",
    )
    checkpoint_payload = snapshot_regular(
        checkpoint_path, MAX_POLICY_BYTES, "bootstrap checkpoint",
        "device_generation_authentication_failed",
        "device_generation_authentication_failed",
    )
    try:
        policy = strict_json(policy_payload, MAX_POLICY_BYTES, "trust policy")
    except GenerationContractError as error:
        fail("device_generation_authentication_failed", str(error))
    if (not isinstance(policy, dict) or set(policy) != POLICY_FIELDS
            or policy.get("schemaVersion") != 1 or policy.get("status") != "active"
            or policy.get("policyId") != "opemos-userspace-lock-generations"
            or policy.get("policySchemaVersion") != 1
            or policy.get("keyringFilename") != keyring_path.name
            or not isinstance(policy.get("keyringSha256"), str)
            or HASH.fullmatch(policy["keyringSha256"]) is None
            or policy["keyringSha256"] != sha256(keyring_payload)
            or not isinstance(policy.get("signingKeyFingerprint"), str)
            or FINGERPRINT.fullmatch(policy["signingKeyFingerprint"]) is None):
        fail("device_generation_authentication_failed", "trust policy is unsupported")
    try:
        checkpoint_document = strict_json(
            checkpoint_payload, MAX_POLICY_BYTES, "bootstrap checkpoint"
        )
    except GenerationContractError as error:
        fail("device_generation_authentication_failed", str(error))
    if (not isinstance(checkpoint_document, dict)
            or set(checkpoint_document) != CHECKPOINT_FIELDS
            or checkpoint_document.get("schemaVersion") != 1
            or checkpoint_document.get("policySha256") != sha256(policy_payload)
            or type(checkpoint_document.get("sequence")) is not int
            or not 1 <= checkpoint_document["sequence"] <= MAX_SEQUENCE
            or not isinstance(checkpoint_document.get("manifestSha256"), str)
            or HASH.fullmatch(checkpoint_document["manifestSha256"]) is None):
        fail("device_generation_authentication_failed", "bootstrap checkpoint is unsupported")
    authority = {
        "policyId": policy["policyId"],
        "policySchemaVersion": policy["policySchemaVersion"],
        "policySha256": sha256(policy_payload),
        "keyringFilename": policy["keyringFilename"],
        "keyringSha256": policy["keyringSha256"],
        "signingKeyFingerprint": policy["signingKeyFingerprint"],
    }
    checkpoint = {
        "sequence": checkpoint_document["sequence"],
        "manifestSha256": checkpoint_document["manifestSha256"],
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
                    if len(output) > MAX_SIGNATURE_STATUS_BYTES:
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
        status_text = output.decode("ascii")
    except UnicodeError:
        fail("device_generation_authentication_failed", "signature status is malformed")
    signers = []
    for line in status_text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
            signers.append(fields[2].upper())
    if signers != [fingerprint]:
        fail("device_generation_authentication_failed", "signature signer is unauthorized")


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
    identity = pair["discovery"]["generation"]["manifestSha256"]
    destination = generations / identity
    if destination.exists() or destination.is_symlink():
        verify_cached_generation(destination, identity)
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
        if entry.name.startswith(".stage-"):
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


def activate(arguments):
    store = absolute_path(arguments.store)
    source = absolute_path(arguments.source)
    policy, authority, keyring, checkpoint = active_policy(arguments)
    pair = load_authenticated_pair(source, keyring, policy["signingKeyFingerprint"])
    requested_target = target_from_arguments(arguments)
    if (pair["manifest"]["authority"] != authority
            or not any(record["target"] == requested_target
                       for record in pair["manifest"]["targetLocks"])):
        fail(
            "device_generation_not_authorized",
            "generation does not authorize the requested target or authority",
        )
    lineage_pairs = []
    for lineage_path in arguments.lineage:
        item = load_authenticated_pair(
            absolute_path(lineage_path), keyring, policy["signingKeyFingerprint"],
            lineage=True,
        )
        lineage_pairs.append((item["discovery"], item["manifest"]))
    with lifecycle_lock(store, create=True) as generations:
        state, prior_revision = read_state_record(store)
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
        payload_root, payload_records = source_payload_records(
            source, pair["manifest"]
        )
        pruned_before = prune_generations(
            generations, state, limit=MAX_GENERATIONS - 1
        )
        require_cache_admission(
            generations, pair, payload_records, policy, authority
        )
        previous = state["active"]
        candidate = {
            "sequence": pair["manifest"]["sequence"],
            "manifestSha256": current_identity,
        }
        activated_state = {
            **state,
            "active": candidate,
            "lastKnownGood": (
                previous if previous is not None else state["lastKnownGood"]
            ),
            "highWaterSequence": max(
                state["highWaterSequence"], candidate["sequence"]
            ),
            "healthPending": candidate != state["lastKnownGood"],
        }
        write_pending_activation(store, state, prior_revision, candidate)
        try:
            _generation, created = publish_generation(
                generations, pair, payload_root, payload_records, policy, authority
            )
            if (development_override() and os.environ.get(
                    "OPEMOS_GENERATION_TEST_PAUSE_AFTER_PUBLISH")):
                time.sleep(float(
                    os.environ["OPEMOS_GENERATION_TEST_PAUSE_AFTER_PUBLISH"]
                ))
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
        state = activated_state
        return result(
            "ok", "activated", state=state,
            details={
                "generationCreated": created,
                "prunedGenerations": pruned_before + pruned,
                "cancellationAfterCommit": cancellation_after_commit,
            },
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
    with lifecycle_lock(store) as generations:
        state = read_state(store)
        if state["active"] is None:
            fail("device_generation_no_active", "no active generation exists")
        identity = state["active"]["manifestSha256"]
        verify_cached_generation(generations / identity, identity)
        validate_health_evidence(arguments, state["active"])
        state = {
            **state,
            "lastKnownGood": dict(state["active"]),
            "healthPending": False,
        }
        pruned, cancellation_after_commit = commit_state_and_prune(
            store, generations, state
        )
        return result(
            "ok", "health_acknowledged", state=state,
            details={
                "prunedGenerations": pruned,
                "cancellationAfterCommit": cancellation_after_commit,
            },
        )


def rollback(arguments):
    store = absolute_path(arguments.store)
    policy, _authority, keyring, _checkpoint = active_policy(arguments)
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
        if pair["discovery"]["authority"]["policySha256"] != sha256(canonical(policy)):
            fail("device_generation_authentication_failed", "rollback authority changed")
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
        with lifecycle_lock(store) as generations:
            state = read_state(store)
            for identity in (state["active"], state["lastKnownGood"]):
                if identity is not None:
                    verify_cached_generation(
                        generations / identity["manifestSha256"],
                        identity["manifestSha256"],
                    )
            return result("ok", "checked", state=state)
    safe_store(store, create=False)
    if read_pending_activation(store) is not None:
        fail(
            "device_generation_state_reconciliation_required",
            "an interrupted activation requires locked reconciliation",
        )
    state = read_state(store)
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
    commands = value.add_subparsers(dest="command", required=True)
    activate_parser = commands.add_parser("activate")
    activate_parser.add_argument("--source", required=True)
    activate_parser.add_argument("--lineage", action="append", default=[])
    activate_parser.add_argument("--steamos", required=True)
    activate_parser.add_argument("--kernel", required=True)
    activate_parser.add_argument("--nvidia", required=True)
    activate_parser.add_argument("--architecture", default="x86_64")
    commands.add_parser("status")
    commands.add_parser("check")
    acknowledge_parser = commands.add_parser("acknowledge-health")
    acknowledge_parser.add_argument("--evidence", required=True)
    commands.add_parser("rollback")
    commands.add_parser("prune")
    commands.add_parser("update")
    commands.add_parser("update-or-repair")
    return value


def handle_signal(_signum, _frame):
    raise DeviceGenerationCancelled()


def main():
    arguments = parser().parse_args()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        if arguments.command == "activate":
            document = activate(arguments)
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
            fail(
                "device_generation_network_inactive",
                "installed-device generation networking is not configured",
            )
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
