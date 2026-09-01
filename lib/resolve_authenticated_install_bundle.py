#!/usr/bin/env python3
"""Resolve a verified immutable cache generation into installer inputs."""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

HEX = re.compile(r"[0-9a-f]{64}")
MAX_JSON = 1024 * 1024
MAX_PACKAGES = 64


def fail(message):
    raise SystemExit(message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("bundle metadata contains duplicate JSON keys")
        result[key] = value
    return result


def load(path):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_JSON or path.is_symlink()):
            fail("bundle metadata is not a bounded regular file")
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("bundle metadata is unreadable")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", required=True, type=Path)
    parser.add_argument("--cache-id", required=True)
    parser.add_argument("--steamos", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--architecture", default="x86_64")
    parser.add_argument("--keyring", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not HEX.fullmatch(args.cache_id):
        fail("bundle cache identity is invalid")
    generation = args.generation
    if generation.is_symlink() or not generation.is_dir():
        fail("bundle generation must be a real directory")
    manifest_path = generation / "manifest.json"
    if digest(manifest_path) != args.cache_id:
        fail("bundle generation does not match its immutable identity")
    manifest = load(manifest_path)
    policy_path, provenance_path = generation / "metadata/policy.json", generation / "metadata/provenance.json"
    policy, provenance = load(policy_path), load(provenance_path)
    if (manifest.get("schemaVersion") != 1 or manifest.get("kind") != "authenticated-artifact-set"
            or manifest.get("policy", {}).get("sha256") != digest(policy_path)
            or manifest.get("provenance", {}).get("sha256") != digest(provenance_path)):
        fail("bundle metadata binding is invalid")
    target = policy.get("target")
    if (policy.get("schemaVersion") != 1 or policy.get("status") != "reviewed"
            or target != {"steamosVersion": args.steamos, "nvidiaVersion": args.nvidia,
                          "architecture": args.architecture}):
        fail("bundle policy does not match the exact requested target")
    provenance_target = provenance.get("target")
    if (not isinstance(provenance_target, dict)
            or provenance_target.get("steamosVersion") != args.steamos
            or provenance_target.get("nvidiaVersion") != args.nvidia
            or provenance_target.get("architecture") != args.architecture):
        fail("bundle provenance does not match the exact requested target")
    keyring = policy.get("keyring")
    if (not isinstance(keyring, dict) or keyring.get("filename") != args.keyring.name
            or keyring.get("sha256") != digest(args.keyring)):
        fail("bundle policy does not bind the supplied package keyring")
    packages = policy.get("packages")
    if not isinstance(packages, list) or not 2 <= len(packages) <= MAX_PACKAGES:
        fail("bundle policy package set is invalid")
    by_filename = {item.get("name"): item for item in manifest.get("artifacts", [])
                   if isinstance(item, dict)}
    resolved = []
    names = set()
    for package in packages:
        if not isinstance(package, dict) or package.get("name") in names:
            fail("bundle policy contains duplicate or malformed package identities")
        names.add(package["name"])
        filename, signature = package.get("filename"), package.get("signatureFilename")
        record = by_filename.get(filename)
        if (not isinstance(filename, str) or not isinstance(signature, str)
                or not isinstance(record, dict) or record.get("signature") != f"payload/{signature}"
                or record.get("sha256") != package.get("packageSha256")
                or record.get("signatureSha256") != package.get("signatureSha256")):
            fail("bundle package does not match the reviewed policy")
        resolved.append({"name": package["name"], "package": str(generation / record["path"]),
                         "signature": str(generation / record["signature"])})
    if len(resolved) != len(manifest.get("artifacts", [])):
        fail("bundle contains packages not present in the reviewed policy")
    by_name = {item["name"]: item for item in resolved}
    if "nvidia-utils" not in by_name or "lib32-nvidia-utils" not in by_name:
        fail("bundle lacks required NVIDIA userspace seed packages")
    document = {"schemaVersion": 1, "status": "resolved", "sourceMode": "authenticated-bundle",
                "cacheId": args.cache_id, "generation": str(generation),
                "target": target, "policy": str(policy_path), "provenance": str(provenance_path),
                "packageKeyring": str(args.keyring), "packages": resolved}
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


if __name__ == "__main__":
    main()
