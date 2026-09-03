#!/usr/bin/env python3
"""Source-intent authorization and fail-closed CLI tests."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from generate_source_intent_fixtures import matrix  # noqa: E402
from source_intent_contract import canonical, decision  # noqa: E402


def main():
    first = canonical(matrix())
    generated = subprocess.run(
        [sys.executable, str(LIB / "generate_source_intent_fixtures.py")],
        cwd="/", check=True, stdout=subprocess.PIPE,
    ).stdout
    assert generated == first
    fixtures = json.loads(first)
    assert 10 <= len(fixtures["cases"]) <= fixtures["maxCases"]
    names = []
    for case in fixtures["cases"]:
        names.append(case["name"])
        actual = decision(case["intent"], case["releases"])
        assert actual["status"] == case["expected"]["status"], case["name"]
        assert actual["reason"] == case["expected"]["reason"], case["name"]
        if "actionKind" in case["expected"]:
            assert actual["action"]["kind"] == case["expected"]["actionKind"], case["name"]
        else:
            assert "action" not in actual, case["name"]
        if case["expected"]["reason"] == "source_intent_invalid":
            assert actual["target"] is None
        assert actual["intentSha256"] == __import__("hashlib").sha256(canonical(case["intent"])).hexdigest()
    assert len(names) == len(set(names))

    upstream = next(case for case in fixtures["cases"] if case["name"] == "explicit-upstream-development")
    action = decision(upstream["intent"], [])["action"]
    assert action["trust"] == "development-unverified"
    assert action["publicationPermitted"] is False
    assert action["source"] == upstream["intent"]["selection"]["source"]

    with tempfile.TemporaryDirectory(prefix="source-intent-") as name:
        root = Path(name)
        selected = fixtures["cases"][0]
        intent_path = root / "intent.json"
        releases_path = root / "releases.json"
        intent_path.write_bytes(canonical(selected["intent"]))
        releases_path.write_bytes(canonical(selected["releases"]))
        completed = subprocess.run([
            sys.executable, str(LIB / "source_intent_contract.py"),
            "--intent", str(intent_path), "--releases", str(releases_path),
        ], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["action"]["kind"] == "use_published_artifact"

        duplicate = root / "duplicate.json"
        duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}\n')
        completed = subprocess.run([
            sys.executable, str(LIB / "source_intent_contract.py"),
            "--intent", str(duplicate), "--releases", str(releases_path),
        ], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert completed.returncode == 2 and not completed.stdout


if __name__ == "__main__":
    main()
