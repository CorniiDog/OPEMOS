#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = "d2d56140c3f94411edfc8610f7cc8126fa26c1fc27b6307e0c1af7f4150516fa"


def main():
    authority = ROOT / "BOUNDARIES.md"
    payload = authority.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256, (
        "BOUNDARIES.md changed without an explicit governance update"
    )
    text = payload.decode("utf-8")
    assert "READ-ONLY GOVERNANCE CONTRACT" in text
    assert "## Sole UI exception" in text
    assert "OPEMOS Core—not OPEMOS.EXE—owns and implements the fullscreen" in text
    assert "must not fork, rewrite, import, link,\nor execute" in text
    for relative in ("README.md", "TODO.md", "docs/image-builder.md"):
        summary = (ROOT / relative).read_text(encoding="utf-8")
        assert "BOUNDARIES.md" in summary, f"{relative} does not link to the authority"


if __name__ == "__main__":
    main()
