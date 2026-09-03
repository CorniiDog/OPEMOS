#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = "d6084c18fb9dbf4076ffc800f499d265078bf4a74ef3386e04199a86904cdb42"


def main():
    authority = ROOT / "BOUNDARIES.md"
    payload = authority.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256, (
        "BOUNDARIES.md changed without an explicit governance update"
    )
    text = payload.decode("utf-8")
    assert "READ-ONLY GOVERNANCE CONTRACT" in text
    assert "## Sole UI exception" in text
    assert "The OPEMOS repository—not OPEMOS.EXE—owns and implements the fullscreen" in text
    assert "sibling consumer of Core progress and state contracts" in text
    assert "## Networking boundary" in text
    assert "## Source intent and Core authorization" in text
    assert "## A/B ownership" in text
    assert "This ownership is cross-platform" in text
    assert "must not fork, rewrite, import, link,\nor execute" in text
    for relative in ("README.md", "TODO.md", "docs/image-builder.md"):
        summary = (ROOT / relative).read_text(encoding="utf-8")
        assert "BOUNDARIES.md" in summary, f"{relative} does not link to the authority"


if __name__ == "__main__":
    main()
