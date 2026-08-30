#!/usr/bin/env python3
"""Validate an exact Valve headers package and its extracted build tree."""

import argparse
import subprocess
from pathlib import Path, PurePosixPath


def fail(message):
    raise SystemExit(message)


def archive_text(package, mode, *members):
    try:
        return subprocess.run(
            ["bsdtar", mode, str(package), *members],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        fail("Headers archive is unreadable or bsdtar is unavailable.")


def normalize_member(name):
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        fail("Headers archive contains an unsafe path.")
    return str(path)


def package_metadata(text):
    metadata = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            metadata.setdefault(key, value)
    return metadata


def validate_package(args):
    listing = archive_text(args.package, "-tf")
    for member in listing.splitlines():
        normalize_member(member)

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
