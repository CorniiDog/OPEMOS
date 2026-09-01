#!/usr/bin/env python3
"""Fail-closed contract tests for optional CUDA omission metadata."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from gaming_payload_profiles import REQUIRED_CAPABILITIES, reviewed_policy, target_record
from resolve_target import resolve_target


def main():
    policy = reviewed_policy()
    assert policy["profileId"] == "gaming-no-cuda-v1"
    assert set(policy["preservedCapabilities"]) == REQUIRED_CAPABILITIES
    assert policy["delivery"]["strategy"] == "support-owned-repacked-packages"
    assert policy["supportedTargets"] == []
    assert target_record("3.8.14", "kernel", "575.64.05") is None
    releases = json.loads((ROOT / "tests/fixtures/releases/policy.json").read_text())
    result = resolve_target("3.8.16", "kernel-a", "x86_64", releases,
                            "CorniiDog/open-gpu-kernel-modules-steamos-support")
    capability = result["capabilities"]["optionalCudaOmission"]
    assert capability == {"schemaVersion": 1, "profileId": "gaming-no-cuda-v1",
                          "supported": False,
                          "reason": "no_reviewed_exact_target_profile"}


if __name__ == "__main__":
    main()
