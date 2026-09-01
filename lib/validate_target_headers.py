#!/usr/bin/env python3
"""Validate an exact Valve headers package and its extracted build tree."""

import argparse
import os
import subprocess
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 250_000
MAX_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 8 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_LISTING_BYTES = 64 * 1024 * 1024
MAX_LISTING_LINE_BYTES = 4096


def fail(message):
    raise SystemExit(message)


def archive_text(package, mode, *members):
    try:
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        return subprocess.run(
            ["bsdtar", mode, str(package), *members],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        fail("Headers archive is unreadable or bsdtar is unavailable.")


def archive_lines(package, verbose=False):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    arguments = ["bsdtar", "-tvf" if verbose else "-tf", str(package)]
    try:
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        fail("Headers archive is unreadable or bsdtar is unavailable.")
    lines = []
    buffer = b""
    total = 0
    try:
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LISTING_BYTES:
                fail("Headers archive member listing exceeds the size limit.")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > MAX_LISTING_LINE_BYTES:
                    fail("Headers archive contains an excessive member name.")
                lines.append(line.decode("utf-8", errors="strict"))
                if len(lines) > MAX_ARCHIVE_MEMBERS:
                    fail("Headers archive has too many members.")
            if len(buffer) > MAX_LISTING_LINE_BYTES:
                fail("Headers archive contains an excessive member name.")
        if buffer:
            lines.append(buffer.decode("utf-8", errors="strict"))
        if process.wait() != 0:
            fail("Headers archive is unreadable or bsdtar is unavailable.")
    except (UnicodeError, ValueError):
        process.kill()
        process.wait()
        fail("Headers archive member listing is not bounded UTF-8 text.")
    except SystemExit:
        process.kill()
        process.wait()
        raise
    return lines


def normalize_member(name):
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        fail("Headers archive contains an unsafe path.")
    return str(path)


def confined_link_target(member, target, hardlink=False):
    target_path = PurePosixPath(target)
    if not target or target_path.is_absolute():
        fail("Headers archive contains an unsafe link target.")
    combined = target_path if hardlink else PurePosixPath(member).parent / target_path
    confined = []
    for component in combined.parts:
        if component in ("", "."):
            continue
        if component == "..":
            if not confined:
                fail("Headers archive contains an escaping link target.")
            confined.pop()
        else:
            confined.append(component)
    if not confined:
        fail("Headers archive contains an unsafe link target.")
    return str(PurePosixPath(*confined))


def validate_archive_layout(package):
    try:
        compressed_size = package.stat().st_size
    except OSError:
        fail("Headers archive is unreadable or bsdtar is unavailable.")
    if compressed_size > MAX_ARCHIVE_BYTES:
        fail("Headers archive exceeds the compressed-size limit.")
    names = archive_lines(package)
    verbose = archive_lines(package, verbose=True)
    if not names or len(names) != len(verbose) or len(names) > MAX_ARCHIVE_MEMBERS:
        fail("Headers archive has an invalid or excessive member listing.")
    normalized_names = [normalize_member(name) for name in names]
    if len(set(normalized_names)) != len(normalized_names):
        fail("Headers archive contains duplicate member paths.")
    member_names = set(normalized_names)
    total_size = 0
    metadata_size = None
    for name, normalized, line in zip(names, normalized_names, verbose):
        fields = line.split(maxsplit=8)
        if len(fields) != 9 or not fields[4].isdigit():
            fail("Headers archive member metadata is unreadable.")
        kind = line[0]
        size = int(fields[4])
        if kind not in ("-", "d", "l", "h"):
            fail("Headers archive contains a special device or stream entry.")
        if size > MAX_MEMBER_BYTES:
            fail("Headers archive contains an oversized member.")
        total_size += size
        if total_size > MAX_TOTAL_MEMBER_BYTES:
            fail("Headers archive exceeds the expanded-size limit.")
        if normalized == ".PKGINFO":
            metadata_size = size
        if kind in ("l", "h"):
            relation = " -> " if kind == "l" else " link to "
            prefix = name + relation
            if not fields[8].startswith(prefix):
                fail("Headers archive link metadata is unreadable.")
            target = fields[8][len(prefix):]
            confined_target = confined_link_target(normalized, target, kind == "h")
            if kind == "h" and confined_target not in member_names:
                fail("Headers archive contains a hardlink to an absent member.")
    if metadata_size is None or metadata_size > MAX_METADATA_BYTES:
        fail("Headers archive lacks bounded package metadata.")


def package_metadata(text):
    metadata = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            metadata.setdefault(key, value)
    return metadata


def validate_package(args):
    validate_archive_layout(args.package)

    info = archive_text(args.package, "-xOf", ".PKGINFO")
    metadata = package_metadata(info)
    expected = {
        "pkgname": args.name,
        "pkgver": args.version,
        "arch": args.architecture,
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            fail(f"Headers package {field} does not match the exact target.")


def validate_tree(args):
    root = args.root.resolve()
    tree = root / "usr" / "lib" / "modules" / args.kernel / "build"
    try:
        resolved_tree = tree.resolve(strict=True)
        resolved_tree.relative_to(root)
    except (FileNotFoundError, RuntimeError, ValueError):
        fail("Headers package does not contain a confined exact-kernel build tree.")

    required = (
        "Makefile",
        "include/generated/autoconf.h",
        "Module.symvers",
    )
    for relative in required:
        candidate = resolved_tree / relative
        if not candidate.is_file():
            fail(f"Headers build tree is incomplete: missing {relative}.")
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (FileNotFoundError, RuntimeError, ValueError):
            fail(f"Headers build-tree file escapes the extraction root: {relative}.")
    print(resolved_tree)


def parse_args():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    package = commands.add_parser("package")
    package.add_argument("--package", type=Path, required=True)
    package.add_argument("--name", required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--architecture", required=True)

    tree = commands.add_parser("tree")
    tree.add_argument("--root", type=Path, required=True)
    tree.add_argument("--kernel", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "package":
        validate_package(args)
    else:
        validate_tree(args)


if __name__ == "__main__":
    main()
