#!/usr/bin/env python3
"""Compatibility tests for immutable generation request planning."""

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from userspace_lock_generation_contract import canonical  # noqa: E402
from userspace_lock_request_plan import (  # noqa: E402
    MAX_PLAN_BYTES,
    MAX_REQUEST_METADATA_BYTES,
    MAX_REQUESTS,
    MAX_URL_BYTES,
    RequestPlanError,
    build_request_plan,
    parse_request_plan,
)


GENERATOR = LIB / "generate_userspace_lock_request_plan_fixtures.py"
SCHEMA = ROOT / "contracts/schemas/userspace-lock-generation-request-plan-v1.schema.json"


def generate():
    completed = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd="/", check=True,
        stdout=subprocess.PIPE,
    )
    return completed.stdout


def arguments(inputs):
    return (
        canonical(inputs["policy"]),
        canonical(inputs["discovery"]),
        inputs["discoverySignature"].encode(),
        canonical(inputs["manifest"]),
        inputs["manifestSignature"].encode(),
        inputs["authentication"],
        {name: payload.encode() for name, payload in inputs["payloads"].items()},
    )


def accepted(callback, *args):
    try:
        callback(*args)
    except RequestPlanError:
        return False
    return True


def raw_plan(case):
    if "rawPlan" in case:
        return case["rawPlan"].encode()
    recipe = case.get("rawPlanRecipe")
    if recipe is not None:
        assert set(recipe) == {"text", "count"}
        assert recipe["text"] == " "
        assert recipe["count"] == MAX_PLAN_BYTES + 1
        return recipe["text"].encode() * recipe["count"]
    return canonical(case["plan"])


def main():
    first = generate()
    assert first == generate()
    matrix = json.loads(first)
    assert canonical(matrix) == first
    assert set(matrix) == {"schemaVersion", "kind", "limits", "cases"}
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == "opemos-userspace-lock-request-plan-compatibility"
    assert matrix["limits"] == {
        "maxRequests": MAX_REQUESTS,
        "maxUrlBytes": MAX_URL_BYTES,
        "maxRequestMetadataBytes": MAX_REQUEST_METADATA_BYTES,
        "maxPlanBytes": MAX_PLAN_BYTES,
    }
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["request"]["additionalProperties"] is False
    assert schema["properties"]["redirects"] == {"const": False}
    assert schema["properties"]["requestCount"]["maximum"] == MAX_REQUESTS
    assert schema["properties"]["aggregateMetadataBytes"]["maximum"] == (
        MAX_REQUEST_METADATA_BYTES
    )

    cases = matrix["cases"]
    assert 25 <= len(cases) <= 64
    names = []
    for case in cases:
        assert set(case) <= {
            "name", "expected", "inputs", "plan", "rawPlan", "rawPlanRecipe"
        }
        names.append(case["name"])
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["name"])
        assert set(case["expected"]) == {"inputsAccepted", "planAccepted"}
        assert all(type(value) is bool for value in case["expected"].values())
        args = arguments(case["inputs"])
        input_ok = accepted(build_request_plan, *args)
        assert input_ok is case["expected"]["inputsAccepted"], case["name"]
        if "plan" in case or "rawPlan" in case or "rawPlanRecipe" in case:
            plan_ok = accepted(parse_request_plan, raw_plan(case), *args)
            assert plan_ok is case["expected"]["planAccepted"], case["name"]
    assert len(names) == len(set(names))

    valid = next(case for case in cases if case["name"] == "valid-canonical-plan")
    plan = valid["plan"]
    inputs = valid["inputs"]
    channel = inputs["policy"]["channel"]
    release_root = (
        channel["origin"] + channel["immutableReleasePathPrefix"]
        + plan["releaseTag"] + "/"
    )
    assert plan["redirects"] is False
    assert plan["keyringSha256"] == inputs["policy"]["authority"][
        "keyringSha256"
    ]
    assert plan["primarySigningFingerprint"] == inputs["policy"][
        "authority"
    ]["primarySigningFingerprint"]
    assert plan["discoveryHashAlgorithmId"] == 10
    assert plan["manifestHashAlgorithmId"] == 8
    assert plan["requestCount"] == len(plan["requests"])
    assert [record["assetRole"] for record in plan["requests"][:4]] == [
        "discovery", "discovery-signature", "generation-manifest",
        "generation-manifest-signature",
    ]
    assert plan["requests"][0]["url"] == (
        channel["origin"] + channel["discoveryPath"]
    )
    assert all(
        record["url"].startswith(release_root)
        for record in plan["requests"][2:]
    )
    assert all(
        "?" not in record["url"] and "#" not in record["url"]
        and "%" not in record["url"] and ".." not in record["url"]
        for record in plan["requests"]
    )
    assert all(
        record["url"] == plan["origin"] + record["path"]
        for record in plan["requests"]
    )
    assert plan["aggregateExpectedBytes"] == sum(
        record["expectedSize"] for record in plan["requests"]
    )
    assert plan["aggregateMetadataBytes"] == sum(
        len(record["filename"].encode("ascii"))
        + len(record["path"].encode("ascii"))
        + len(record["url"].encode("ascii")) for record in plan["requests"]
    )


if __name__ == "__main__":
    main()
