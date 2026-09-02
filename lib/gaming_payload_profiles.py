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
MEMBER = re.compile(r"[A-Za-z0-9._+~/-]{1,512}")
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
                    "userspaceLockAsset", "userspaceLockSha256", "savedBytes"}
        if (set(record) != required
                or record.get("architecture") != "x86_64"
                or any(not isinstance(record.get(key), str)
                       for key in required - {"savedBytes"})
                or VERSION.fullmatch(record.get("steamosVersion", "")) is None
                or VERSION.fullmatch(record.get("nvidiaVersion", "")) is None
                or KERNEL.fullmatch(record.get("kernelVersion", "")) is None
                or not SHA256.fullmatch(record.get("profileSha256", ""))
                or not SHA256.fullmatch(record.get("userspaceLockSha256", ""))
                or not isinstance(record.get("savedBytes"), int)
                or isinstance(record["savedBytes"], bool)
                or record["savedBytes"] <= 0
                or any(Path(record[key]).name != record[key]
                       for key in ("profileAsset", "userspaceLockAsset"))
                or any(re.fullmatch(r"[A-Za-z0-9._+~-]{1,255}", record[key]) is None
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
    delivery = profile.get("delivery")
    packages = profile.get("packages")
    profile_keys = {
        "schemaVersion", "status", "profileId", "target", "delivery",
        "omittedCapabilities", "preservedCapabilities", "packageOwnership",
        "provenanceRequired", "savedBytes", "packages",
    }
    if (set(profile) != profile_keys
            or profile.get("schemaVersion") != 1 or profile.get("status") != "reviewed"
            or profile.get("profileId") != PROFILE_ID
            or profile.get("target") != target
            or set(profile.get("preservedCapabilities", [])) != REQUIRED_CAPABILITIES
            or profile.get("omittedCapabilities") != ["cuda-compute"]
            or profile.get("packageOwnership") != "archive-and-pacman-database-exact"
            or profile.get("provenanceRequired") is not True
            or delivery != {
                "strategy": "deterministic-authenticated-source-repack-v1",
                "packageOwnership": "archive-and-pacman-database-exact",
                "sourceAuthentication": (
                    "arch-detached-signatures-and-reviewed-userspace-lock"
                ),
                "repacker": {
                    "name": "repack_gaming_userspace.py", "schemaVersion": 1,
                    "zstdVersion": "1.5.7", "compression": "zstd-19-t1",
                },
            }
            or not isinstance(packages, list) or len(packages) != 2):
        raise ProfileError("gaming payload profile is malformed or incomplete")
    try:
        lock = load_json(userspace_lock)
    except ProfileError as error:
        raise ProfileError("gaming payload userspace lock is malformed") from error
    lock_packages = lock.get("packages")
    if not isinstance(lock_packages, list) or len(lock_packages) > 64:
        raise ProfileError("gaming payload userspace lock package set is malformed")
    locked = {
        item.get("name"): item for item in lock_packages
        if isinstance(item, dict)
    }
    if len(locked) != len(lock_packages):
        raise ProfileError("gaming payload userspace lock identities are ambiguous")
    package_records = []
    total_saved = 0
    identities = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ProfileError("gaming payload package record is malformed")
        required_keys = {
            "name", "sourceFilename", "sourceSha256", "sourceSignatureFilename",
            "sourceSignatureSha256", "sourceSignerFingerprint", "outputFilename",
            "outputVersion", "outputSha256", "installedSize", "savedBytes",
            "requiredMembers", "omittedMembers",
        }
        name = package.get("name")
        source = locked.get(name)
        if (set(package) != required_keys or name not in {
                "nvidia-utils", "lib32-nvidia-utils"} or name in identities
                or not isinstance(source, dict)):
            raise ProfileError("gaming payload package identity is invalid")
        identities.add(name)
        source_mapping = {
            "sourceFilename": "filename",
            "sourceSha256": "packageSha256",
            "sourceSignatureFilename": "signatureFilename",
            "sourceSignatureSha256": "signatureSha256",
            "sourceSignerFingerprint": "signerFingerprint",
        }
        if any(package[key] != source.get(lock_key)
               for key, lock_key in source_mapping.items()):
            raise ProfileError("gaming payload package source differs from reviewed lock")
        if (Path(package["outputFilename"]).name != package["outputFilename"]
                or not re.fullmatch(r"[A-Za-z0-9._+~-]{1,255}", package["outputFilename"])
                or not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["outputVersion"])
                or package["outputVersion"] != source.get("version", "") + ".1"
                or package["outputFilename"] != (
                    f"{name}-{package['outputVersion']}-x86_64.pkg.tar.zst"
                )
                or SHA256.fullmatch(package["outputSha256"]) is None
                or package["outputSha256"] == "0" * 64
                or not isinstance(package["installedSize"], int)
                or isinstance(package["installedSize"], bool)
                or package["installedSize"] < 0
                or not isinstance(package["savedBytes"], int)
                or isinstance(package["savedBytes"], bool)
                or package["savedBytes"] <= 0
                or source.get("installedSize")
                != package["installedSize"] + package["savedBytes"]):
            raise ProfileError("gaming payload derived package metadata is invalid")
        required_members = package["requiredMembers"]
        omissions = package["omittedMembers"]
        if (not isinstance(required_members, list) or not required_members
                or required_members != sorted(set(required_members))
                or not isinstance(omissions, list) or not omissions
                or len(required_members) > 64 or len(omissions) > 64):
            raise ProfileError("gaming payload member policy is invalid")
        omitted_paths = []
        omitted_file_bytes = 0
        for item in omissions:
            if (not isinstance(item, dict)
                    or set(item) != {"path", "type", "size", "sha256"}
                    or item.get("type") not in ("file", "symlink")
                    or not isinstance(item.get("path"), str)
                    or not MEMBER.fullmatch(item["path"])
                    or item["path"].startswith("/") or ".." in Path(item["path"]).parts
                    or not isinstance(item.get("size"), int)
                    or isinstance(item["size"], bool) or item["size"] < 0
                    or SHA256.fullmatch(item.get("sha256", "")) is None):
                raise ProfileError("gaming payload omission metadata is invalid")
            omitted_paths.append(item["path"])
            if item["type"] == "file":
                omitted_file_bytes += item["size"]
        if (len(omitted_paths) != len(set(omitted_paths))
                or any(not isinstance(member, str) or not MEMBER.fullmatch(member)
                       or member.startswith("/") or ".." in Path(member).parts
                       or member in omitted_paths for member in required_members)
                or omitted_file_bytes != package["savedBytes"]):
            raise ProfileError("gaming payload member accounting is invalid")
        total_saved += package["savedBytes"]
        package_records.append({
            "name": name, "sourceFilename": package["sourceFilename"],
            "sourceSignatureFilename": package["sourceSignatureFilename"],
            "sourceSha256": package["sourceSha256"],
            "sourceSignatureSha256": package["sourceSignatureSha256"],
            "sourceSignerFingerprint": package["sourceSignerFingerprint"],
            "filename": package["outputFilename"],
            "version": package["outputVersion"], "sha256": package["outputSha256"],
            "installedSize": package["installedSize"],
            "savedBytes": package["savedBytes"],
        })
    if identities != {"nvidia-utils", "lib32-nvidia-utils"} or total_saved != profile.get("savedBytes"):
        raise ProfileError("gaming payload saved-byte total is invalid")
    return {"schemaVersion": 1, "status": "reviewed", "profileId": PROFILE_ID,
            "sha256": digest(path), "policySha256": digest(POLICY),
            "target": target, "delivery": delivery,
            "omittedCapabilities": ["cuda-compute"],
            "preservedCapabilities": sorted(REQUIRED_CAPABILITIES),
            "packageOwnership": "archive-and-pacman-database-exact",
            "savedBytes": total_saved,
            "packageRecords": sorted(package_records, key=lambda item: item["name"])}
