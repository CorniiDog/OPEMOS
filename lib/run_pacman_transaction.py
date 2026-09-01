#!/usr/bin/env python3
"""Run pacman in a process group and detect non-fatal post-hook failures."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from atomic_output import atomic_write_bytes


HOOK_FAILURE_MARKERS = (
    b"error: command failed to execute correctly",
    b"error: failed to run transaction hooks",
)
MAX_CARRY = max(map(len, HOOK_FAILURE_MARKERS)) - 1


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a pacman command is required")
    return args


def main():
    args = arguments()
    try:
        os.setsid()
    except OSError:
        # install_to_root launches this helper through run_in_process_group.py,
        # so it is already the process-group leader in normal operation.
        pass
    try:
        process = subprocess.Popen(
            args.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
    except OSError:
        atomic_write_bytes(
            args.output,
            b'{"exitStatus":127,"hookFailure":false,"reason":'
            b'"userspace_transaction_failed","schemaVersion":1,"status":"failed"}\n',
        )
        return 127
    hook_failure = False
    carry = b""
    assert process.stdout is not None
    try:
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            inspected = carry + chunk
            hook_failure = hook_failure or any(
                marker in inspected for marker in HOOK_FAILURE_MARKERS
            )
            carry = inspected[-MAX_CARRY:]
    finally:
        process.stdout.close()
    exit_status = process.wait()
    document = {
        "schemaVersion": 1,
        "status": "failed" if hook_failure or exit_status else "verified",
        "reason": "userspace_hook_failed" if hook_failure else (
            "userspace_transaction_failed" if exit_status else "userspace_transaction_complete"
        ),
        "exitStatus": exit_status,
        "hookFailure": hook_failure,
    }
    atomic_write_bytes(
        args.output,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
