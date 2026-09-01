#!/usr/bin/env python3
"""Failure, resource-cap, cleanup, and repeat tests for bounded capture."""

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib/capture_bounded_command.py"


def invoke(output, limit, timeout, code):
    return subprocess.run([str(HELPER), "--output", str(output), "--max-bytes", str(limit),
                           "--timeout", str(timeout), "--", sys.executable, "-c", code],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    with tempfile.TemporaryDirectory(prefix="bounded-capture-") as temporary:
        root = Path(temporary)
        first = root / "first"
        assert invoke(first, 64, 5, "print('listing')").returncode == 0
        assert first.read_text() == "listing\n"
        second = root / "second"
        assert invoke(second, 64, 5, "print('listing')").returncode == 0
        assert first.read_bytes() == second.read_bytes()
        excessive = root / "excessive"
        assert invoke(excessive, 8, 5, "print('x' * 100)").returncode != 0
        assert not excessive.exists()
        timeout = root / "timeout"
        assert invoke(timeout, 64, 0.1, "import time; time.sleep(30)").returncode != 0
        assert not timeout.exists()
        failed = root / "failed"
        assert invoke(failed, 64, 5, "raise SystemExit(7)").returncode != 0
        assert not failed.exists()


if __name__ == "__main__":
    main()
