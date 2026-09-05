#!/usr/bin/env python3
"""Run pacman in a process group, emit safe heartbeats, and detect hook failures."""
import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from atomic_output import atomic_write_bytes

HOOK_FAILURE_MARKERS = (b"error: command failed to execute correctly", b"error: failed to run transaction hooks")
MAX_CARRY = max(map(len, HOOK_FAILURE_MARKERS)) - 1

def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--progress-attempt", type=int, default=0)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a pacman command is required")
    if not 0 <= args.progress_attempt <= 1_000_000:
        parser.error("progress attempt must be between 0 and 1000000")
    if not 0.01 <= args.heartbeat_seconds <= 300.0:
        parser.error("heartbeat interval is outside the supported range")
    return args

def emit_heartbeat(attempt):
    document = {"attempt": attempt, "indeterminate": True, "phase": "userspace_install", "schemaVersion": 1}
    sys.stderr.write("STEAMOS_NVIDIA_PROGRESS " + json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stderr.flush()

def main():
    args = arguments()
    try:
        os.setsid()
    except OSError:
        pass
    try:
        process = subprocess.Popen(args.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "LANG": "C", "LC_ALL": "C"}, bufsize=0)
    except OSError:
        atomic_write_bytes(args.output, b'{"exitStatus":127,"hookFailure":false,"reason":"userspace_transaction_failed","schemaVersion":1,"status":"failed"}\n')
        return 127
    hook_failure = False
    carry = b""
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + args.heartbeat_seconds
    try:
        while True:
            events = selector.select(max(0.0, deadline - time.monotonic()))
            if not events:
                emit_heartbeat(args.progress_attempt)
                deadline = time.monotonic() + args.heartbeat_seconds
                continue
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            deadline = time.monotonic() + args.heartbeat_seconds
            inspected = carry + chunk
            hook_failure = hook_failure or any(marker in inspected for marker in HOOK_FAILURE_MARKERS)
            carry = inspected[-MAX_CARRY:]
    finally:
        selector.close()
        process.stdout.close()
    exit_status = process.wait()
    document = {
        "schemaVersion": 1,
        "status": "failed" if hook_failure or exit_status else "verified",
        "reason": "userspace_hook_failed" if hook_failure else ("userspace_transaction_failed" if exit_status else "userspace_transaction_complete"),
        "exitStatus": exit_status,
        "hookFailure": hook_failure,
    }
    atomic_write_bytes(args.output, (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return exit_status

if __name__ == "__main__":
    raise SystemExit(main())
