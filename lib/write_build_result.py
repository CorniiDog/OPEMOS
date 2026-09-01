#!/usr/bin/env python3
"""Write the offline-target build result contract atomically."""

import argparse
import json
import os
import re
from pathlib import Path


TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
STEAMOS_VERSION = re.compile(r"(?:unknown|[0-9]+\.[0-9]+\.[0-9]+)")
NVIDIA_VERSION = re.compile(r"(?:unknown|[0-9]+\.[0-9]+(?:\.[0-9]+)?)")
KERNEL_VERSION = re.compile(r"(?:unknown|[A-Za-z0-9._+~-]{1,255})")
SAFE_IDENTITY = re.compile(r"[A-Za-z0-9._+~-]{1,255}")
PLAIN_FILENAME = re.compile(r"[A-Za-z0-9@._+~:-]{1,255}")
TRUST_VALUES = {
    "development-unverified",
    "locally-built-verified",
    "certified-published",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--status", required=True, choices=("success", "failed", "cancelled"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--trust", default="development-unverified")
    parser.add_argument("--steamos", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--archive")
    parser.add_argument("--checksum")
    parser.add_argument("--build-info")
    parser.add_argument("--provenance")
    parser.add_argument("--archive-sha256")
    return parser.parse_args()


def main():
    args = parse_args()
    if not TOKEN.fullmatch(args.reason):
        raise SystemExit("Build result reason must be a stable token.")
    if (not args.message or len(args.message) > 2048 or "\x00" in args.message
            or any(character not in "\n\t" and ord(character) < 32 for character in args.message)):
        raise SystemExit("Build result message is empty, excessive, or contains control data.")
    identity_patterns = (STEAMOS_VERSION, KERNEL_VERSION, NVIDIA_VERSION)
    if args.status != "success":
        args.steamos = args.steamos if SAFE_IDENTITY.fullmatch(args.steamos) else "invalid"
        args.kernel = args.kernel if SAFE_IDENTITY.fullmatch(args.kernel) else "invalid"
        args.nvidia = args.nvidia if SAFE_IDENTITY.fullmatch(args.nvidia) else "invalid"
        args.architecture = (
            args.architecture if SAFE_IDENTITY.fullmatch(args.architecture) else "invalid"
        )
        identity_patterns = (SAFE_IDENTITY, SAFE_IDENTITY, SAFE_IDENTITY)
    if any(
        pattern.fullmatch(value) is None
        for pattern, value in zip(
            identity_patterns, (args.steamos, args.kernel, args.nvidia)
        )
    ):
        raise SystemExit("Build result target identity is invalid.")
    if args.status == "success" and args.architecture != "x86_64":
        raise SystemExit("Build result architecture is invalid.")
    if args.trust not in TRUST_VALUES:
        raise SystemExit("Build result trust classification is invalid.")
    if args.status == "success" and args.reason != "build_complete":
        raise SystemExit("Successful build results require reason=build_complete.")
    if args.status == "cancelled" and args.reason != "cancelled":
        raise SystemExit("Cancelled build results require reason=cancelled.")
    if args.status == "success":
        artifact_names = (
            args.archive,
            args.checksum,
            args.build_info,
            args.provenance,
        )
        if any(
            not value
            or PLAIN_FILENAME.fullmatch(value) is None
            or Path(value).name != value
            or value in (".", "..")
            for value in artifact_names
        ):
            raise SystemExit(
                "Successful results require plain archive, checksum, build-info, "
                "and provenance filenames."
            )
        if not args.archive_sha256 or not re.fullmatch(
            r"[0-9a-fA-F]{64}", args.archive_sha256
        ):
            raise SystemExit("Successful results require a complete archive SHA256.")
    document = {
        "schemaVersion": 1,
        "status": args.status,
        "reason": args.reason,
        "message": args.message,
        "trust": args.trust,
        "target": {
            "steamosVersion": args.steamos,
            "kernelVersion": args.kernel,
            "nvidiaVersion": args.nvidia,
            "architecture": args.architecture,
        },
    }
    if args.status == "success":
        document["artifact"] = {
            "archive": args.archive,
            "checksum": args.checksum,
            "buildInfo": args.build_info,
            "provenance": args.provenance,
            "sha256": args.archive_sha256.lower(),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
