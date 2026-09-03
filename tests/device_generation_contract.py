#!/usr/bin/env python3
"""Canonical device-generation result and health compatibility tests."""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from device_generation_contract import (  # noqa: E402
    MAX_DOCUMENT_BYTES,
    DeviceGenerationContractError,
    canonical,
    strict_json,
    validate_health,
    validate_result,
)


GENERATOR = ROOT / "lib/generate_device_generation_fixtures.py"


def accepted(payload, kind, active):
    try:
        document = strict_json(payload)
        if kind == "result":
            validate_result(document)
        else:
            validate_health(document, active)
    except DeviceGenerationContractError:
        return False
    return True


def main():
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(GENERATOR)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(outputs[0])
    assert set(matrix) == {
        "schemaVersion", "kind", "resultSchemaVersion", "healthSchemaVersion",
        "activeIdentity", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == "opemos-device-generation-compatibility-fixtures"
    assert matrix["resultSchemaVersion"] == matrix["healthSchemaVersion"] == 1
    assert matrix["limits"] == {
        "maxDocumentBytes": MAX_DOCUMENT_BYTES, "maxCases": 64,
    }
    cases = matrix["cases"]
    assert 1 <= len(cases) <= matrix["limits"]["maxCases"]
    by_name = {
        case["name"]: copy.deepcopy(case) for case in cases
        if "document" in case
    }
    names = []
    for case in cases:
        assert set(case) in (
            {"name", "kind", "expected", "document"},
            {"name", "kind", "expected", "rawDocument"},
            {"name", "kind", "expected", "documentRecipe"},
        )
        names.append(case["name"])
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["name"])
        assert case["kind"] in {"result", "health"}
        assert set(case["expected"]) == {"accepted"}
        assert type(case["expected"]["accepted"]) is bool
        if "document" in case:
            payload = canonical(case["document"])
        elif "rawDocument" in case:
            payload = case["rawDocument"].encode()
        else:
            recipe = case["documentRecipe"]
            assert set(recipe) == {"kind", "baseCase", "paddingBytes"}
            assert recipe["kind"] == "top-level-padding"
            document = by_name[recipe["baseCase"]]["document"]
            document["padding"] = "x" * recipe["paddingBytes"]
            payload = canonical(document)
        actual = accepted(payload, case["kind"], matrix["activeIdentity"])
        assert actual is case["expected"]["accepted"], case["name"]
    assert len(names) == len(set(names))
    assert {
        "valid-empty-state", "valid-pending-state", "valid-healthy-state",
        "valid-rollback-high-water", "health-wrong-active-binding",
        "pending-equals-lkg", "healthy-differs-from-lkg",
        "duplicate-json-key", "non-finite-json", "oversized-document",
    } <= set(names)


if __name__ == "__main__":
    main()
