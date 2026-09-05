#!/usr/bin/env python3
"""Prevent large host workflows from regressing to root-backed /tmp."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_LARGE_WORKFLOWS = (
    "bootstrap/compile.sh",
    "bootstrap/compile_online.sh",
    "bootstrap/install.sh",
    "bootstrap/install_upstream.sh",
    "bootstrap/online_commit.sh",
    "bootstrap/online_dev.sh",
    "bootstrap/online_install.sh",
    "bootstrap/online_setup_nvidia.sh",
    "bootstrap/setup_nvidia.sh",
    "bootstrap/uninstall.sh",
)
FORBIDDEN = ("/tmp/", "mktemp /tmp", "${TMPDIR:-/tmp}")

def violations(text: str):
    return [token for token in FORBIDDEN if token in text]

def main() -> None:
    rejected = {}
    for relative in HOST_LARGE_WORKFLOWS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        found = violations(text)
        if found:
            rejected[relative] = found
    assert not rejected, f"large host workflows use root-backed temporary storage: {rejected}"

    container_build = (ROOT / "bootstrap/build.sh").read_text(encoding="utf-8")
    assert '/tmp/$HEADERS_FILENAME' in container_build
    assert "Fedora container-local /tmp" in container_build
    assert "Rootless Podman stores it under" in container_build

    assert violations('WORK="$(mktemp -d /tmp/large.XXXXXX)"')
    assert violations('WORK="$(mktemp -d ${TMPDIR:-/tmp}/large.XXXXXX)"')
    assert not violations('WORK="$(mktemp -d ${HOME}/.cache/project/large.XXXXXX)"')

if __name__ == "__main__":
    main()
