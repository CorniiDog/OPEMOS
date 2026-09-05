#!/usr/bin/env python3
"""Focused safe-heartbeat tests for the opaque pacman transaction runner."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "lib/run_pacman_transaction.py"
PREFIX = "STEAMOS_NVIDIA_PROGRESS "

def main():
    with tempfile.TemporaryDirectory(prefix="pacman-heartbeat-") as name:
        output = Path(name) / "result.json"
        completed = subprocess.run(
            [sys.executable, str(RUNNER), "--output", str(output), "--progress-attempt", "27",
             "--heartbeat-seconds", "0.02", "--", sys.executable, "-c",
             "import time; time.sleep(.07); print('transaction complete')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert completed.returncode == 0
        assert completed.stdout == "transaction complete\n"
        records = [json.loads(line[len(PREFIX):]) for line in completed.stderr.splitlines() if line.startswith(PREFIX)]
        assert len(records) >= 2
        expected = {"attempt": 27, "indeterminate": True, "phase": "userspace_install", "schemaVersion": 1}
        assert all(record == expected for record in records)
        active = subprocess.run(
            [sys.executable, str(RUNNER), "--output", str(output),
             "--heartbeat-seconds", "0.10", "--", sys.executable, "-c",
             "import time; [(print(i, flush=True), time.sleep(.02)) for i in range(4)]"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert active.returncode == 0
        assert not any(line.startswith(PREFIX) for line in active.stderr.splitlines())

        assert json.loads(output.read_text()) == {
            "exitStatus": 0, "hookFailure": False, "reason": "userspace_transaction_complete",
            "schemaVersion": 1, "status": "verified",
        }
        for value in ("-1", "1000001"):
            rejected = subprocess.run(
                [sys.executable, str(RUNNER), "--output", str(output), "--progress-attempt", value, "--", "true"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert rejected.returncode == 2 and rejected.stdout == b""
        for value in ("0", "301", "nan"):
            rejected = subprocess.run(
                [sys.executable, str(RUNNER), "--output", str(output), "--heartbeat-seconds", value, "--", "true"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert rejected.returncode == 2 and rejected.stdout == b""

if __name__ == "__main__":
    main()
