#!/usr/bin/env python3
"""Atomically write the offline-root installation result contract."""

import argparse
import json
import os
from pathlib import Path


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
    return not value or (Path(value).name == value and value not in (".", ".."))


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
    if args.status == "success" and args.mounts_released != "true":
        raise SystemExit("A successful installation result requires all mounts released.")

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
        validation = json.loads(args.validation.read_text(encoding="utf-8"))
        if validation.get("status") != "verified":
            raise SystemExit("Result validation metadata must be a verified document.")
        document["validation"] = {
            "archiveSha256": validation["archiveSha256"],
            "pacmanDatabase": validation["pacmanDatabase"],
            "boot": validation["boot"],
            "keyring": validation["keyring"],
            "packages": validation["packages"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(args.output)


if __name__ == "__main__":
    main()
