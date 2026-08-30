#!/usr/bin/env python3
"""Contract tests for reviewed Valve signer activation and revocation."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib" / "validate_valve_signer.py"
MANIFEST = ROOT / "trust" / "valve-package-signers.json"
FINGERPRINT = "889B5EBDDD505A683621900DAF1D2199EF0A3CCF"


def validate(manifest, fingerprint):
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--manifest",
            str(manifest),
            "--fingerprint",
            fingerprint,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def main():
    active = validate(MANIFEST, FINGERPRINT.lower())
    assert active.returncode == 0
    assert active.stdout.strip() == FINGERPRINT

    absent = validate(MANIFEST, "0" * 40)
    assert absent.returncode != 0

    with tempfile.TemporaryDirectory(prefix="trust-policy-") as temporary:
        revoked_manifest = Path(temporary) / "revoked.json"
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        document["signers"][0]["status"] = "revoked"
        document["signers"][0]["revokedAt"] = "2026-08-30"
        revoked_manifest.write_text(json.dumps(document), encoding="utf-8")
        revoked = validate(revoked_manifest, FINGERPRINT)
        assert revoked.returncode != 0


if __name__ == "__main__":
    main()
