#!/usr/bin/env python3
"""Compatibility tests for bounded OpenPGP verification status."""

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from userspace_lock_generation_contract import (  # noqa: E402
    GenerationContractError,
    MAX_OPENPGP_STATUS_BYTES,
    validate_openpgp_status,
)


GENERATOR = ROOT / "lib/generate_openpgp_status_fixtures.py"


def accepted(payload, fingerprint):
    try:
        validate_openpgp_status(payload, fingerprint)
        return True
    except GenerationContractError:
        return False


def main():
    first = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd="/", check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    second = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd="/", check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert first.stderr == second.stderr == b""
    assert first.stdout == second.stdout and first.stdout.endswith(b"\n")
    matrix = json.loads(first.stdout)
    assert set(matrix) == {
        "schemaVersion", "kind", "signatureScheme",
        "expectedPrimaryFingerprint", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["signatureScheme"] == "openpgp-detached-v1"
    assert matrix["limits"] == {
        "maxStatusBytes": MAX_OPENPGP_STATUS_BYTES, "maxCases": 32,
    }
    base = {
        item["name"]: item["status"] for item in matrix["cases"]
        if "status" in item
    }
    names = []
    for fixture in matrix["cases"]:
        assert set(fixture) <= {"name", "expected", "status", "statusRecipe"}
        names.append(fixture["name"])
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", fixture["name"])
        if "status" in fixture:
            payload = fixture["status"].encode("utf-8")
        else:
            recipe = fixture["statusRecipe"]
            assert recipe["kind"] == "append-padding"
            payload = (
                base[recipe["baseCase"]] + "X" * recipe["paddingBytes"]
            ).encode("utf-8")
        assert accepted(
            payload, matrix["expectedPrimaryFingerprint"]
        ) is fixture["expected"]["accepted"], fixture["name"]
    assert len(names) == len(set(names)) and len(names) <= 32
    assert not accepted(b"\xff", matrix["expectedPrimaryFingerprint"])
    assert not accepted(base["valid-sha256-subkey"].encode(), "a" * 40)


if __name__ == "__main__":
    main()
