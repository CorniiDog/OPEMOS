#!/usr/bin/env python3
"""Portable control-pipe and process-group tests for the transport watchdog."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "lib/device_generation_transport_watchdog.py"


def write(path, payload):
    path.write_bytes(payload)
    path.chmod(0o700)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_dead(pids):
    deadline = time.time() + 5
    while time.time() < deadline and any(alive(pid) for pid in pids):
        time.sleep(0.02)
    assert not any(alive(pid) for pid in pids)


def main():
    with tempfile.TemporaryDirectory(prefix="opemos-transport-watchdog-") as name:
        root = Path(name)
        destination = root / "destination"
        destination.mkdir()
        pid_file = root / "pids"
        transport = root / "transport"
        write(transport, (
            f"#!{sys.executable}\n"
            "import os, pathlib, subprocess, sys, time\n"
            f"pid_file = pathlib.Path({str(pid_file)!r})\n"
            "child = subprocess.Popen(['/bin/sleep', '30'])\n"
            "pid_file.write_text(f'{os.getpid()} {child.pid}\\n')\n"
            "time.sleep(30)\n"
        ).encode())
        control_read, control_write = os.pipe()
        watchdog = subprocess.Popen([
            sys.executable, str(WATCHDOG), "--control-fd", str(control_read),
            "--transport", str(transport), "--destination", str(destination),
        ], pass_fds=(control_read,), stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, text=True, start_new_session=True)
        os.close(control_read)
        deadline = time.time() + 5
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        pids = [int(value) for value in pid_file.read_text().split()]
        assert all(alive(pid) for pid in pids)
        os.close(control_write)
        stdout, stderr = watchdog.communicate(timeout=5)
        assert watchdog.returncode == 125 and stdout == "" and stderr == ""
        wait_dead(pids)

        quick = root / "quick"
        write(quick, b"#!/bin/sh\nexit 69\n")
        control_read, control_write = os.pipe()
        completed = subprocess.Popen([
            sys.executable, str(WATCHDOG), "--control-fd", str(control_read),
            "--transport", str(quick), "--destination", str(destination),
        ], pass_fds=(control_read,), stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, text=True, start_new_session=True)
        os.close(control_read)
        stdout, stderr = completed.communicate(timeout=5)
        os.close(control_write)
        assert completed.returncode == 69 and stdout == "" and stderr == ""


if __name__ == "__main__":
    main()
