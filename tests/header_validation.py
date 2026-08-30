#!/usr/bin/env python3
"""Regression tests for exact Valve header-package validation."""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib" / "validate_target_headers.py"
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfe145653a794"
NAME = "linux-neptune-616-headers"
VERSION = "6.16.12.valve24.4-1"


def run_validator(*arguments, success):
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR), *map(str, arguments)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if (completed.returncode == 0) != success:
        raise AssertionError(
            f"unexpected validator result {completed.returncode}: {completed.stderr}"
        )
    return completed


def add_text(archive, name, text="fixture\n"):
    content = text.encode()
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def make_package(path, *, name=NAME, version=VERSION, architecture="x86_64", unsafe=None):
    with tarfile.open(path, "w:gz") as archive:
        add_text(
            archive,
            ".PKGINFO",
            f"pkgname = {name}\npkgver = {version}\narch = {architecture}\n",
        )
        add_text(archive, "usr/share/header-fixture")
        if unsafe:
            add_text(archive, unsafe)


def package_arguments(package):
    return (
        "package",
        "--package",
        package,
        "--name",
        NAME,
        "--version",
        VERSION,
        "--architecture",
        "x86_64",
    )


def make_tree(root, kernel=KERNEL, missing=None):
    tree = root / "usr" / "lib" / "modules" / kernel / "build"
    for relative in ("Makefile", "include/generated/autoconf.h", "Module.symvers"):
        if relative == missing:
            continue
        destination = tree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("fixture\n", encoding="utf-8")
    return tree


def main():
    with tempfile.TemporaryDirectory(prefix="header-validation-") as temporary:
        temporary = Path(temporary)
        valid = temporary / "valid.tar.gz"
        make_package(valid)
        run_validator(*package_arguments(valid), success=True)

        for field, value in (
            ("name", "wrong-headers"),
            ("version", "0-1"),
            ("architecture", "aarch64"),
        ):
            package = temporary / f"wrong-{field}.tar.gz"
            options = {field: value}
            make_package(package, **options)
            run_validator(*package_arguments(package), success=False)

        for index, unsafe in enumerate(("../escape", "/absolute/escape")):
            package = temporary / f"unsafe-{index}.tar.gz"
            make_package(package, unsafe=unsafe)
            run_validator(*package_arguments(package), success=False)

        valid_root = temporary / "valid-root"
        expected_tree = make_tree(valid_root).resolve()
        completed = run_validator(
            "tree", "--root", valid_root, "--kernel", KERNEL, success=True
        )
        assert Path(completed.stdout.strip()) == expected_tree

        wrong_root = temporary / "wrong-kernel-root"
        make_tree(wrong_root, kernel="wrong-kernel")
        run_validator(
            "tree", "--root", wrong_root, "--kernel", KERNEL, success=False
        )

        for index, missing in enumerate(
            ("Makefile", "include/generated/autoconf.h", "Module.symvers")
        ):
            root = temporary / f"missing-{index}"
            make_tree(root, missing=missing)
            run_validator("tree", "--root", root, "--kernel", KERNEL, success=False)

        escaping_root = temporary / "escaping-root"
        external_tree = temporary / "external-tree"
        external_tree.mkdir()
        link = escaping_root / "usr" / "lib" / "modules" / KERNEL / "build"
        link.parent.mkdir(parents=True)
        os.symlink(external_tree, link)
        run_validator(
            "tree", "--root", escaping_root, "--kernel", KERNEL, success=False
        )


if __name__ == "__main__":
    main()
