#!/usr/bin/env python3
"""Verify the complete reviewed userspace set after an offline transaction."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath


MAX_VALIDATION_BYTES = 16 * 1024 * 1024
MAX_PACKAGES = 64
MAX_TOTAL_SECONDS = 600
MAX_PACKAGE_BYTES = 2 * 1024**3
MAX_PACKAGE_MEMBERS = 250_000
PACMAN_METADATA = {".BUILDINFO", ".CHANGELOG", ".INSTALL", ".MTREE", ".PKGINFO"}
SAFE_NAME = re.compile(r"[A-Za-z0-9@._+:-]{1,256}")
SAFE_VERSION = re.compile(r"[A-Za-z0-9@._+:-]{1,256}")
MAX_PROGRESS_ATTEMPT = 1_000_000


def progress_attempt(value):
    if re.fullmatch(r"[0-9]{1,7}", value) is None:
        raise argparse.ArgumentTypeError("progress attempt must be an integer")
    attempt = int(value, 10)
    if not 0 <= attempt <= MAX_PROGRESS_ATTEMPT:
        raise argparse.ArgumentTypeError("progress attempt is outside its supported range")
    return attempt


def emit_progress(attempt, completed, total):
    if attempt is None:
        return
    record = {
        "schemaVersion": 1,
        "attempt": attempt,
        "phase": "userspace_verification",
        "indeterminate": False,
        "unit": "items",
        "completed": completed,
        "total": total,
    }
    print(
        "STEAMOS_NVIDIA_PROGRESS "
        + json.dumps(record, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--package", action="append", default=[], type=Path)
    parser.add_argument("--progress-attempt", type=progress_attempt)
    return parser.parse_args()


def fail(message):
    raise SystemExit(message)


def require_time(deadline):
    if time.monotonic() >= deadline:
        fail("Installed userspace verification exceeded its time limit.")


def sha256(path, deadline):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            require_time(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def load_packages(path, incoming_paths):
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
        if path.stat().st_size > MAX_VALIDATION_BYTES:
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("Verified userspace metadata is unavailable.")
    packages = document.get("packages") if isinstance(document, dict) else None
    if (not isinstance(packages, list) or not 2 <= len(packages) <= MAX_PACKAGES):
        fail("Verified userspace metadata is malformed.")
    result = []
    seen = set()
    for package in packages:
        if not isinstance(package, dict):
            fail("Verified userspace metadata is malformed.")
        name = package.get("name")
        version = package.get("fullVersion")
        if (not isinstance(name, str) or SAFE_NAME.fullmatch(name) is None
                or not isinstance(version, str)
                or SAFE_VERSION.fullmatch(version) is None
                or name in seen):
            fail("Verified userspace identities are malformed.")
        seen.add(name)
        filename = package.get("filename")
        digest = package.get("sha256")
        if (not isinstance(filename, str) or Path(filename).name != filename
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            fail("Verified userspace package records are malformed.")
        result.append((name, version, filename, digest))
    incoming = {}
    for path in incoming_paths:
        try:
            if (path.is_symlink() or not path.is_file()
                    or path.stat().st_size > MAX_PACKAGE_BYTES
                    or path.name in incoming):
                raise OSError
        except OSError:
            fail("Incoming userspace package set is unsafe.")
        incoming[path.name] = path
    if set(incoming) != {record[2] for record in result}:
        fail("Incoming userspace package set differs from verified metadata.")
    return [(name, version, incoming[filename], digest)
            for name, version, filename, digest in result]


def confined_target(root, relative, *, allow_leaf_symlink=False):
    path = PurePosixPath(relative.removeprefix("./"))
    if not relative or path.is_absolute() or ".." in path.parts:
        fail("An installed userspace payload path is unsafe.")
    candidate = root
    for index, component in enumerate(path.parts):
        candidate = candidate / component
        try:
            mode = os.lstat(candidate).st_mode
        except OSError:
            fail("An installed userspace payload member is missing.")
        if stat.S_ISLNK(mode) and not (
            allow_leaf_symlink and index == len(path.parts) - 1
        ):
            fail("An installed userspace payload path traverses a symlink.")
    return candidate


def compare_streams(source, target, deadline):
    while True:
        require_time(deadline)
        source_chunk = source.read(1024 * 1024)
        target_chunk = target.read(1024 * 1024)
        if source_chunk != target_chunk:
            return False
        if not source_chunk:
            return True


def verify_package_payload(root, package, deadline):
    process = None
    archive = None
    package_stream = None
    hardlinks = []
    try:
        if package.name.endswith(".zst"):
            process = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(package)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            package_stream = process.stdout
            archive = tarfile.open(fileobj=package_stream, mode="r|")
        else:
            archive = tarfile.open(package, mode="r:*")
        member_count = 0
        for member in archive:
            require_time(deadline)
            member_count += 1
            if member_count > MAX_PACKAGE_MEMBERS:
                fail("Installed userspace payload exceeds its member limit.")
            normalized = str(PurePosixPath(member.name.removeprefix("./")))
            if normalized in PACMAN_METADATA:
                continue
            if member.isdir():
                target = confined_target(root, normalized)
                if not target.is_dir() or target.is_symlink():
                    fail("An installed userspace directory is invalid.")
                continue
            if member.issym():
                target = confined_target(root, normalized, allow_leaf_symlink=True)
                if not target.is_symlink() or os.readlink(target) != member.linkname:
                    fail("An installed userspace symlink differs from its package.")
                continue
            if member.islnk():
                hardlinks.append((normalized, member.linkname.removeprefix("./")))
                continue
            if not member.isfile():
                fail("An installed userspace payload contains a special member.")
            target = confined_target(root, normalized)
            target_stat = os.lstat(target)
            test_mode = os.environ.get("PROJECT_TEST_MODE") == "1"
            if (not stat.S_ISREG(target_stat.st_mode)
                    or target_stat.st_size != member.size
                    or stat.S_IMODE(target_stat.st_mode) != (member.mode & 0o7777)
                    or (not test_mode and target_stat.st_uid != member.uid)
                    or (not test_mode and target_stat.st_gid != member.gid)):
                fail("An installed userspace file's metadata differs from its package.")
            source = archive.extractfile(member)
            if source is None:
                fail("An installed userspace package member is unreadable.")
            with source, target.open("rb") as installed:
                if not compare_streams(source, installed, deadline):
                    fail("An installed userspace file differs from its package.")
        for name, linkname in hardlinks:
            target = confined_target(root, name)
            linked = confined_target(root, linkname)
            target_stat = os.stat(target, follow_symlinks=False)
            linked_stat = os.stat(linked, follow_symlinks=False)
            if ((target_stat.st_dev, target_stat.st_ino)
                    != (linked_stat.st_dev, linked_stat.st_ino)):
                fail("An installed userspace hardlink differs from its package.")
        if process is not None:
            package_stream.close()
            if process.wait() != 0:
                fail("An installed userspace package is unreadable.")
    except (OSError, tarfile.TarError):
        fail("Installed userspace payload verification could not complete.")
    finally:
        if archive is not None:
            archive.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def run_pacman(command, deadline, *, capture=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail("Installed userspace verification exceeded its time limit.")
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C", "SYSTEMD_OFFLINE": "1"})
    try:
        return subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=min(120, remaining),
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("Installed userspace verification could not complete.")


def main():
    args = arguments()
    try:
        if (not args.root.is_absolute() or args.root.is_symlink()
                or not args.root.is_dir()):
            raise OSError
        root_resolved = args.root.resolve(strict=True)
        database = args.root
        for component in ("usr", "lib", "holo", "pacmandb"):
            database = database / component
            mode = os.lstat(database).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise OSError
        database.resolve(strict=True).relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError):
        fail("Target userspace database is unsafe.")

    packages = load_packages(args.validation, args.package)
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    for completed, (name, expected_version, package, expected_digest) in enumerate(
        packages, start=1
    ):
        if sha256(package, deadline) != expected_digest:
            fail("An incoming userspace package changed after validation.")
        query = run_pacman([
            "pacman", "--root", str(args.root), "--dbpath", str(database),
            "-Q", name,
        ], deadline, capture=True)
        if (query.returncode != 0 or len(query.stdout) > 1024
                or query.stdout != f"{name} {expected_version}\n".encode()):
            fail("An installed userspace package does not match the reviewed lock.")
        integrity = run_pacman([
            "pacman", "--root", str(args.root), "--dbpath", str(database),
            "-Qkk", name,
        ], deadline)
        if integrity.returncode != 0:
            fail("An installed userspace package failed its integrity check.")
        verify_package_payload(args.root, package, deadline)
        if sha256(package, deadline) != expected_digest:
            fail("An incoming userspace package changed during verification.")
        emit_progress(args.progress_attempt, completed, len(packages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
