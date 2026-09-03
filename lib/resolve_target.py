#!/usr/bin/env python3
"""Resolve a published NVIDIA artifact for an offline SteamOS target image."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from select_release import select_release
from gaming_payload_profiles import ProfileError, target_record


SCHEMA_VERSION = 2
MAX_RELEASES_BYTES = 32 * 1024 * 1024
MAX_RELEASES = 2_000
MAX_RELEASE_ASSETS = 2_000
SUPPORTED_ARCHITECTURES = {"x86_64"}
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
NVIDIA_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
KERNEL_PATTERN = re.compile(r"^[A-Za-z0-9._+~-]+$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_PLAN_POLICY = (
    Path(__file__).resolve().parent.parent / "policies/exact-target-builds-v1.json"
)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def strict_json(payload):
    return json.loads(
        payload,
        object_pairs_hook=unique_object,
        parse_constant=reject_json_constant,
    )


def read_bounded_regular(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum):
            raise ValueError("input is unsafe or exceeds its size limit")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
        if (len(payload) > maximum or after.st_size != len(payload)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or (after.st_dev, after.st_ino) != (path_after.st_dev, path_after.st_ino)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("input changed while it was being read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def result(status, target, **fields):
    document = {"schemaVersion": SCHEMA_VERSION, "status": status, "target": target}
    document.update(fields)
    return document


def load_exact_target_build_plan(steamos, kernel, architecture):
    """Return one reviewed, hash-addressed build plan for an exact target."""
    try:
        payload = read_bounded_regular(BUILD_PLAN_POLICY, 1024 * 1024)
        policy = strict_json(payload)
    except (OSError, ValueError, json.JSONDecodeError):
        raise ProfileError("exact-target build policy is unreadable") from None
    if not isinstance(policy, dict):
        raise ProfileError("exact-target build policy is malformed")
    plans = policy.get("plans")
    if (set(policy) != {"schemaVersion", "plans"} or policy["schemaVersion"] != 1
            or not isinstance(plans, list) or len(plans) > 128):
        raise ProfileError("exact-target build policy is malformed")
    matches = []
    seen = set()
    for plan in plans:
        if not isinstance(plan, dict) or set(plan) != {"target", "source", "baseline"}:
            raise ProfileError("exact-target build policy is malformed")
        target = plan["target"]
        source = plan["source"]
        baseline = plan["baseline"]
        identity = tuple(target.get(field) for field in (
            "steamosVersion", "kernelVersion", "architecture"
        )) if isinstance(target, dict) else ()
        if (not isinstance(target, dict) or set(target) != {
                "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture"}
                or not VERSION_PATTERN.fullmatch(target.get("steamosVersion", ""))
                or not NVIDIA_VERSION_PATTERN.fullmatch(target.get("nvidiaVersion", ""))
                or not KERNEL_PATTERN.fullmatch(target.get("kernelVersion", ""))
                or target.get("architecture") not in SUPPORTED_ARCHITECTURES
                or identity in seen
                or not isinstance(source, dict) or set(source) != {
                    "repository", "ref", "commit"}
                or source.get("repository")
                != "CorniiDog/open-gpu-kernel-modules-steamos"
                or source.get("ref") != f"refs/heads/nvidia/{target.get('nvidiaVersion')}"
                or not COMMIT_PATTERN.fullmatch(source.get("commit", ""))
                or not isinstance(baseline, dict) or set(baseline) != {
                    "releaseTag", "archiveSha256", "provenanceSha256", "trust"}
                or not isinstance(baseline.get("releaseTag"), str)
                or len(baseline["releaseTag"]) > 1024
                or f"-nvidia-{target.get('nvidiaVersion')}-"
                not in baseline["releaseTag"]
                or not SHA256_PATTERN.fullmatch(baseline.get("archiveSha256", ""))
                or not SHA256_PATTERN.fullmatch(baseline.get("provenanceSha256", ""))
                or baseline.get("trust") not in {
                    "locally-built-verified", "certified-published"}):
            raise ProfileError("exact-target build policy is malformed")
        seen.add(identity)
        if identity == (steamos, kernel, architecture):
            matches.append(plan)
    if not matches:
        return None
    if len(matches) != 1:
        raise ProfileError("exact-target build policy is ambiguous")
    return matches[0], hashlib.sha256(payload).hexdigest()


def exact_target_build_action(steamos, kernel, architecture):
    """Describe the sole reviewed fallback when no published release matches."""
    selected = load_exact_target_build_plan(steamos, kernel, architecture)
    if selected is None:
        return None
    plan, policy_sha256 = selected
    return {
        "schemaVersion": 1,
        "kind": "build_exact_target",
        "entrypoint": "bootstrap/build_for_target.sh",
        "executionArchitecture": "x86_64",
        "kernelPolicy": "exact",
        "buildPlan": {
            "schemaVersion": 1,
            "policy": {
                "name": BUILD_PLAN_POLICY.name,
                "sha256": policy_sha256,
            },
            "target": plan["target"],
            "source": plan["source"],
            "baseline": plan["baseline"],
        },
    }


def resolve_target(steamos, kernel, architecture, releases, repository):
    target = {
        "steamosVersion": steamos,
        "kernelVersion": kernel,
        "architecture": architecture,
    }

    if not VERSION_PATTERN.fullmatch(steamos):
        return result(
            "invalid_target", target, reason="invalid_steamos_version",
            message="SteamOS VERSION_ID must contain three numeric components.",
        )
    if not KERNEL_PATTERN.fullmatch(kernel):
        return result(
            "invalid_target", target, reason="invalid_kernel_version",
            message="The target kernel contains unsupported characters.",
        )
    if architecture not in SUPPORTED_ARCHITECTURES:
        return result(
            "unsupported_target", target, reason="unsupported_architecture",
            message=f"No published NVIDIA artifact format is defined for {architecture}.",
        )
    if not REPOSITORY_PATTERN.fullmatch(repository):
        return result(
            "invalid_target", target, reason="invalid_repository",
            message="The artifact repository identity is invalid.",
        )
    if (not isinstance(releases, list) or len(releases) > MAX_RELEASES
            or any(not isinstance(release, dict) for release in releases)):
        return result(
            "resolver_error", target, reason="release_metadata_invalid",
            message="The publication metadata is malformed or exceeds its size limit.",
        )
    if any(
        not isinstance(release.get("tag_name", ""), str)
        or not isinstance(release.get("draft", False), bool)
        or not isinstance(release.get("prerelease", False), bool)
        or (
            release.get("published_at") is not None
            and not isinstance(release.get("published_at"), str)
        )
        for release in releases
    ):
        return result(
            "resolver_error", target, reason="release_metadata_invalid",
            message="The publication metadata contains invalid field types.",
        )

    selected = select_release(steamos, kernel, releases)
    if not selected:
        try:
            action = exact_target_build_action(steamos, kernel, architecture)
        except ProfileError:
            return result(
                "resolver_error", target, reason="build_plan_policy_invalid",
                message="The support-owned exact-target build policy is invalid.",
            )
        if action is None:
            return result(
                "no_compatible_artifact", target,
                reason="no_reviewed_exact_target_build_plan",
                message=("No published release or reviewed exact-target build plan "
                         "matches this target."),
            )
        return result(
            "no_compatible_artifact", target, reason="no_compatible_release",
            message=("No published release matches the exact target kernel "
                     "within the permitted SteamOS compatibility range."),
            nextAction=action,
        )

    published_steamos, nvidia, selected_kernel, tag = selected
    asset_name = f"nvidia-open-{tag}-{architecture}.tar.gz"
    checksum_name = f"{asset_name}.sha256"
    provenance_name = f"nvidia-open-{tag}-{architecture}.provenance.json"
    matching_releases = [item for item in releases if item.get("tag_name") == tag]
    if len(matching_releases) != 1:
        return result(
            "resolver_error", target, reason="release_metadata_ambiguous",
            message="The selected publication identity is duplicated.",
        )
    release = matching_releases[0]
    release_assets = release.get("assets")
    if (not isinstance(release_assets, list)
            or len(release_assets) > MAX_RELEASE_ASSETS
            or any(not isinstance(asset, dict) for asset in release_assets)):
        return result(
            "resolver_error", target, reason="release_metadata_invalid",
            message="The selected publication has malformed asset metadata.",
        )
    asset_names = [asset.get("name") for asset in release_assets]
    if any(not isinstance(name, str) or len(name) > 255 for name in asset_names):
        return result(
            "resolver_error", target, reason="release_metadata_invalid",
            message="The selected publication has invalid asset identities.",
        )
    missing = [
        name
        for name in (asset_name, checksum_name, provenance_name)
        if asset_names.count(name) == 0
    ]
    publication = {
        "tag": tag,
        "steamosVersion": published_steamos,
        "kernelVersion": selected_kernel,
        "nvidiaVersion": nvidia,
        "publishedAt": release.get("published_at"),
    }
    if missing:
        return result(
            "no_compatible_artifact", target, reason="release_assets_missing",
            message="The selected publication is incomplete and cannot be consumed safely.",
            publication=publication, missingAssets=missing,
        )
    ambiguous = [
        name
        for name in (asset_name, checksum_name, provenance_name)
        if asset_names.count(name) != 1
    ]
    if ambiguous:
        return result(
            "no_compatible_artifact", target, reason="release_assets_ambiguous",
            message="The selected publication contains duplicate canonical assets.",
            publication=publication, ambiguousAssets=ambiguous,
        )

    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    gaming = {
        "schemaVersion": 1,
        "profileId": "gaming-no-cuda-v1",
        "supported": False,
        "reason": "no_reviewed_exact_target_profile",
    }
    try:
        gaming_record = target_record(
            steamos, kernel, nvidia, architecture
        ) if published_steamos == steamos else None
    except ProfileError:
        return result(
            "resolver_error", target, reason="gaming_profile_policy_invalid",
            message="The support-owned gaming payload policy is invalid.",
        )
    if gaming_record is not None:
        required_gaming_assets = (
            gaming_record["profileAsset"], gaming_record["userspaceLockAsset"]
        )
        gaming_missing = [name for name in required_gaming_assets if asset_names.count(name) != 1]
        if not gaming_missing:
            gaming = {
                "schemaVersion": 1, "profileId": "gaming-no-cuda-v1",
                "supported": True, "compatibility": "exact",
                "requiredVerification": "support-policy-hash-and-exact-package-lock",
                "delivery": "deterministic-authenticated-source-repack-v1",
                "savedBytes": gaming_record["savedBytes"],
                "omittedCapabilities": ["cuda-compute"],
                "preservedCapabilities": [
                    "gaming-32bit", "glvnd-egl", "graphics", "gsp-firmware",
                    "nvdec", "nvenc", "recovery-rendering", "vulkan",
                ],
                "profile": {"name": gaming_record["profileAsset"],
                            "sha256": gaming_record["profileSha256"],
                            "url": f"{base_url}/{gaming_record['profileAsset']}"},
                "userspaceLock": {"name": gaming_record["userspaceLockAsset"],
                                  "sha256": gaming_record["userspaceLockSha256"],
                                  "url": f"{base_url}/{gaming_record['userspaceLockAsset']}"},
            }
        else:
            gaming["reason"] = "reviewed_profile_assets_missing"
    return result(
        "compatible", target,
        compatibility=("exact" if published_steamos == steamos else "same_series_fallback"),
        publication=publication,
        artifact={
            "name": asset_name,
            "url": f"{base_url}/{asset_name}",
            "checksum": {
                "algorithm": "sha256",
                "name": checksum_name,
                "url": f"{base_url}/{checksum_name}",
            },
            "provenance": {
                "name": provenance_name,
                "url": f"{base_url}/{provenance_name}",
            },
            "trust": {
                "classification": "pending-provenance-verification",
                "source": provenance_name,
                "requiredVerification": "external-and-embedded-provenance-byte-match",
            },
        },
        capabilities={"optionalCudaOmission": gaming},
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve a published artifact for an offline SteamOS image target."
    )
    parser.add_argument("--steamos", required=True, help="target SteamOS VERSION_ID")
    parser.add_argument("--kernel", required=True, help="exact target kernel version")
    parser.add_argument("--architecture", required=True, help="target ELF architecture")
    parser.add_argument("--releases", required=True, type=Path, help="GitHub releases JSON")
    parser.add_argument(
        "--repository", default="CorniiDog/OPEMOS",
        help="expected GitHub owner/repository",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        releases = strict_json(read_bounded_regular(args.releases, MAX_RELEASES_BYTES))
        if not isinstance(releases, list):
            raise ValueError("releases document must be a JSON array")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"resolve_target.py: {error}", file=sys.stderr)
        return 2

    resolved = resolve_target(
        args.steamos, args.kernel, args.architecture, releases, args.repository
    )
    print(json.dumps(resolved, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
