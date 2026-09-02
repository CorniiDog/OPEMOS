#!/usr/bin/env python3
"""Fail-closed contract tests for optional CUDA omission metadata."""

import copy
import json
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from gaming_payload_profiles import (
    ProfileError, REQUIRED_CAPABILITIES, reviewed_policy, target_record,
    validate_profile,
)
from resolve_target import resolve_target


def main():
    policy = reviewed_policy()
    assert policy["profileId"] == "gaming-no-cuda-v1"
    assert set(policy["preservedCapabilities"]) == REQUIRED_CAPABILITIES
    assert policy["delivery"]["strategy"] == "support-owned-repacked-packages"
    assert len(policy["supportedTargets"]) == 1
    kernel = "6.16.12-valve24.4-1-neptune-616-gfe145653a794"
    record = target_record("3.8.14", kernel, "575.64.05")
    assert record is not None
    assert target_record("3.8.14", kernel + "-closest", "575.64.05") is None
    profile = ROOT / "profiles/gaming" / record["profileAsset"]
    userspace_lock = ROOT / "locks/userspace" / record["userspaceLockAsset"]
    target = {"steamosVersion": "3.8.14", "kernelVersion": kernel,
              "nvidiaVersion": "575.64.05", "architecture": "x86_64"}
    verified = validate_profile(profile, userspace_lock, target)
    assert verified["savedBytes"] == 316170989
    assert {item["name"] for item in verified["packageRecords"]} == {
        "nvidia-utils", "lib32-nvidia-utils"
    }
    assert verified["delivery"]["sourceAuthentication"] == (
        "arch-detached-signatures-and-reviewed-userspace-lock"
    )
    with tempfile.TemporaryDirectory(prefix="gaming-profile-test-") as temporary:
        altered_lock = Path(temporary) / record["userspaceLockAsset"]
        altered = json.loads(userspace_lock.read_text())
        altered["packages"][-1]["packageSha256"] = "0" * 64
        altered_lock.write_text(json.dumps(altered), encoding="utf-8")
        try:
            validate_profile(profile, altered_lock, target)
        except ProfileError:
            pass
        else:
            raise AssertionError("altered userspace lock was accepted")
    releases = json.loads((ROOT / "tests/fixtures/releases/policy.json").read_text())
    result = resolve_target("3.8.16", "kernel-a", "x86_64", releases,
                            "CorniiDog/open-gpu-kernel-modules-steamos-support")
    capability = result["capabilities"]["optionalCudaOmission"]
    assert capability == {"schemaVersion": 1, "profileId": "gaming-no-cuda-v1",
                          "supported": False,
                          "reason": "no_reviewed_exact_target_profile"}
    tag = f"steamos-3.8.14-nvidia-575.64.05-k{kernel}"
    release = {
        "tag_name": tag, "draft": False, "prerelease": False,
        "published_at": "2026-09-01T00:00:00Z",
        "assets": [{"name": name} for name in (
            f"nvidia-open-{tag}-x86_64.tar.gz",
            f"nvidia-open-{tag}-x86_64.tar.gz.sha256",
            f"nvidia-open-{tag}-x86_64.provenance.json",
            record["profileAsset"], record["userspaceLockAsset"],
        )],
    }
    supported = resolve_target(
        "3.8.14", kernel, "x86_64", [release],
        "CorniiDog/open-gpu-kernel-modules-steamos-support",
    )["capabilities"]["optionalCudaOmission"]
    assert supported["supported"] is True
    assert supported["compatibility"] == "exact"
    assert supported["profile"]["sha256"] == record["profileSha256"]
    missing = copy.deepcopy(release)
    missing["assets"] = [
        asset for asset in missing["assets"]
        if asset["name"] != record["profileAsset"]
    ]
    unavailable = resolve_target(
        "3.8.14", kernel, "x86_64", [missing],
        "CorniiDog/open-gpu-kernel-modules-steamos-support",
    )["capabilities"]["optionalCudaOmission"]
    assert unavailable["supported"] is False
    assert unavailable["reason"] == "reviewed_profile_assets_missing"


if __name__ == "__main__":
    main()
