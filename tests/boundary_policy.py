#!/usr/bin/env python3
"""Prevent accidental edits or disconnected copies of the ownership contract."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = "0b077301b473b6de9853ec9c7e211ab6da39df4e41e6a41b1f1a2cf68ac9753b"


def main():
    authority = ROOT / "BOUNDARIES.md"
    payload = authority.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256, (
        "BOUNDARIES.md changed without an explicit governance update"
    )
    text = payload.decode("utf-8")
    assert "READ-ONLY GOVERNANCE CONTRACT" in text
    assert "## Sole UI exception" in text
    assert "payload deployment, not a\nsource, build, or runtime dependency" in text
    for relative in ("README.md", "TODO.md", "docs/image-builder.md"):
        summary = (ROOT / relative).read_text(encoding="utf-8")
        assert "BOUNDARIES.md" in summary, f"{relative} does not link to the authority"


if __name__ == "__main__":
    main()
