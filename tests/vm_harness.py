#!/usr/bin/env python3
"""Static and argument-contract checks for the disposable headless VM runner."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests/vm/run.sh"
GUEST = ROOT / "tests/vm/guest-checks.sh"
IGNORE = ROOT / "tests/vm/.gitignore"


def invoke(*arguments):
    return subprocess.run(
        [str(RUNNER), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def main():
    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "--no-image-download" in help_result.stdout

    malformed = invoke("--unknown")
    assert malformed.returncode == 2
    assert "unknown argument" in malformed.stderr

    runner = RUNNER.read_text(encoding="utf-8")
    guest = GUEST.read_text(encoding="utf-8")
    ignored = IGNORE.read_text(encoding="utf-8")
    assert "e401a4db2e5e04d1967b6729774faa96da629bcf3ba90b67d8d9cce9906bec0f" in runner
    assert "sha256sum -c" in runner
    assert "-display none" in runner and "-serial" in runner
    assert "-nic user" in runner and "hostfwd" not in runner
    assert "2700" in runner and "20G" in runner
    assert "tests/transaction.sh" in guest
    assert "unshare --mount" in guest and "mkfs.btrfs" in guest
    assert '"schemaVersion":1' in guest
    for pattern in (".cache/", ".runtime/", "*.qcow2", "*.img", "*.iso", "*.log", "*.sock"):
        assert pattern in ignored


if __name__ == "__main__":
    main()
