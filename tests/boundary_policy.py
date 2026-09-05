#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import argparse
import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COUNTERPART_COMMIT = "064d1d54c7ef2eda3d56e80c67e9f8e78a554725"
EXPECTED_GIT_BLOB = "68fd9553bb8fee79cee803a38f980a94b2d80e57"
EXPECTED_SHA256 = "136d3572effa90c1b84bcf51002d7f9641c367132de20d54dd7173f68f13c6a8"


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


def main(local_only=False):
    authority = ROOT / "BOUNDARIES.md"
    payload = authority.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256, (
        "BOUNDARIES.md changed without an explicit governance update"
    )
    assert git_blob_id(payload) == EXPECTED_GIT_BLOB, (
        "BOUNDARIES.md is not the exact cross-project governance blob"
    )
    if not local_only:
        verify_counterpart_commit(payload)
    text = payload.decode("utf-8")
    assert "READ-ONLY GOVERNANCE CONTRACT" in text
    assert "## Sole UI exception" in text
    assert "The OPEMOS repository—not OPEMOS.EXE—owns and implements the fullscreen" in text
    assert "sibling consumer of Core progress and state contracts" in text
    assert "## Networking boundary" in text
    assert "## Source intent and Core authorization" in text
    assert "## A/B ownership" in text
    assert "## Artifact cleanup ownership" in text
    assert "Artifact cleanup follows creator ownership" in text
    assert "Missing, stale, malformed, mismatched," in text
    assert "The flag grants\nno blanket deletion authority" in text
    assert "This ownership is cross-platform" in text
    assert "Automatic is itself explicit user intent" in text
    assert "authenticated OPEMOS-owned\ninterstitial target payload" in text
    assert "Core-owned installed-device supervisor may launch and\nmonitor" in text
    for relative in ("README.md", "TODO.md", "docs/image-builder.md"):
        summary = (ROOT / relative).read_text(encoding="utf-8")
        assert "BOUNDARIES.md" in summary, f"{relative} does not link to the authority"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true",
                        help="verify canonical Core bytes without the optional local EXE checkout")
    main(parser.parse_args().local_only)
