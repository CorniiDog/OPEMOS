#!/usr/bin/env python3
"""Contain one injected device-generation transport process group."""

import argparse
import os
import selectors
import signal
import subprocess
import sys


OWNER_GONE = 125
WATCHDOG_FAILED = 126


class StopWatchdog(Exception):
    """The lifecycle owner or an external signal requested teardown."""


def stop(_signum, _frame):
    raise StopWatchdog()


def terminate_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def parser():
    value = argparse.ArgumentParser(add_help=False)
    value.add_argument("--control-fd", required=True, type=int)
    value.add_argument("--transport", required=True)
    value.add_argument("--destination", required=True)
    return value


def main():
    arguments = parser().parse_args()
    if arguments.control_fd < 3:
        return WATCHDOG_FAILED
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    process = None
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(arguments.control_fd, False)
        selector.register(arguments.control_fd, selectors.EVENT_READ)
        process = subprocess.Popen(
            [arguments.transport, "--destination", arguments.destination],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
        )
        while process.poll() is None:
            for _key, _events in selector.select(0.1):
                try:
                    payload = os.read(arguments.control_fd, 1)
                except BlockingIOError:
                    continue
                if payload == b"":
                    raise StopWatchdog()
                return WATCHDOG_FAILED
        return process.returncode
    except StopWatchdog:
        return OWNER_GONE
    except OSError:
        return WATCHDOG_FAILED
    finally:
        if process is not None:
            terminate_group(process)
        selector.close()
        try:
            os.close(arguments.control_fd)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
