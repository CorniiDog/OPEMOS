#!/usr/bin/env python3
"""Contract tests for symlink-safe atomic result publication."""

import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from atomic_output import atomic_create_bytes, atomic_write_bytes  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="atomic-output-") as temporary:
        root = Path(temporary)
        output = root / "result.json"
        atomic_write_bytes(output, b"first\n")
        assert output.read_bytes() == b"first\n"
        assert stat.S_IMODE(output.stat().st_mode) == 0o644
        atomic_write_bytes(output, b"second\n")
        assert output.read_bytes() == b"second\n"

        victim = root / "victim"
        victim.write_bytes(b"keep\n")
        output.unlink()
        output.symlink_to(victim)
        atomic_write_bytes(output, b"replacement\n")
        assert not output.is_symlink()
        assert output.read_bytes() == b"replacement\n"
        assert victim.read_bytes() == b"keep\n"

        immutable = root / "immutable.json"
        atomic_create_bytes(immutable, b"created\n")
        try:
            atomic_create_bytes(immutable, b"clobber\n")
        except FileExistsError:
            pass
        else:
            raise AssertionError("create-only output was overwritten")
        assert immutable.read_bytes() == b"created\n"

        symlink = root / "immutable-link.json"
        symlink.symlink_to(victim)
        try:
            atomic_create_bytes(symlink, b"clobber\n")
        except FileExistsError:
            pass
        else:
            raise AssertionError("create-only output replaced a symlink")
        assert victim.read_bytes() == b"keep\n"


if __name__ == "__main__":
    main()
