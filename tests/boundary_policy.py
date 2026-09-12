#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import argparse
import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COUNTERPART_COMMIT = "507e23cf848cde3c74390f7e6c41ba09f9084a15"
EXPECTED_GIT_BLOB = "9b379788b1deadbb2088887eb10be325008254ac"
EXPECTED_SHA256 = "c44a987b4931f413ee72cc6d94ff3797f746bbdba4bcf51c7d7aed9406ffd9f2"
COUNTERPART_EXPECTED_SHA256 = "8c882b9a25e3d53fc200d82fff0807a8746dc826410271563d37342542c01df0"
MIRROR_SYNCHRONIZED = False


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
    counterpart_sha256 = hashlib.sha256(result.stdout).hexdigest()
    assert counterpart_sha256 == COUNTERPART_EXPECTED_SHA256
    if MIRROR_SYNCHRONIZED:
        assert result.stdout == payload, "Core boundary differs from the pinned OPEMOS.EXE mirror"
        assert counterpart_sha256 == EXPECTED_SHA256
    else:
        assert result.stdout != payload, "staged mirror unexpectedly matches canonical bytes"


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
    assert "## Cross-repository pull-request merge governance" in text
    assert "Only the owning repository primary lead may squash-merge" in text
    assert "Routine pull requests may merge without counterpart-primary approval" in text
    assert "Imaging-sensitive pull requests still require" in text
    assert "Do not open a standalone evidence-only pull request" in text
    assert "Any new head commit, changed base commit, material scope change" in text
    assert "the counterpart primary may instead record approval\nthrough the" in text
    assert "authenticated scheduler/handoff channel" in text
    assert "may delete only that exact merged topic branch" in text
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
