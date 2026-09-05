#!/usr/bin/env python3
"""Run an opaque installer operation with bounded semantic heartbeats."""
import argparse
import json
import subprocess
import sys

ALLOWED_PHASES = ("initramfs",)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=ALLOWED_PHASES, required=True)
    parser.add_argument("--progress-attempt", type=int, default=0)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("an operation command is required")
    if not 0 <= args.progress_attempt <= 1_000_000:
        parser.error("progress attempt must be between 0 and 1000000")
    if not 0.01 <= args.heartbeat_seconds <= 300.0:
        parser.error("heartbeat interval is outside the supported range")
    return args


def emit_heartbeat(phase, attempt):
    document = {
        "attempt": attempt,
        "indeterminate": True,
        "phase": phase,
        "schemaVersion": 1,
    }
    sys.stderr.write(
        "STEAMOS_NVIDIA_PROGRESS "
        + json.dumps(document, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    sys.stderr.flush()


def main():
    args = arguments()
    try:
        process = subprocess.Popen(args.command)
    except OSError:
        return 127
    while True:
        try:
            return process.wait(timeout=args.heartbeat_seconds)
        except subprocess.TimeoutExpired:
            emit_heartbeat(args.phase, args.progress_attempt)


if __name__ == "__main__":
    raise SystemExit(main())
