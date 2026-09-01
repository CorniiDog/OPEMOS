#!/usr/bin/env python3
"""Create-only promotion of an immutable candidate userspace lock."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from atomic_output import atomic_create_bytes  # noqa: E402
DEFAULT_POLICY = ROOT / "trust/nvidia-userspace-package-signers.json"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_KEYRING_BYTES = 64 * 1024 * 1024


def bounded_bytes(path, description, limit):
    try:
        if not path.is_file() or path.is_symlink():
            raise OSError
        size = path.stat().st_size
        if size > limit:
            raise SystemExit(f"{description} exceeds the size limit")
        return path.read_bytes()
    except OSError:
        raise SystemExit(f"{description} is not a readable regular file")


def json_document(path, description):
    payload = bounded_bytes(path, description, MAX_JSON_BYTES)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(f"{description} is not valid bounded JSON")
    if not isinstance(document, dict):
        raise SystemExit(f"{description} must be a JSON object")
    return payload, document


def sha256(path):
    return hashlib.sha256(bounded_bytes(path, "input file", MAX_KEYRING_BYTES)).hexdigest()


def fingerprint_groups(keyring):
    bounded_bytes(keyring, "minimal keyring", MAX_KEYRING_BYTES)
    try:
        completed = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", str(keyring)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, UnicodeError):
        raise SystemExit("minimal keyring cannot be inspected with gpg")
    groups = []
    current = None
    for line in completed.stdout.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            current = set()
            groups.append(current)
        elif fields[0] == "fpr" and len(fields) > 9 and current is not None:
            current.add(fields[9].upper())
    if not groups or any(not group for group in groups):
        raise SystemExit("minimal keyring has malformed primary-key metadata")
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--minimal-keyring", required=True, type=Path)
    parser.add_argument("--reviewed-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("reviewed lock output already exists")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args.reviewed_at):
        raise SystemExit("reviewed-at must be YYYY-MM-DD")
    candidate_bytes, candidate = json_document(args.candidate, "candidate lock")
    if candidate.get("schemaVersion") != 1 or candidate.get("status") != "candidate":
        raise SystemExit("input is not a schema-1 candidate lock")
    _, policy = json_document(args.reviewed_policy, "reviewed signer policy")
    if policy.get("schemaVersion") != 1 or not isinstance(policy.get("signers"), list):
        raise SystemExit("reviewed signer policy schema is invalid")
    try:
        approved = {
            (package, signer["fingerprint"].upper())
            for signer in policy["signers"] if signer["status"] == "active"
            for package in signer["packages"]
            if isinstance(package, str)
        }
    except (KeyError, TypeError, AttributeError):
        raise SystemExit("reviewed signer policy schema is invalid")
    packages = candidate.get("packages")
    if not isinstance(packages, list) or not packages:
        raise SystemExit("candidate lock has no package records")
    normalized_packages = []
    for package in packages:
        if not isinstance(package, dict):
            raise SystemExit("candidate lock has a malformed package record")
        name = package.get("name")
        fingerprint = package.get("signerFingerprint")
        if not isinstance(name, str) or not name or not isinstance(fingerprint, str):
            raise SystemExit("candidate lock has a malformed package record")
        if not re.fullmatch(r"[0-9A-Fa-f]{40}", fingerprint):
            raise SystemExit("candidate lock has an invalid signer fingerprint")
        normalized_packages.append((name, fingerprint.upper()))
    if len({name for name, _ in normalized_packages}) != len(normalized_packages):
        raise SystemExit("candidate lock has duplicate package identities")
    missing = sorted(
        ({"packageName": name, "signerFingerprint": fingerprint}
         for name, fingerprint in normalized_packages
         if (name, fingerprint) not in approved),
        key=lambda item: (item["packageName"], item["signerFingerprint"]),
    )
    if missing:
        raise SystemExit("candidate still has unreviewed package/signer mappings")
    required_fingerprints = {fingerprint for _, fingerprint in normalized_packages}
    key_groups = fingerprint_groups(args.minimal_keyring)
    present = set().union(*key_groups)
    absent = required_fingerprints - present
    if absent:
        raise SystemExit("minimal keyring lacks a reviewed package signer")
    if any(group.isdisjoint(required_fingerprints) for group in key_groups):
        raise SystemExit("minimal keyring contains an unrelated primary key")
    reviewed = dict(candidate)
    reviewed["status"] = "reviewed"
    reviewed["missingReview"] = []
    reviewed["keyring"] = {
        "filename": args.minimal_keyring.name,
        "sha256": sha256(args.minimal_keyring),
        "provenance": {
            "candidateSha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "policySha256": sha256(args.reviewed_policy),
            "reviewedAt": args.reviewed_at,
        },
    }
    payload = (json.dumps(reviewed, indent=2, sort_keys=True) + "\n").encode()
    try:
        atomic_create_bytes(args.output, payload)
    except FileExistsError:
        raise SystemExit("reviewed lock output already exists")


if __name__ == "__main__":
    main()
