#!/usr/bin/env python3
"""Create and enforce one immutable exact-release plan across repair retries."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

MAX_DOCUMENT_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
FIELDS = {
    "schemaVersion", "steamosVersion", "nvidiaVersion", "kernelTag",
    "releaseTag", "assetName", "archiveSha256",
}
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")
KERNEL = re.compile(r"[A-Za-z0-9._+\-]{1,192}")
RELEASE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,255}")
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,255}\.tar\.gz")
SHA = re.compile(r"[0-9a-f]{64}")


def fail(message):
    raise ValueError(message)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("release plan contains a duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value):
    fail("release plan contains a non-finite number")


def encode(document):
    payload = (json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n").encode("utf-8")
    if len(payload) > MAX_DOCUMENT_BYTES:
        fail("release plan is excessive")
    return payload


def validate(document):
    if not isinstance(document, dict) or set(document) != FIELDS:
        fail("release plan fields are malformed")
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        fail("release plan schema is unsupported")
    if (not isinstance(document["steamosVersion"], str)
            or VERSION.fullmatch(document["steamosVersion"]) is None
            or not isinstance(document["nvidiaVersion"], str)
            or VERSION.fullmatch(document["nvidiaVersion"]) is None
            or not isinstance(document["kernelTag"], str)
            or KERNEL.fullmatch(document["kernelTag"]) is None
            or not isinstance(document["releaseTag"], str)
            or RELEASE_TAG.fullmatch(document["releaseTag"]) is None
            or not isinstance(document["assetName"], str)
            or ASSET_NAME.fullmatch(document["assetName"]) is None):
        fail("release plan identity is malformed")
    digest = document["archiveSha256"]
    if digest is not None and (
            not isinstance(digest, str) or SHA.fullmatch(digest) is None):
        fail("release plan archive identity is malformed")
    return document


def safe_parent(path):
    path = Path(os.path.abspath(os.fspath(path)))
    if path.name in ("", ".", ".."):
        fail("release plan location is invalid")
    try:
        info = path.parent.lstat()
    except OSError:
        fail("release plan directory is unavailable")
    if (not stat.S_ISDIR(info.st_mode) or path.parent.is_symlink()
            or info.st_uid != os.geteuid() or info.st_mode & 0o022):
        fail("release plan directory is unsafe")
    return path.parent.resolve(strict=True) / path.name


def identity(info):
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_nlink,
    )


def safe_open(path, missing_ok=False):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        fail("release plan is unavailable")
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
    except OSError:
        os.close(descriptor)
        fail("release plan is unavailable")
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)):
        os.close(descriptor)
        fail("release plan is unsafe")
    return descriptor


def read(path):
    path = safe_parent(path)
    descriptor = safe_open(path)
    try:
        before = os.fstat(descriptor)
        if not 1 <= before.st_size <= MAX_DOCUMENT_BYTES:
            fail("release plan is empty or excessive")
        payload = bytearray()
        while len(payload) <= MAX_DOCUMENT_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_DOCUMENT_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError:
            fail("release plan changed while it was read")
        if (len(payload) != before.st_size or len(payload) > MAX_DOCUMENT_BYTES
                or identity(before) != identity(after)
                or identity(after) != identity(current)):
            fail("release plan changed while it was read")
        try:
            document = json.loads(
                bytes(payload).decode("utf-8", errors="strict"),
                object_pairs_hook=strict_object, parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("release plan is not canonical JSON")
        validate(document)
        if bytes(payload) != encode(document):
            fail("release plan is not canonical JSON")
        return document, identity(after)
    finally:
        os.close(descriptor)


def verify_destination(path, expected_identity):
    descriptor = safe_open(path, missing_ok=expected_identity is None)
    if descriptor is None:
        if expected_identity is not None:
            fail("release plan disappeared before publication")
        return
    try:
        actual = identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    if expected_identity is None or actual != expected_identity:
        fail("release plan was replaced before publication")


def write(path, document, expected_identity=None, create_only=False):
    validate(document)
    path = safe_parent(path)
    payload = encode(document)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".release-plan.", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        verify_destination(path, expected_identity)
        if create_only:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                fail("release plan already exists")
            temporary.unlink()
            temporary = None
        else:
            os.replace(temporary, path)
            temporary = None
        directory = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def plan_lock(path):
    path = safe_parent(path)
    lock_path = path.parent / f".{path.name}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        fail("release plan lock is unavailable")
    try:
        opened = os.fstat(descriptor)
        current = lock_path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino)
                != (current.st_dev, current.st_ino)):
            fail("release plan lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another release plan operation is running")
        current = lock_path.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("release plan lock was replaced")
        yield path
        current = lock_path.lstat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            fail("release plan lock was replaced")
    finally:
        os.close(descriptor)


def hash_archive(path):
    path = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail("release archive is unavailable or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or not 1 <= before.st_size <= MAX_ARCHIVE_BYTES):
            fail("release archive is unsafe or outside size policy")
        hasher = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError:
            fail("release archive changed while it was hashed")
        if identity(before) != identity(after) or identity(after) != identity(current):
            fail("release archive changed while it was hashed")
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("create", "show", "bind-archive"))
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--steamos")
    parser.add_argument("--nvidia")
    parser.add_argument("--kernel-tag")
    parser.add_argument("--release-tag")
    parser.add_argument("--asset-name")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    with plan_lock(args.plan) as plan:
        if args.operation == "create":
            value = {
                "schemaVersion": 1,
                "steamosVersion": args.steamos,
                "nvidiaVersion": args.nvidia,
                "kernelTag": args.kernel_tag,
                "releaseTag": args.release_tag,
                "assetName": args.asset_name,
                "archiveSha256": None,
            }
            validate(value)
            write(plan, value, create_only=True)
        elif args.operation == "bind-archive":
            if args.archive is None:
                fail("bind-archive requires a release archive")
            value, plan_identity = read(plan)
            digest = hash_archive(args.archive)
            if value["archiveSha256"] not in (None, digest):
                fail("release archive changed across repair attempts")
            if value["archiveSha256"] is None:
                value["archiveSha256"] = digest
                write(plan, value, expected_identity=plan_identity)
        else:
            value, _plan_identity = read(plan)
    print("\t".join(str(value[key] or "") for key in (
        "steamosVersion", "nvidiaVersion", "kernelTag", "releaseTag",
        "assetName", "archiveSha256",
    )))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"recovery_release_plan.py: {error}") from None
