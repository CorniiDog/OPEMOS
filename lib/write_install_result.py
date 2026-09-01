#!/usr/bin/env python3
"""Atomically write the offline-root installation result contract."""

import argparse
import json
import os
import re
from pathlib import Path


MAX_VALIDATION_BYTES = 16 * 1024 * 1024
TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
KERNEL = re.compile(r"(?:unknown|[A-Za-z0-9._+~-]{1,255})")
VERSION = re.compile(r"(?:unknown|[0-9]+\.[0-9]+(?:\.[0-9]+)?)")
TRUST_VALUES = {
    "pending-validation",
    "development-unverified",
    "locally-built-verified",
    "certified-published",
}
PLAIN_FILENAME = re.compile(r"[A-Za-z0-9@._+~:-]{1,255}")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
PACKAGE_KEYS = {
    "name", "role", "filename", "signatureFilename", "fullVersion",
    "pkgver", "pkgrel", "architecture", "signer", "sha256",
    "signatureSha256", "installedSize", "dependencies", "provides",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--status", required=True, choices=("success", "failed", "cancelled", "validated"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--steamos", default="unknown")
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", default="unknown")
    parser.add_argument("--trust", default="pending-validation")
    parser.add_argument("--archive", default="")
    parser.add_argument("--provenance", default="")
    parser.add_argument("--nvidia-utils", default="")
    parser.add_argument("--lib32-nvidia-utils", default="")
    parser.add_argument("--mounts-released", choices=("true", "false"), default="true")
    parser.add_argument("--validation", type=Path)
    return parser.parse_args()


def plain_name(value):
    return (
        not value
        or (
            len(value) <= 255
            and PLAIN_FILENAME.fullmatch(value) is not None
            and Path(value).name == value
            and value not in (".", "..")
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )
    )


def bounded_message(value):
    return (
        0 < len(value) <= 2048
        and "\x00" not in value
        and all(character in "\n\t" or ord(character) >= 32 for character in value)
    )


def load_validation(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_VALIDATION_BYTES:
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Result validation metadata is unreadable or exceeds its size limit.")
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise SystemExit("Result validation metadata has an unsupported schema.")
    return document


def validate_verified_metadata(validation):
    required = {
        "archiveSha256", "provenanceSha256", "userspaceLock",
        "pacmanDatabase", "boot", "keyring", "packages", "storage",
        "packageDependencyClosure", "compression",
    }
    if not required <= validation.keys():
        raise SystemExit("Verified installation metadata is incomplete.")
    for field in ("archiveSha256", "provenanceSha256"):
        if not isinstance(validation[field], str) or not HEX_SHA256.fullmatch(
            validation[field]
        ):
            raise SystemExit("Verified installation metadata contains an invalid hash.")
    for identity in (validation["userspaceLock"], validation["keyring"]):
        if (not isinstance(identity, dict) or set(identity) != {"name", "sha256"}
                or not identity.get("name")
                or not plain_name(identity.get("name"))
                or not isinstance(identity.get("sha256"), str)
                or not HEX_SHA256.fullmatch(identity["sha256"])):
            raise SystemExit("Verified installation metadata has an invalid pinned input.")
    packages = validation["packages"]
    if (not isinstance(packages, list) or not 2 <= len(packages) <= 64
            or any(not isinstance(package, dict) or set(package) != PACKAGE_KEYS
                   for package in packages)):
        raise SystemExit("Verified installation package metadata is malformed.")
    for package in packages:
        if (not isinstance(package["name"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["name"]) is None
                or not isinstance(package["fullVersion"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["fullVersion"]) is None
                or not isinstance(package["pkgver"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["pkgver"]) is None
                or not isinstance(package["pkgrel"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["pkgrel"]) is None
                or not package["filename"]
                or not plain_name(package["filename"])
                or not package["signatureFilename"]
                or not plain_name(package["signatureFilename"])
                or package["architecture"] not in ("x86_64", "any")
                or package["role"] not in ("nvidia-userspace", "dependency")
                or not isinstance(package["installedSize"], int)
                or isinstance(package["installedSize"], bool)
                or not 0 <= package["installedSize"] <= 16 * 1024**3
                or not isinstance(package["sha256"], str)
                or not HEX_SHA256.fullmatch(package["sha256"])
                or not isinstance(package["signatureSha256"], str)
                or not HEX_SHA256.fullmatch(package["signatureSha256"])
                or not isinstance(package["signer"], str)
                or re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", package["signer"]) is None):
            raise SystemExit("Verified installation package metadata is invalid.")
        for field in ("dependencies", "provides"):
            relations = package[field]
            if (not isinstance(relations, list) or len(relations) > 64
                    or any(not isinstance(value, str) or not 0 < len(value) <= 256
                           for value in relations)):
                raise SystemExit("Verified installation package relations are invalid.")


def main():
    args = parse_args()
    artifact_names = (
        args.archive,
        args.provenance,
        args.nvidia_utils,
        args.lib32_nvidia_utils,
    )
    if not all(plain_name(value) for value in artifact_names):
        raise SystemExit("Installation results may contain filenames, never host paths.")
    if not TOKEN.fullmatch(args.reason) or not TOKEN.fullmatch(args.phase):
        raise SystemExit("Installation result reason and phase must be stable tokens.")
    if not bounded_message(args.message):
        raise SystemExit("Installation result message is empty, excessive, or contains control data.")
    if args.root != "/target-root":
        raise SystemExit("Installation results must use the logical /target-root identity.")
    if args.status == "success" or args.status == "validated":
        if not KERNEL.fullmatch(args.kernel):
            raise SystemExit("Installation result kernel identity is invalid.")
        if not VERSION.fullmatch(args.steamos) or not VERSION.fullmatch(args.nvidia):
            raise SystemExit("Installation result version identity is invalid.")
    else:
        args.kernel = args.kernel if KERNEL.fullmatch(args.kernel) else "invalid"
        args.steamos = args.steamos if VERSION.fullmatch(args.steamos) else "unknown"
        args.nvidia = args.nvidia if VERSION.fullmatch(args.nvidia) else "unknown"
    if args.trust not in TRUST_VALUES:
        raise SystemExit("Installation result trust classification is invalid.")
    if args.status == "success" and args.mounts_released != "true":
        raise SystemExit("A successful installation result requires all mounts released.")
    expected_terminal = {
        "success": ("install_complete", "complete"),
        "validated": ("validation_complete", "validated"),
        "cancelled": ("cancelled", None),
    }
    if args.status in expected_terminal:
        reason, phase = expected_terminal[args.status]
        if args.reason != reason or (phase is not None and args.phase != phase):
            raise SystemExit("Installation result terminal status is internally inconsistent.")

    document = {
        "schemaVersion": 1,
        "status": args.status,
        "reason": args.reason,
        "message": args.message,
        "phase": args.phase,
        "trust": args.trust,
        "target": {
            "root": args.root,
            "steamosVersion": args.steamos,
            "kernelVersion": args.kernel,
            "nvidiaVersion": args.nvidia,
            "architecture": "x86_64",
        },
        "inputs": {
            "archive": args.archive or None,
            "provenance": args.provenance or None,
            "nvidiaUtils": args.nvidia_utils or None,
            "lib32NvidiaUtils": args.lib32_nvidia_utils or None,
        },
        "cleanup": {"mountsReleased": args.mounts_released == "true"},
    }
    if args.validation:
        validation = load_validation(args.validation)
        if validation.get("status") == "verified":
            validate_verified_metadata(validation)
            document["validation"] = {
                "archiveSha256": validation["archiveSha256"],
                "provenanceSha256": validation["provenanceSha256"],
                "userspaceLock": validation["userspaceLock"],
                "pacmanDatabase": validation["pacmanDatabase"],
                "boot": validation["boot"],
                "keyring": validation["keyring"],
                "packages": validation["packages"],
                "storage": validation["storage"],
                "packageDependencyClosure": validation["packageDependencyClosure"],
                "compression": validation["compression"],
            }
        elif args.status == "failed" and validation.get("status") == "failed":
            failure_validation = {
                key: validation[key]
                for key in (
                    "storage", "packageDependencyClosure", "compression",
                    "missingDependencies", "dependencyRequestedBy",
                    "packageName", "signerFingerprint",
                    "missingPackages", "unexpectedPackages",
                    "duplicatePackages", "packageMismatches",
                    "packageRecord", "invalidFields",
                )
                if key in validation
            }
            if failure_validation:
                document["validation"] = failure_validation
        else:
            raise SystemExit("Result validation metadata does not match result status.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(args.output)


if __name__ == "__main__":
    main()
