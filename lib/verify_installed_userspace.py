#!/usr/bin/env python3
"""Verify the complete reviewed userspace set after an offline transaction."""

import argparse
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

from atomic_output import atomic_write_bytes


MAX_VALIDATION_BYTES = 16 * 1024 * 1024
MAX_PACKAGES = 64
MAX_TOTAL_SECONDS = 600
MAX_PACKAGE_BYTES = 2 * 1024**3
MAX_PACKAGE_MEMBERS = 250_000
PACMAN_METADATA = {".BUILDINFO", ".CHANGELOG", ".INSTALL", ".MTREE", ".PKGINFO"}
SAFE_NAME = re.compile(r"[A-Za-z0-9@._+:-]{1,256}")
SAFE_VERSION = re.compile(r"[A-Za-z0-9@._+:-]{1,256}")
MAX_PROGRESS_ATTEMPT = 1_000_000
MAX_RESULT_BYTES = 256 * 1024
MAX_PACMAN_DIAGNOSTIC_BYTES = 64 * 1024
RECONCILABLE_QKK_DIRECTORIES = frozenset({"usr/lib"})


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


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
    parser.add_argument("--output", required=True, type=Path)
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
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
            parse_constant=reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        fail("Verified userspace metadata is unavailable.")
    packages = document.get("packages") if isinstance(document, dict) else None
    target = document.get("target") if isinstance(document, dict) else None
    nvidia_version = target.get("nvidiaVersion") if isinstance(target, dict) else None
    if (not isinstance(nvidia_version, str)
            or re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", nvidia_version) is None):
        fail("Verified NVIDIA userspace identity is malformed.")
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
        dependencies = package.get("dependencies")
        provides = package.get("provides")
        if (not isinstance(filename, str) or Path(filename).name != filename
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or any(not isinstance(values, list) or len(values) > 64
                       or any(not isinstance(value, str) or not 1 <= len(value) <= 256
                              or any(ord(character) < 32 or ord(character) == 127
                                     for character in value)
                              for value in values)
                       or len(values) != len(set(values))
                       for values in (dependencies, provides))):
            fail("Verified userspace package records are malformed.")
        result.append((name, version, filename, digest, dependencies, provides))
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
    binding = {
        "userspaceLockSha256": document.get("userspaceLock", {}).get("sha256"),
        "provenanceSha256": document.get("provenanceSha256"),
    }
    if any(not isinstance(value, str)
           or re.fullmatch(r"[0-9a-f]{64}", value) is None
           for value in binding.values()):
        fail("Verified userspace binding metadata is malformed.")
    return ([(name, version, filename, incoming[filename], digest, dependencies, provides)
             for name, version, filename, digest, dependencies, provides in result],
            nvidia_version, binding)


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


def verify_package_payload(
    root, package, deadline, nvidia_version, qkk_directory_entries=()
):
    process = None
    archive = None
    package_stream = None
    hardlinks = []
    counts = {
        "directories": 0,
        "regularFiles": 0,
        "symlinks": 0,
        "hardlinks": 0,
        "sharedLibraries": 0,
    }
    gsp_firmware = []
    unmatched_qkk_directories = set(qkk_directory_entries)
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
                unmatched_qkk_directories.discard(normalized)
                counts["directories"] += 1
                continue
            if member.issym():
                target = confined_target(root, normalized, allow_leaf_symlink=True)
                if not target.is_symlink() or os.readlink(target) != member.linkname:
                    fail("An installed userspace symlink differs from its package.")
                counts["symlinks"] += 1
                if ".so" in PurePosixPath(normalized).name:
                    counts["sharedLibraries"] += 1
                continue
            if member.islnk():
                hardlinks.append((normalized, member.linkname.removeprefix("./")))
                counts["hardlinks"] += 1
                if ".so" in PurePosixPath(normalized).name:
                    counts["sharedLibraries"] += 1
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
            counts["regularFiles"] += 1
            if ".so" in PurePosixPath(normalized).name:
                counts["sharedLibraries"] += 1
            firmware_prefix = f"usr/lib/firmware/nvidia/{nvidia_version}/"
            if (normalized.startswith(firmware_prefix)
                    and PurePosixPath(normalized).name.startswith("gsp")
                    and PurePosixPath(normalized).name.endswith(".bin")):
                if len(gsp_firmware) >= 16:
                    fail("Matching GSP firmware inventory exceeds its limit.")
                gsp_firmware.append(normalized)
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
        if unmatched_qkk_directories:
            fail(
                "Package database integrity diagnostics do not describe "
                "package directories."
            )
    except (OSError, tarfile.TarError):
        fail("Installed userspace payload verification could not complete.")
    finally:
        if archive is not None:
            archive.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
    return counts, sorted(gsp_firmware)


def publish(path, document):
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_RESULT_BYTES:
        fail("Installed userspace verification result exceeds its size limit.")
    atomic_write_bytes(path, payload)


def publish_mismatch(path, package_name, invalid_fields, affected_entries, message):
    publish(path, {
        "schemaVersion": 1,
        "status": "failed",
        "reason": "installed_userspace_mismatch",
        "message": message,
        "packageMismatches": [{
            "packageName": package_name,
            "invalidFields": invalid_fields,
            "affectedEntries": sorted(set(affected_entries))[:16],
        }],
    })


def parse_pacman_integrity_diagnostics(name, output):
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    entries = []
    altered = None
    warning = re.compile(
        rf"warning: {re.escape(name)}: "
        r"(/[A-Za-z0-9._+~/-]{1,512}) "
        r"\((?:Permissions|UID|GID|Modification time) mismatch\)"
    )
    summary = re.compile(
        rf"{re.escape(name)}: [0-9]+ total files, ([0-9]+) altered files?"
    )
    for line in lines:
        match = warning.fullmatch(line)
        if match is not None:
            relative = match.group(1).removeprefix("/")
            path = PurePosixPath(relative)
            if not relative or ".." in path.parts:
                return None
            entries.append(relative)
            continue
        match = summary.fullmatch(line)
        if match is not None and altered is None:
            altered = int(match.group(1), 10)
            continue
        return None
    unique = sorted(set(entries))
    if (altered is None or altered != len(unique)
            or not 1 <= len(unique) <= 16
            or not set(unique) <= RECONCILABLE_QKK_DIRECTORIES):
        return None
    return unique


def run_pacman_captured(command, deadline, limit):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail("Installed userspace verification exceeded its time limit.")
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C", "SYSTEMD_OFFLINE": "1"})
    process = None
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = False
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, MAX_TOTAL_SECONDS)
            events = selector.select(timeout=min(1, remaining))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in selector.get_map().values()
                ]
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                captured = sum(len(value) for value in streams.values())
                capacity = max(0, limit + 1 - captured)
                streams[key.data].extend(chunk[:capacity])
                if len(chunk) > capacity or captured + len(chunk) > limit:
                    overflow = True
                    process.kill()
                    break
            if overflow:
                break
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        fail("Installed userspace verification could not complete.")
    finally:
        selector.close()
        for stream in (() if process is None else (process.stdout, process.stderr)):
            if stream is not None and not stream.closed:
                stream.close()
    return subprocess.CompletedProcess(
        command, returncode, bytes(streams["stdout"]), bytes(streams["stderr"])
    ), overflow


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
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
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

    packages, nvidia_version, validation_binding = load_packages(
        args.validation, args.package
    )
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    records = []
    all_gsp_firmware = []
    for completed, (name, expected_version, filename, package, expected_digest,
                    dependencies, provides) in enumerate(
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
            publish_mismatch(
                args.output, name, ["query"], [],
                "An installed userspace package does not match the reviewed lock.",
            )
            fail("An installed userspace package does not match the reviewed lock.")
        integrity, integrity_overflow = run_pacman_captured([
            "pacman", "--root", str(args.root), "--dbpath", str(database),
            "-Qkk", name,
        ], deadline, MAX_PACMAN_DIAGNOSTIC_BYTES)
        integrity_output = integrity.stdout + integrity.stderr
        integrity_entries = []
        if integrity.returncode != 0:
            if not integrity_overflow:
                integrity_entries = parse_pacman_integrity_diagnostics(
                    name, integrity_output
                )
            if not integrity_entries:
                publish_mismatch(
                    args.output, name, ["databaseIntegrity"], [],
                    "Package database integrity diagnostics could not be reconciled.",
                )
                fail("An installed userspace package failed its integrity check.")
        try:
            counts, gsp_firmware = verify_package_payload(
                args.root, package, deadline, nvidia_version, integrity_entries
            )
        except SystemExit as error:
            fields = ["databaseIntegrity"]
            message = str(error)
            if "symlink" in message or "hardlink" in message:
                fields.append("payloadLink")
            elif "metadata" in message:
                fields.extend(["payloadHash", "payloadMode", "payloadOwnership"])
            elif "file differs" in message:
                fields.append("payloadHash")
            else:
                fields.append("payloadPath")
            publish_mismatch(
                args.output, name, fields,
                integrity_entries, message,
            )
            raise
        # pacman -Qkk can report inherited directory metadata from the SteamOS
        # base as altered even though every package member is present and the
        # authoritative incoming archive comparison above succeeds.  Only that
        # complete byte/link/metadata comparison may reconcile a nonzero Qkk;
        # any payload discrepancy publishes a bounded failure document.
        all_gsp_firmware.extend(gsp_firmware)
        if sha256(package, deadline) != expected_digest:
            fail("An incoming userspace package changed during verification.")
        emit_progress(args.progress_attempt, completed, len(packages))
        records.append({
            "packageName": name,
            "packageFilename": filename,
            "version": expected_version,
            "packageSha256": expected_digest,
            "dependencies": dependencies,
            "provides": provides,
            "packageQueryVerified": True,
            "pacmanIntegrityVerified": True,
            "payloadVerified": True,
            "payloadPathsConfined": True,
            "payloadHashesVerified": True,
            "payloadModesVerified": True,
            "payloadOwnershipVerified": True,
            "payloadLinksVerified": True,
            **counts,
        })
    all_gsp_firmware = sorted(set(all_gsp_firmware))
    if not all_gsp_firmware:
        fail("Matching GSP firmware was not installed into the target root.")
    if len(all_gsp_firmware) > 16:
        fail("Matching GSP firmware inventory exceeds its limit.")
    consistency = run_pacman([
        "pacman", "--root", str(args.root), "--dbpath", str(database), "-Dk",
    ], deadline)
    if consistency.returncode != 0:
        fail("The target userspace package database failed its consistency check.")
    publish(args.output, {
        "schemaVersion": 1,
        "status": "verified",
        "reason": "installed_userspace_verified",
        "validationBinding": validation_binding,
        "pacmanDatabase": {
            "path": "/usr/lib/holo/pacmandb",
            "status": "verified",
            "verifiedPackageCount": len(records),
            "consistencyVerified": True,
        },
        "packages": records,
        "gspFirmware": {
            "version": nvidia_version,
            "status": "verified",
            "targetRelativeFiles": all_gsp_firmware,
        },
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
