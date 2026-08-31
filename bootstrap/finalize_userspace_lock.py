#!/usr/bin/env python3
"""Create-only promotion of an immutable candidate userspace lock."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "trust/nvidia-userspace-package-signers.json"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints(keyring):
    completed = subprocess.run(
        ["gpg", "--batch", "--show-keys", "--with-colons", str(keyring)],
        check=True, stdout=subprocess.PIPE, text=True,
    )
    return {
        fields[9].upper() for line in completed.stdout.splitlines()
        if (fields := line.split(":"))[0] == "fpr" and len(fields) > 9
    }


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
    candidate_bytes = args.candidate.read_bytes()
    candidate = json.loads(candidate_bytes)
    if candidate.get("schemaVersion") != 1 or candidate.get("status") != "candidate":
        raise SystemExit("input is not a schema-1 candidate lock")
    policy = json.loads(args.reviewed_policy.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != 1:
        raise SystemExit("reviewed signer policy schema is invalid")
    approved = {
        (package, signer["fingerprint"])
        for signer in policy["signers"] if signer["status"] == "active"
        for package in signer["packages"]
    }
    missing = sorted(
        ({"packageName": package["name"],
          "signerFingerprint": package["signerFingerprint"]}
         for package in candidate.get("packages", [])
         if (package.get("name"), package.get("signerFingerprint")) not in approved),
        key=lambda item: (item["packageName"], item["signerFingerprint"]),
    )
    if missing:
        raise SystemExit("candidate still has unreviewed package/signer mappings")
    required_fingerprints = {package["signerFingerprint"] for package in candidate["packages"]}
    absent = required_fingerprints - fingerprints(args.minimal_keyring)
    if absent:
        raise SystemExit("minimal keyring lacks a reviewed package signer")
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(payload)
        os.link(temporary, args.output)
    except FileExistsError:
        raise SystemExit("reviewed lock output already exists")
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
