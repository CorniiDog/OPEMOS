#!/usr/bin/env python3
"""Focused semantic heartbeat tests for opaque installer operations."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "lib/run_opaque_operation.py"
PREFIX = "STEAMOS_NVIDIA_PROGRESS "


def invoke(*arguments):
    return subprocess.run(
        [sys.executable, str(RUNNER), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def main():
    completed = invoke(
        "--phase", "initramfs", "--progress-attempt", "41",
        "--heartbeat-seconds", "0.02", "--", sys.executable, "-c",
        "import time; print('mkinitcpio output', flush=True); time.sleep(.07)",
    )
    assert completed.returncode == 0
    assert completed.stdout == "mkinitcpio output\n"
    records = [
        json.loads(line[len(PREFIX):])
        for line in completed.stderr.splitlines()
        if line.startswith(PREFIX)
    ]
    assert len(records) >= 2
    expected = {
        "attempt": 41,
        "indeterminate": True,
        "phase": "initramfs",
        "schemaVersion": 1,
    }
    assert all(record == expected for record in records)

    failed = invoke(
        "--phase", "initramfs", "--heartbeat-seconds", "0.02",
        "--", sys.executable, "-c", "import time; time.sleep(.03); raise SystemExit(23)",
    )
    assert failed.returncode == 23
    assert any(line.startswith(PREFIX) for line in failed.stderr.splitlines())

    for arguments in (
        ("--phase", "future_phase", "--", "true"),
        ("--phase", "initramfs", "--progress-attempt", "-1", "--", "true"),
        ("--phase", "initramfs", "--progress-attempt", "1000001", "--", "true"),
        ("--phase", "initramfs", "--heartbeat-seconds", "0", "--", "true"),
        ("--phase", "initramfs", "--heartbeat-seconds", "301", "--", "true"),
        ("--phase", "initramfs", "--heartbeat-seconds", "nan", "--", "true"),
    ):
        rejected = invoke(*arguments)
        assert rejected.returncode == 2
        assert rejected.stdout == ""

    missing = invoke("--phase", "initramfs", "--", "definitely-not-an-operation")
    assert missing.returncode == 127
    assert missing.stdout == ""
    assert not any(line.startswith(PREFIX) for line in missing.stderr.splitlines())


if __name__ == "__main__":
    main()
