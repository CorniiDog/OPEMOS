#!/usr/bin/env python3
"""Run SteamOS Gamescope, falling back to software Plasma on Vulkan failure."""

import os
import re
import subprocess
import sys


GAMESCOPE = "/usr/bin/start-gamescope-session"
JOURNALCTL = "/usr/bin/journalctl"
LOGGER = "/usr/bin/logger"
PLASMA_RUNNER = "/usr/lib/plasma-dbus-run-session-if-needed"
PLASMA = "/usr/bin/startplasma-wayland"
MAX_JOURNAL_BYTES = 1024 * 1024
CURSOR = re.compile(r"^-- cursor: (\S{1,4096})$", re.MULTILINE)


def run_bounded(arguments: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(arguments, 127, b"")
    if len(completed.stdout) > MAX_JOURNAL_BYTES:
        return subprocess.CompletedProcess(arguments, 125, b"")
    return completed


def journal_cursor() -> str | None:
    completed = run_bounded(
        [JOURNALCTL, "--user", "--no-pager", "-n", "0", "--show-cursor"]
    )
    if completed.returncode != 0:
        return None
    try:
        output = completed.stdout.decode("utf-8", "replace")
    except AttributeError:
        return None
    match = CURSOR.search(output)
    return match.group(1) if match else None


def journal_after(cursor: str | None) -> str:
    if cursor is None:
        return ""
    completed = run_bounded(
        [JOURNALCTL, "--user", "--no-pager", "--output=cat", f"--after-cursor={cursor}"]
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", "replace")


def has_vulkan_initialization_failure(journal: str) -> bool:
    lowered = journal.lower()
    device_failure = (
        "vdrm_device_connect failed" in lowered
        and "failed to initialize device" in lowered
    )
    dispatch_assertion = (
        "createdispatchtable" in lowered
        and ("assert" in lowered or "abrt" in lowered or "core dump" in lowered)
    )
    return device_failure or dispatch_assertion


def plasma_environment(source: dict[str, str]) -> dict[str, str]:
    environment = dict(source)
    environment.update(
        {
            "DESKTOP_SESSION": "plasma",
            "GALLIUM_DRIVER": "llvmpipe",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "XDG_CURRENT_DESKTOP": "KDE",
            "XDG_SESSION_DESKTOP": "KDE",
            "XDG_SESSION_TYPE": "wayland",
        }
    )
    environment.pop("XDG_DESKTOP_PORTAL_DIR", None)
    return environment


def main() -> int:
    cursor = journal_cursor()
    try:
        gamescope = subprocess.run([GAMESCOPE], check=False)
    except OSError as error:
        print(f"OPEMOS could not launch the SteamOS Gamescope session: {error}", file=sys.stderr)
        return 126
    journal = journal_after(cursor)
    if not has_vulkan_initialization_failure(journal):
        return gamescope.returncode
    message = (
        "Gamescope Vulkan initialization failed; starting the existing Plasma "
        "Wayland session with software rendering"
    )
    print(f"OPEMOS: {message}.", file=sys.stderr)
    run_bounded([LOGGER, "-t", "opemos-gamescope-fallback", message])
    try:
        os.execve(
            PLASMA_RUNNER,
            [PLASMA_RUNNER, PLASMA],
            plasma_environment(os.environ),
        )
    except OSError as error:
        print(f"OPEMOS could not launch the visible Plasma fallback: {error}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
