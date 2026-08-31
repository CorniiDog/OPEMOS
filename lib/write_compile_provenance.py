#!/usr/bin/env python3
"""Create schema-1 provenance for the native SteamOS compile workflow."""

import argparse
import json
import os
import re
from pathlib import Path


def metadata(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def parse_modules(path, nvidia):
    modules = []
    pattern = re.compile(r"^\s{2}([0-9a-f]{64})\s+(\S+)\s+vermagic=(\S+)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            digest, name, vermagic = match.groups()
            modules.append({"name": name, "sha256": digest, "version": nvidia,
                            "architecture": "x86_64", "vermagic": vermagic})
    if len(modules) != 5:
        raise SystemExit("Build information does not describe exactly five modules.")
    return sorted(modules, key=lambda item: item["name"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    info = metadata(args.build_info)
    required = ("steamos_version", "kernel_version", "nvidia_version", "release_tag",
                "release_asset", "source_repository", "source_commit",
                "support_repository", "support_commit", "trust_classification")
    missing = [key for key in required if not info.get(key)]
    if missing:
        raise SystemExit("Build information lacks: " + ", ".join(missing))
    document = {
        "schemaVersion": 1,
        "trust": info["trust_classification"],
        "target": {"steamosVersion": info["steamos_version"],
                   "kernelVersion": info["kernel_version"],
                   "nvidiaVersion": info["nvidia_version"], "architecture": "x86_64"},
        "artifact": {"releaseTag": info["release_tag"], "archive": info["release_asset"]},
        "build": {"mode": "native-steamos-container", "completedAt": info.get("built_at", "unknown")},
        "support": {"repository": info["support_repository"],
                    "commit": info["support_commit"], "dirty": int(info.get("support_dirty", "1"))},
        "source": {"repository": info["source_repository"], "branch": info.get("source_branch", ""),
                   "commit": info["source_commit"], "dirty": int(info.get("source_dirty", "1"))},
        "modules": parse_modules(args.build_info, info["nvidia_version"]),
    }
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    staged.replace(args.output)


if __name__ == "__main__":
    main()
