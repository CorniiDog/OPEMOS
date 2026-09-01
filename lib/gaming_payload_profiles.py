#!/usr/bin/env python3
"""Validate and resolve support-owned reviewed gaming payload profiles."""

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "profiles/gaming/reviewed-policy-v1.json"
PROFILE_ID = "gaming-no-cuda-v1"
REQUIRED_CAPABILITIES = {
    "graphics", "vulkan", "glvnd-egl", "nvenc", "nvdec", "gsp-firmware",
    "gaming-32bit", "recovery-rendering",
}
MAX_PROFILE_BYTES = 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){2}")
KERNEL = re.compile(r"[A-Za-z0-9._+~-]{1,255}")
MAX_TARGETS = 256


class ProfileError(ValueError):
    pass


def digest(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PROFILE_BYTES:
            raise OSError
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ProfileError("gaming payload metadata is unsafe") from error


def load_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PROFILE_BYTES:
        raise ProfileError("gaming payload metadata is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileError("gaming payload metadata is unreadable") from error
    if not isinstance(value, dict):
        raise ProfileError("gaming payload metadata is malformed")
    return value


def reviewed_policy():
    policy = load_json(POLICY)
    if (set(policy) != {"schemaVersion", "profileId", "status", "delivery",
                        "omittedCapabilities", "preservedCapabilities",
                        "provenanceRequired", "supportedTargets"}
            or policy.get("schemaVersion") != 1
            or policy.get("profileId") != PROFILE_ID
            or policy.get("status") != "reviewed"
            or policy.get("omittedCapabilities") != ["cuda-compute"]
            or set(policy.get("preservedCapabilities", [])) != REQUIRED_CAPABILITIES
            or policy.get("provenanceRequired") is not True
            or policy.get("delivery") != {
                "strategy": "support-owned-repacked-packages",
                "packageOwnership": "archive-and-pacman-database-exact",
            }
            or not isinstance(policy.get("supportedTargets"), list)
            or len(policy["supportedTargets"]) > MAX_TARGETS):
        raise ProfileError("reviewed gaming payload policy is invalid")
    return policy


def target_record(steamos, kernel, nvidia, architecture="x86_64"):
    matches = []
    identities = set()
    for record in reviewed_policy()["supportedTargets"]:
        if not isinstance(record, dict):
            raise ProfileError("reviewed gaming target record is malformed")
        required = {"steamosVersion", "kernelVersion", "nvidiaVersion",
                    "architecture", "profileAsset", "profileSha256",
                    "userspaceLockAsset", "userspaceLockSha256"}
        if (set(record) != required
                or record.get("architecture") != "x86_64"
                or any(not isinstance(record.get(key), str) for key in required)
                or VERSION.fullmatch(record.get("steamosVersion", "")) is None
                or VERSION.fullmatch(record.get("nvidiaVersion", "")) is None
                or KERNEL.fullmatch(record.get("kernelVersion", "")) is None
                or not SHA256.fullmatch(record.get("profileSha256", ""))
                or not SHA256.fullmatch(record.get("userspaceLockSha256", ""))
                or any(Path(record[key]).name != record[key]
                       for key in ("profileAsset", "userspaceLockAsset"))):
            raise ProfileError("reviewed gaming target record is invalid")
        identity = (record["steamosVersion"], record["kernelVersion"],
                    record["nvidiaVersion"], record["architecture"])
        if identity in identities:
            raise ProfileError("reviewed gaming target identity is ambiguous")
        identities.add(identity)
        if identity == (
                    steamos, kernel, nvidia, architecture):
            matches.append(record)
    if len(matches) > 1:
        raise ProfileError("reviewed gaming target identity is ambiguous")
    return matches[0] if matches else None


def validate_profile(path, userspace_lock, target):
    record = target_record(target["steamosVersion"], target["kernelVersion"],
                           target["nvidiaVersion"], target["architecture"])
    if record is None:
        raise ProfileError("gaming payload is not reviewed for the exact target")
    if path.name != record["profileAsset"] or digest(path) != record["profileSha256"]:
        raise ProfileError("gaming payload profile does not match reviewed metadata")
    if (userspace_lock.name != record["userspaceLockAsset"]
            or digest(userspace_lock) != record["userspaceLockSha256"]):
        raise ProfileError("gaming payload userspace lock does not match reviewed metadata")
    profile = load_json(path)
    if (profile.get("schemaVersion") != 1 or profile.get("status") != "reviewed"
            or profile.get("profileId") != PROFILE_ID
            or profile.get("target") != target
            or profile.get("policySha256") != digest(POLICY)
            or set(profile.get("preservedCapabilities", [])) != REQUIRED_CAPABILITIES
            or profile.get("omittedCapabilities") != ["cuda-compute"]
            or profile.get("packageOwnership") != "archive-and-pacman-database-exact"
            or profile.get("provenanceRequired") is not True):
        raise ProfileError("gaming payload profile is malformed or incomplete")
    return {"schemaVersion": 1, "status": "reviewed", "profileId": PROFILE_ID,
            "sha256": digest(path), "policySha256": digest(POLICY),
            "omittedCapabilities": ["cuda-compute"],
            "preservedCapabilities": sorted(REQUIRED_CAPABILITIES),
            "packageOwnership": "archive-and-pacman-database-exact"}
