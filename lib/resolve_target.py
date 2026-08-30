#!/usr/bin/env python3
"""Resolve a published NVIDIA artifact for an offline SteamOS target image."""

import argparse
import json
import re
import sys
from pathlib import Path

from select_release import select_release


SCHEMA_VERSION = 2
SUPPORTED_ARCHITECTURES = {"x86_64"}
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
KERNEL_PATTERN = re.compile(r"^[A-Za-z0-9._+~-]+$")


def result(status, target, **fields):
    document = {"schemaVersion": SCHEMA_VERSION, "status": status, "target": target}
    document.update(fields)
    return document


def resolve_target(steamos, kernel, architecture, releases, repository):
    target = {
        "steamosVersion": steamos,
        "kernelVersion": kernel,
        "architecture": architecture,
    }

    if not VERSION_PATTERN.fullmatch(steamos):
        return result(
            "invalid_target", target, reason="invalid_steamos_version",
            message="SteamOS VERSION_ID must contain three numeric components.",
        )
    if not KERNEL_PATTERN.fullmatch(kernel):
        return result(
            "invalid_target", target, reason="invalid_kernel_version",
            message="The target kernel contains unsupported characters.",
        )
    if architecture not in SUPPORTED_ARCHITECTURES:
        return result(
            "unsupported_target", target, reason="unsupported_architecture",
            message=f"No published NVIDIA artifact format is defined for {architecture}.",
        )

    selected = select_release(steamos, kernel, releases)
    if not selected:
        return result(
            "no_compatible_artifact", target, reason="no_compatible_release",
            message=("No published release matches the exact target kernel "
                     "within the permitted SteamOS compatibility range."),
        )

    published_steamos, nvidia, selected_kernel, tag = selected
    asset_name = f"nvidia-open-{tag}-{architecture}.tar.gz"
    checksum_name = f"{asset_name}.sha256"
    provenance_name = f"nvidia-open-{tag}-{architecture}.provenance.json"
    release = next(item for item in releases if item.get("tag_name") == tag)
    assets = {
        asset.get("name")
        for asset in release.get("assets", [])
        if isinstance(asset, dict) and asset.get("name")
    }
    missing = [
        name
        for name in (asset_name, checksum_name, provenance_name)
        if name not in assets
    ]
    publication = {
        "tag": tag,
        "steamosVersion": published_steamos,
        "kernelVersion": selected_kernel,
        "nvidiaVersion": nvidia,
        "publishedAt": release.get("published_at"),
    }
    if missing:
        return result(
            "no_compatible_artifact", target, reason="release_assets_missing",
            message="The selected publication is incomplete and cannot be consumed safely.",
            publication=publication, missingAssets=missing,
        )

    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    return result(
        "compatible", target,
        compatibility=("exact" if published_steamos == steamos else "same_series_fallback"),
        publication=publication,
        artifact={
            "name": asset_name,
            "url": f"{base_url}/{asset_name}",
            "checksum": {
                "algorithm": "sha256",
                "name": checksum_name,
                "url": f"{base_url}/{checksum_name}",
            },
            "provenance": {
                "name": provenance_name,
                "url": f"{base_url}/{provenance_name}",
            },
            "trust": {
                "classification": "pending-provenance-verification",
                "source": provenance_name,
                "requiredVerification": "external-and-embedded-provenance-byte-match",
            },
        },
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve a published artifact for an offline SteamOS image target."
    )
    parser.add_argument("--steamos", required=True, help="target SteamOS VERSION_ID")
    parser.add_argument("--kernel", required=True, help="exact target kernel version")
    parser.add_argument("--architecture", required=True, help="target ELF architecture")
    parser.add_argument("--releases", required=True, type=Path, help="GitHub releases JSON")
    parser.add_argument(
        "--repository", default="CorniiDog/open-gpu-kernel-modules-steamos-support",
        help="expected GitHub owner/repository",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        with args.releases.open(encoding="utf-8") as release_file:
            releases = json.load(release_file)
        if not isinstance(releases, list):
            raise ValueError("releases document must be a JSON array")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"resolve_target.py: {error}", file=sys.stderr)
        return 2

    resolved = resolve_target(
        args.steamos, args.kernel, args.architecture, releases, args.repository
    )
    print(json.dumps(resolved, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
