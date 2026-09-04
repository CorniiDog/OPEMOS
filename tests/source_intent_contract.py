#!/usr/bin/env python3
"""Source-intent authorization and fail-closed CLI tests."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from generate_source_intent_fixtures import matrix  # noqa: E402
from resolve_target import exact_target_build_action  # noqa: E402
from source_intent_contract import canonical, decision  # noqa: E402


def main():
    first = canonical(matrix())
    generated = subprocess.run(
        [sys.executable, str(LIB / "generate_source_intent_fixtures.py")],
        cwd="/", check=True, stdout=subprocess.PIPE,
    ).stdout
    assert generated == first
    fixtures = json.loads(first)
    assert 20 <= len(fixtures["cases"]) <= fixtures["maxCases"]
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
        assert actual["intentSha256"] == hashlib.sha256(canonical(case["intent"])).hexdigest()
    assert len(names) == len(set(names))

    upstream = next(case for case in fixtures["cases"] if case["name"] == "explicit-upstream-development")
    action = decision(upstream["intent"], [])["action"]
    assert action["trust"] == "development-unverified"
    assert action["publicationPermitted"] is False
    assert action["source"] == upstream["intent"]["selection"]["source"]

    escaped = {"value": "OPEMOS \u2603 caf\u00e9"}
    assert canonical(escaped) == b'{"value":"OPEMOS \\u2603 caf\\u00e9"}\n'
    assert hashlib.sha256(canonical(escaped)).hexdigest() == \
        "7c9eb8a6acdcf4b5a4164d6f25434ecd2aba0762fc8ee37ec610bc5922095ca6"

    exact = next(case for case in fixtures["cases"] if case["name"] == "exact-reviewed-build")
    exact_result = decision(exact["intent"], exact["releases"])
    assert exact_result["action"] == exact_target_build_action(
        exact["intent"]["target"]["steamosVersion"],
        exact["intent"]["target"]["kernelVersion"],
        exact["intent"]["target"]["architecture"],
    )
    assert exact_result["action"]["buildPlan"]["target"]["nvidiaVersion"] \
        == exact["intent"]["selection"]["nvidiaVersion"]

    published = next(case for case in fixtures["cases"] if case["name"] == "exact-published-match")
    published_result = decision(published["intent"], published["releases"])
    assert published_result["action"]["resolverResult"]["publication"]["tag"] \
        == published["intent"]["selection"]["releaseTag"]
    assert published_result["action"]["resolverResultSha256"] == hashlib.sha256(
        canonical(published_result["action"]["resolverResult"])
    ).hexdigest()

    rejected = [case for case in fixtures["cases"] if case["expected"]["status"] == "rejected"]
    assert rejected
    assert all("action" not in decision(case["intent"], case["releases"])
               for case in rejected)

    intent_schema = json.loads((
        ROOT / "contracts/schemas/source-intent-v1.schema.json"
    ).read_text(encoding="utf-8"))
    authorization_schema = json.loads((
        ROOT / "contracts/schemas/source-authorization-v1.schema.json"
    ).read_text(encoding="utf-8"))
    assert intent_schema["additionalProperties"] is False
    assert intent_schema["$defs"]["source"]["additionalProperties"] is False
    assert intent_schema["$defs"]["target"]["additionalProperties"] is False
    assert authorization_schema["additionalProperties"] is False

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

        noncanonical = root / "noncanonical.json"
        noncanonical.write_text(
            json.dumps(selected["intent"], indent=2) + "\n", encoding="utf-8"
        )
        completed = subprocess.run([
            sys.executable, str(LIB / "source_intent_contract.py"),
            "--intent", str(noncanonical), "--releases", str(releases_path),
        ], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert completed.returncode == 2 and not completed.stdout

        unicode_json = root / "unicode.json"
        unicode_intent = fixtures["cases"][0]["intent"]
        unicode_intent["unexpected"] = "caf\u00e9"
        unicode_json.write_text(
            json.dumps(unicode_intent, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run([
            sys.executable, str(LIB / "source_intent_contract.py"),
            "--intent", str(unicode_json), "--releases", str(releases_path),
        ], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert completed.returncode == 2 and not completed.stdout


if __name__ == "__main__":
    main()
