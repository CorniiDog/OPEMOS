#!/usr/bin/env python3
"""Crash-safe mutation for the installed recovery fallback state."""

import argparse
import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

PROFILES = {"console", "igpu-desktop", "nouveau-experimental"}
MAX_STATE_BYTES = 4096
TEMP_PREFIX = ".fallback-state.tmp-"


def fail(message):
    raise ValueError(message)


def canonical(profile):
    payload = (json.dumps(
        {"schemaVersion": 1, "active": True, "profile": profile},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n").encode("ascii")
    if len(payload) > MAX_STATE_BYTES:
        fail("fallback state is excessive")
    return payload


def safe_state_path(value):
    path = Path(os.path.abspath(os.fspath(value)))
    if path.name != "state.json":
        fail("fallback state filename is invalid")
    try:
        info = path.parent.lstat()
        if path.parent.is_symlink():
            fail("fallback state directory is unsafe")
        parent = path.parent.resolve(strict=True)
    except OSError:
        fail("fallback state directory is unavailable")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        fail("fallback state directory is unsafe")
    return parent / path.name


def file_identity(info):
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_nlink,
    )


def verify_state(path, expected_payload=None, missing_ok=False):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        fail("fallback state is unavailable")
    except OSError:
        fail("fallback state is unsafe")
    try:
        before = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError:
            fail("fallback state changed during verification")
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o644
                or not 1 <= before.st_size <= MAX_STATE_BYTES
                or file_identity(before) != file_identity(current)):
            fail("fallback state is unsafe")
        payload = os.read(descriptor, MAX_STATE_BYTES + 1)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (len(payload) != before.st_size
                or file_identity(before) != file_identity(after)
                or file_identity(after) != file_identity(current)):
            fail("fallback state changed during verification")
        if expected_payload is not None and payload != expected_payload:
            fail("published fallback state differs from its intent")
        return file_identity(after)
    finally:
        os.close(descriptor)


def fsync_directory(path):
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def cleanup_temporaries(parent):
    entries = [entry for entry in parent.iterdir()
               if entry.name.startswith(TEMP_PREFIX)]
    if len(entries) > 16:
        fail("fallback state has excessive abandoned temporaries")
    for entry in entries:
        info = entry.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_size > MAX_STATE_BYTES):
            fail("fallback state temporary is unsafe")
        entry.unlink()
    if entries:
        fsync_directory(parent)


@contextmanager
def state_lock(path):
    lock_path = path.parent / ".fallback-state.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        fail("fallback state lock is unavailable")
    try:
        opened = os.fstat(descriptor)
        current = lock_path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino)
                != (current.st_dev, current.st_ino)):
            fail("fallback state lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another fallback state operation is running")
        cleanup_temporaries(path.parent)
        yield
        current = lock_path.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("fallback state lock was replaced")
    finally:
        os.close(descriptor)


def write_state(path, profile):
    if profile not in PROFILES:
        fail("fallback profile is invalid")
    payload = canonical(profile)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=TEMP_PREFIX, dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        fsync_directory(path.parent)
        verify_state(path, payload)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remove_state(path):
    expected = verify_state(path, missing_ok=True)
    if expected is None:
        return
    try:
        if file_identity(path.lstat()) != expected:
            fail("fallback state changed before removal")
        path.unlink()
    except OSError:
        fail("fallback state could not be removed")
    fsync_directory(path.parent)
    if path.exists() or path.is_symlink():
        fail("fallback state remains after removal")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("write", "remove"))
    parser.add_argument("--state", required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES))
    arguments = parser.parse_args()
    path = safe_state_path(arguments.state)
    with state_lock(path):
        if arguments.operation == "write":
            if arguments.profile is None:
                fail("write requires a fallback profile")
            write_state(path, arguments.profile)
        else:
            if arguments.profile is not None:
                fail("remove does not accept a fallback profile")
            remove_state(path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"recovery_fallback_state.py: {error}") from None
