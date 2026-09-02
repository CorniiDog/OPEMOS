#!/usr/bin/env python3
"""Hostile destination-race tests for immutable installer snapshots."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from snapshot_install_input import snapshot  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="install-input-race-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.write_bytes(b"authenticated input\n")

        destination = root / "snapshot"
        replacement = b"attacker-controlled replacement\n"
        real_fsync = os.fsync

        def replace_after_write(descriptor):
            real_fsync(descriptor)
            destination.unlink()
            destination.write_bytes(replacement)

        with mock.patch("snapshot_install_input.os.fsync", replace_after_write):
            try:
                snapshot(source, destination, 1024)
            except OSError as error:
                assert "destination changed" in str(error)
            else:
                raise AssertionError("snapshot accepted a replaced destination")
        assert destination.read_bytes() == replacement, (
            "failure cleanup removed or changed a foreign replacement"
        )

        hardlinked = root / "hardlinked-snapshot"
        alias = root / "snapshot-alias"

        def link_after_write(descriptor):
            real_fsync(descriptor)
            os.link(hardlinked, alias)

        with mock.patch("snapshot_install_input.os.fsync", link_after_write):
            try:
                snapshot(source, hardlinked, 1024)
            except OSError as error:
                assert "destination changed" in str(error)
            else:
                raise AssertionError("snapshot accepted an externally linked destination")
        assert not hardlinked.exists(), "owned failed snapshot was not cleaned"
        assert alias.read_bytes() == source.read_bytes()

        fifo = root / "hostile-fifo"
        os.mkfifo(fifo)
        try:
            snapshot(fifo, root / "fifo-snapshot", 1024)
        except OSError as error:
            assert "not a regular file" in str(error)
        else:
            raise AssertionError("snapshot accepted a FIFO input")
        assert not (root / "fifo-snapshot").exists()

        changing_source = root / "changing-source"
        changing_source.write_bytes(b"initial authenticated bytes\n")
        changing_destination = root / "changing-snapshot"

        def mutate_source_after_write(descriptor):
            real_fsync(descriptor)
            changing_source.write_bytes(b"changed while copying\n")

        with mock.patch(
            "snapshot_install_input.os.fsync", mutate_source_after_write
        ):
            try:
                snapshot(changing_source, changing_destination, 1024)
            except OSError as error:
                assert "changed during snapshot" in str(error)
            else:
                raise AssertionError("snapshot accepted a changing source")
        assert not changing_destination.exists()


if __name__ == "__main__":
    main()
