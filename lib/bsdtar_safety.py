#!/usr/bin/env python3
"""Bounded path validation for archives consumed by trusted tooling."""

import os
import hashlib
import tempfile
import subprocess
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


MAX_LISTING_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class ArchiveSafetyError(ValueError):
    pass


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        if (path.is_symlink() or not path.is_file()
                or path.stat().st_size > MAX_ARCHIVE_BYTES):
            raise OSError
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ArchiveSafetyError("archive changed or became unreadable") from error
    return digest.hexdigest()


@contextmanager
def archive_snapshot(path):
    """Yield a private snapshot while detecting concurrent source changes."""
    before = file_sha256(path)
    with tempfile.TemporaryDirectory(prefix="archive-snapshot-") as temporary:
        snapshot = Path(temporary) / "archive"
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as source, snapshot.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ArchiveSafetyError("archive exceeds its size limit")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
        except OSError as error:
            raise ArchiveSafetyError("archive changed or became unreadable") from error
        if digest.hexdigest() != before or file_sha256(path) != before:
            raise ArchiveSafetyError("archive changed during inspection")
        yield snapshot


def bounded_output(arguments, maximum, *, text=False):
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C"})
    try:
        process = subprocess.Popen(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment,
        )
    except OSError as error:
        raise ArchiveSafetyError("bsdtar is unavailable") from error
    output = process.stdout.read(maximum + 1)
    if len(output) > maximum:
        process.kill()
        process.wait()
        raise ArchiveSafetyError("archive output exceeds its size limit")
    if process.wait() != 0:
        raise ArchiveSafetyError("archive cannot be inspected")
    if not text:
        return output
    try:
        return output.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ArchiveSafetyError("archive listing is not UTF-8") from error


def inspect_archive(path, *, max_members, max_expanded_bytes, allow_links=False,
                    allow_empty=False, return_kinds=False):
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
    except OSError as error:
        raise ArchiveSafetyError("archive is not a safe regular file") from error
    names = bounded_output(
        ["bsdtar", "-tf", str(path)], MAX_LISTING_BYTES, text=True
    ).splitlines()
    verbose = bounded_output(
        ["bsdtar", "-tvf", str(path)], MAX_LISTING_BYTES, text=True
    ).splitlines()
    if ((not names and not allow_empty) or len(names) != len(verbose)
            or len(names) > max_members):
        raise ArchiveSafetyError("archive member listing is invalid or excessive")
    normalized_names = []
    seen = set()
    kinds = {}
    total = 0
    for original, line in zip(names, verbose):
        normalized = str(PurePosixPath(original))
        path_name = PurePosixPath(original)
        if (not original or path_name.is_absolute() or ".." in path_name.parts
                or normalized in seen):
            raise ArchiveSafetyError("archive has an unsafe or duplicate member path")
        fields = line.split(maxsplit=8)
        if len(fields) != 9 or not fields[4].isdigit():
            raise ArchiveSafetyError("archive member metadata is malformed")
        kind = line[0]
        allowed = {"-", "d", "l", "h"} if allow_links else {"-", "d"}
        if kind not in allowed:
            raise ArchiveSafetyError("archive contains a disallowed member type")
        size = int(fields[4])
        total += size
        if size > max_expanded_bytes or total > max_expanded_bytes:
            raise ArchiveSafetyError("archive expansion exceeds its size limit")
        canonical = {normalized, normalized + "/"} if kind == "d" else {normalized}
        if original not in canonical:
            raise ArchiveSafetyError("archive has a noncanonical member path")
        normalized_names.append(normalized)
        seen.add(normalized)
        kinds[normalized] = kind
    return (normalized_names, kinds) if return_kinds else normalized_names


def extract_single_member(path, member, *, maximum, max_members=250_000):
    with archive_snapshot(path) as snapshot:
        names, kinds = inspect_archive(
            snapshot, max_members=max_members,
            max_expanded_bytes=max(maximum, 2 * 1024 * 1024 * 1024),
            allow_links=True, return_kinds=True,
        )
        normalized = str(PurePosixPath(member))
        if names.count(normalized) != 1 or kinds.get(normalized) != "-":
            raise ArchiveSafetyError("required archive member is absent or ambiguous")
        return bounded_output(
            ["bsdtar", "-xOf", str(snapshot), member], maximum, text=False
        )


def extract_confined(path, destination, *, max_members, max_expanded_bytes,
                     allow_empty=False):
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ArchiveSafetyError("archive extraction destination is unsafe or nonempty")
    with archive_snapshot(path) as snapshot:
        inspect_archive(
            snapshot, max_members=max_members, max_expanded_bytes=max_expanded_bytes,
            allow_links=False, allow_empty=allow_empty,
        )
        try:
            environment = os.environ.copy()
            environment.update({"LANG": "C", "LC_ALL": "C"})
            subprocess.run(
                ["bsdtar", "-xf", str(snapshot), "-C", str(destination)],
                check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=environment,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ArchiveSafetyError("archive extraction failed") from error
    root = destination.resolve(strict=True)
    for extracted in destination.rglob("*"):
        if extracted.is_symlink():
            raise ArchiveSafetyError("archive extraction produced a symlink")
        try:
            extracted.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise ArchiveSafetyError("archive extraction escaped its destination") from error
