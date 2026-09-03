#!/usr/bin/env python3
"""Crash-safe, bounded delayed-network recovery transaction state."""

import argparse
import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MAX_DOCUMENT_BYTES = 64 * 1024
MAX_ATTEMPTS = 1_000_000
PHASES = {
    "offline_waiting", "retry_scheduled", "downloading", "verifying",
    "rebuilding", "installing", "restored", "cancelled", "failed",
}
ACTIVE_PHASES = PHASES - {"restored", "cancelled"}
TRANSITIONS = {
    "offline_waiting": {"retry_scheduled", "downloading", "cancelled"},
    "retry_scheduled": {"retry_scheduled", "downloading", "cancelled"},
    "downloading": {"rebuilding", "installing", "retry_scheduled", "failed", "cancelled"},
    "rebuilding": {"installing", "retry_scheduled", "failed", "cancelled"},
    "installing": {"verifying", "retry_scheduled", "failed", "cancelled"},
    "verifying": {"restored", "retry_scheduled", "failed", "cancelled"},
    # A failed post-install check remains recoverable by an explicit or queued
    # exact-target retry. Restored and cancelled transactions are terminal.
    "failed": {"downloading", "cancelled"},
    "restored": set(),
    "cancelled": set(),
}
FIELDS = {
    "schemaVersion", "active", "automaticRetry", "phase", "reason",
    "target", "supportRevision", "attempt", "createdAt", "updatedAt",
}
TARGET_FIELDS = {"kernelVersion", "nvidiaVersion"}
KERNEL = re.compile(r"^[A-Za-z0-9._+\-]{1,192}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REASON = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+){0,15}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00$")


def fail(message):
    raise ValueError(message)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("transaction state contains a duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value):
    fail("transaction state contains a non-finite number")


def encode(document):
    data = (json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("utf-8")
    if len(data) > MAX_DOCUMENT_BYTES:
        fail("transaction state is excessive")
    return data


def validate_timestamp(value, name):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        fail(f"transaction {name} is malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        fail(f"transaction {name} is malformed")
    if parsed.tzinfo != timezone.utc:
        fail(f"transaction {name} is malformed")
    return parsed


def validate(document):
    if not isinstance(document, dict) or set(document) != FIELDS:
        fail("transaction state fields are malformed")
    if document["schemaVersion"] != 1 or isinstance(document["schemaVersion"], bool):
        fail("transaction schema is unsupported")
    phase = document["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        fail("transaction phase is malformed")
    if type(document["active"]) is not bool or document["active"] != (phase in ACTIVE_PHASES):
        fail("transaction active state contradicts its phase")
    if type(document["automaticRetry"]) is not bool:
        fail("transaction retry state is malformed")
    if phase == "cancelled" and document["automaticRetry"]:
        fail("a cancelled transaction cannot retry automatically")
    reason = document["reason"]
    if not isinstance(reason, str) or len(reason) > 128 or not REASON.fullmatch(reason):
        fail("transaction reason is malformed")
    target = document["target"]
    if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
        fail("transaction target is malformed")
    kernel = target["kernelVersion"]
    nvidia = target["nvidiaVersion"]
    if not isinstance(kernel, str) or not KERNEL.fullmatch(kernel):
        fail("transaction kernel identity is malformed")
    if not isinstance(nvidia, str) or not VERSION.fullmatch(nvidia):
        fail("transaction NVIDIA identity is malformed")
    revision = document["supportRevision"]
    if not isinstance(revision, str) or not COMMIT.fullmatch(revision):
        fail("transaction support identity is malformed")
    attempt = document["attempt"]
    if type(attempt) is not int or not 0 <= attempt <= MAX_ATTEMPTS:
        fail("transaction attempt is malformed")
    created = validate_timestamp(document["createdAt"], "creation time")
    updated = validate_timestamp(document["updatedAt"], "update time")
    if updated < created:
        fail("transaction update time precedes its creation time")
    return document


def safe_parent(path):
    if not path.is_absolute() or path.name in ("", ".", ".."):
        fail("transaction state location is invalid")
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        fail("transaction state directory is unsafe")
    if info.st_mode & 0o022:
        fail("transaction state directory is writable by another identity")
    # Bind all later operations to the resolved parent. System roots may have
    # legitimate ancestor aliases (macOS /var -> /private/var), while the final
    # state directory itself must remain a real directory owned by this caller.
    return parent.resolve(strict=True)


def safe_open(path, *, missing_ok):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    info = os.fstat(descriptor)
    try:
        current = path.lstat()
    except OSError:
        os.close(descriptor)
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or
            (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
        os.close(descriptor)
        fail("transaction state is unsafe")
    return descriptor


def load(path):
    safe_parent(path)
    descriptor = safe_open(path, missing_ok=True)
    if descriptor is None:
        return None
    try:
        info = os.fstat(descriptor)
        if info.st_size <= 0 or info.st_size > MAX_DOCUMENT_BYTES:
            fail("transaction state is empty or excessive")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        after = os.fstat(descriptor)
        current = path.lstat()
        before_identity = (info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns, info.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if (len(data) != info.st_size or len(data) > MAX_DOCUMENT_BYTES or
                before_identity != after_identity or
                (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)):
            fail("transaction state changed while it was read")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            fail("transaction state is not UTF-8")
        document = json.loads(text, object_pairs_hook=strict_object,
                              parse_constant=reject_constant)
        validate(document)
        if data != encode(document):
            fail("transaction state is not canonical JSON")
        return document
    finally:
        os.close(descriptor)


def write(path, document, *, create_only=False):
    validate(document)
    parent = safe_parent(path)
    data = encode(document)
    existing = safe_open(path, missing_ok=True)
    existing_identity = None
    if existing is not None:
        info = os.fstat(existing)
        existing_identity = (info.st_dev, info.st_ino)
        os.close(existing)
        if create_only:
            fail("a recovery transaction already exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".transaction.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck the destination immediately before publication. This rejects
        # an attacker replacing an existing state after it was loaded.
        current = safe_open(path, missing_ok=True)
        if current is not None:
            info = os.fstat(current)
            current_identity = (info.st_dev, info.st_ino)
            os.close(current)
            if existing_identity is None or current_identity != existing_identity:
                fail("transaction state was replaced before publication")
        elif existing_identity is not None:
            fail("transaction state disappeared before publication")
        os.replace(temporary, path)
        temporary = None
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def remove_terminal(path):
    document = load(path)
    if document is None:
        fail("no recovery transaction exists")
    if document["phase"] not in {"restored", "cancelled"}:
        fail("an active recovery transaction cannot be removed")
    descriptor = safe_open(path, missing_ok=False)
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("transaction state changed before removal")
    finally:
        os.close(descriptor)
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if path.exists() or path.is_symlink():
        fail("transaction state remains after removal")


@contextmanager
def transaction_lock(path):
    parent = safe_parent(path)
    lock_path = parent / f".{path.name}.lock"
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = os.open(lock_path, flags)
    try:
        info = os.fstat(descriptor)
        current = lock_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or
                (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            fail("transaction lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another recovery transaction operation is running")
        current = lock_path.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            fail("transaction lock was replaced")
        yield
    finally:
        os.close(descriptor)


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation", choices=("show", "begin", "set", "cancel", "remove-terminal")
    )
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--kernel")
    parser.add_argument("--nvidia")
    parser.add_argument("--support-revision")
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    args.state = safe_parent(args.state) / args.state.name
    with transaction_lock(args.state):
        if args.operation == "show":
            document = load(args.state)
            print(json.dumps(document or {"schemaVersion": 1, "phase": "restored", "active": False},
                             sort_keys=True, separators=(",", ":")))
            return
        if args.operation == "remove-terminal":
            if any((args.kernel, args.nvidia, args.support_revision,
                    args.phase, args.reason)):
                fail("remove-terminal does not accept transaction fields")
            remove_terminal(args.state)
            return
        timestamp = now_utc()
        if args.operation == "begin":
            if not (args.kernel and args.nvidia and args.support_revision and args.phase):
                raise SystemExit("begin requires exact target, support revision, and phase")
            if args.phase != "offline_waiting":
                fail("a recovery transaction must begin in offline_waiting")
            document = {
                "schemaVersion": 1, "active": True, "automaticRetry": True,
                "phase": args.phase, "reason": args.reason,
                "target": {"kernelVersion": args.kernel, "nvidiaVersion": args.nvidia},
                "supportRevision": args.support_revision, "attempt": 0,
                "createdAt": timestamp, "updatedAt": timestamp,
            }
            write(args.state, document, create_only=True)
        else:
            document = load(args.state)
            if document is None:
                raise SystemExit("no recovery transaction exists")
            if args.operation == "cancel":
                if document["phase"] == "restored":
                    fail("a restored recovery transaction cannot be cancelled")
                if document["phase"] != "cancelled":
                    document.update({
                        "active": False, "automaticRetry": False,
                        "phase": "cancelled", "reason": "cancelled_by_user",
                        "updatedAt": timestamp,
                    })
                    write(args.state, document)
            else:
                if not args.phase:
                    raise SystemExit("set requires --phase")
                current_phase = document["phase"]
                if args.phase not in TRANSITIONS[current_phase]:
                    fail(f"invalid recovery transition from {current_phase} to {args.phase}")
                if document["attempt"] >= MAX_ATTEMPTS:
                    fail("transaction attempt limit is exhausted")
                document["phase"] = args.phase
                document["reason"] = args.reason
                document["attempt"] += 1
                document["active"] = args.phase in ACTIVE_PHASES
                if args.phase == "cancelled":
                    document["automaticRetry"] = False
                document["updatedAt"] = timestamp
                write(args.state, document)
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"recovery_transaction.py: {error}") from None
