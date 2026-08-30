#!/usr/bin/env python3
"""Write the offline-target build result contract atomically."""

import argparse
import json
import os
from pathlib import Path


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
    parser.add_argument("--archive-sha256")
    return parser.parse_args()


def main():
    args = parse_args()
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
            "sha256": args.archive_sha256,
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
