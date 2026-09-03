#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COUNTERPART_COMMIT = "c6733c7c80a104f57b44411d2d4223c2d624818d"
EXPECTED_GIT_BLOB = "a8123b2134a3b6ed536353ab16ed9496ba263c01"
EXPECTED_SHA256 = "3d995e054dbad65f871dfbf20234d5be7977a54eba765b10635d09a954d01bbb"


def git_blob_id(payload):
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def verify_counterpart_commit(payload):
    sibling = ROOT.parent / "steamos-nvidia-image-builder"
    if not (sibling / ".git").exists():
        return
    result = subprocess.run(
        ["git", "-C", str(sibling), "show", f"{COUNTERPART_COMMIT}:BOUNDARIES.md"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, "pinned OPEMOS.EXE boundary commit is unavailable"
    assert result.stdout == payload, "Core boundary differs from the pinned OPEMOS.EXE mirror"
    assert hashlib.sha256(result.stdout).hexdigest() == EXPECTED_SHA256


def main():
    authority = ROOT / "BOUNDARIES.md"
    payload = authority.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256, (
        "BOUNDARIES.md changed without an explicit governance update"
    )
    assert git_blob_id(payload) == EXPECTED_GIT_BLOB, (
        "BOUNDARIES.md is not the exact cross-project governance blob"
    )
    verify_counterpart_commit(payload)
    text = payload.decode("utf-8")
    assert "READ-ONLY GOVERNANCE CONTRACT" in text
    assert "## Sole UI exception" in text
    assert "The OPEMOS repository—not OPEMOS.EXE—owns and implements the fullscreen" in text
    assert "sibling consumer of Core progress and state contracts" in text
    assert "## Networking boundary" in text
    assert "## Source intent and Core authorization" in text
    assert "## A/B ownership" in text
    assert "This ownership is cross-platform" in text
    assert "Automatic is itself explicit user intent" in text
    assert "authenticated OPEMOS-owned\ninterstitial target payload" in text
    assert "Core-owned installed-device supervisor may launch and\nmonitor" in text
    for relative in ("README.md", "TODO.md", "docs/image-builder.md"):
        summary = (ROOT / relative).read_text(encoding="utf-8")
        assert "BOUNDARIES.md" in summary, f"{relative} does not link to the authority"


if __name__ == "__main__":
    main()
