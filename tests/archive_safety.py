#!/usr/bin/env python3
"""Regression tests for bounded bsdtar archive confinement."""

import io
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from bsdtar_safety import (  # noqa: E402
    ArchiveSafetyError, extract_confined, extract_single_member,
)


def member(archive, name, content=b"fixture"):
    item = tarfile.TarInfo(name)
    item.size = len(content)
    archive.addfile(item, io.BytesIO(content))


def rejected(path, destination):
    try:
        extract_confined(path, destination, max_members=16,
                         max_expanded_bytes=1024)
    except ArchiveSafetyError:
        return
    raise AssertionError("unsafe archive was accepted")


def main():
    with tempfile.TemporaryDirectory(prefix="archive-safety-") as temporary:
        root = Path(temporary)
        valid = root / "valid.tar.gz"
        with tarfile.open(valid, "w:gz") as archive:
            member(archive, "records/item/desc")
        destination = root / "valid-output"
        destination.mkdir()
        extract_confined(valid, destination, max_members=16,
                         max_expanded_bytes=1024)
        assert (destination / "records/item/desc").read_bytes() == b"fixture"
        assert extract_single_member(valid, "records/item/desc", maximum=1024) == b"fixture"

        traversal = root / "traversal.tar.gz"
        with tarfile.open(traversal, "w:gz") as archive:
            member(archive, "../escape")
        traversal_output = root / "traversal-output"
        traversal_output.mkdir()
        rejected(traversal, traversal_output)
        assert not (root / "escape").exists()

        linked = root / "link.tar.gz"
        with tarfile.open(linked, "w:gz") as archive:
            item = tarfile.TarInfo("link")
            item.type = tarfile.SYMTYPE
            item.linkname = "../escape"
            archive.addfile(item)
        linked_output = root / "link-output"
        linked_output.mkdir()
        rejected(linked, linked_output)
        try:
            extract_single_member(linked, "link", maximum=1024)
        except ArchiveSafetyError:
            pass
        else:
            raise AssertionError("symlink was accepted as a regular archive member")

        oversized = root / "oversized.tar.gz"
        with tarfile.open(oversized, "w:gz") as archive:
            member(archive, "large", b"x" * 1025)
        oversized_output = root / "oversized-output"
        oversized_output.mkdir()
        rejected(oversized, oversized_output)

        nonempty = root / "nonempty"
        nonempty.mkdir()
        (nonempty / "existing").write_text("keep", encoding="utf-8")
        rejected(valid, nonempty)
        assert (nonempty / "existing").read_text(encoding="utf-8") == "keep"


if __name__ == "__main__":
    main()
