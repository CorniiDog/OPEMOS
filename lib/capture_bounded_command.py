#!/usr/bin/env python3
"""Capture command stdout with strict byte and wall-clock bounds."""

import argparse
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from atomic_output import atomic_create_bytes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-bytes", required=True, type=int)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if (not args.command or not 1 <= args.max_bytes <= 64 * 1024 * 1024
            or not 0 < args.timeout <= 300):
        parser.error("a command and safe capture bounds are required")
    process = subprocess.Popen(args.command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    def forward(signum, _frame):
        try:
            os.killpg(process.pid, signum)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + args.timeout
    output = bytearray()
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            events = selector.select(min(remaining, 1.0))
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > args.max_bytes:
                    raise OverflowError
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code != 0 or not output:
            raise subprocess.CalledProcessError(return_code, args.command)
        atomic_create_bytes(args.output, bytes(output), mode=0o600)
    except (OSError, OverflowError, TimeoutError, subprocess.SubprocessError, FileExistsError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        raise SystemExit("capture_bounded_command.py: command capture failed")
    finally:
        selector.close()


if __name__ == "__main__":
    main()
