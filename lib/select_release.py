#!/usr/bin/env python3
"""Select the certified project release for a SteamOS/kernel pair."""

import json
import re
import sys


TAG_PATTERN = re.compile(
    r"^steamos-"
    r"([0-9]+(?:\.[0-9]+){2})"
    r"-nvidia-"
    r"([0-9]+(?:\.[0-9]+){1,2})"
    r"-k(.+)$"
)


def steamos_version(version):
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def nvidia_version(version):
    return tuple(int(part) for part in version.split("."))


def select_release(target, target_kernel, releases):
    target_version = steamos_version(target)
    candidates = []

    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue

        tag = release.get("tag_name", "")
        match = TAG_PATTERN.match(tag)
        if not match:
            continue

        steam, nvidia, kernel = match.groups()
        steam_version = steamos_version(steam)

        # Certified fallback never crosses a SteamOS major/minor boundary,
        # moves forward, or selects modules for a different running kernel.
        if steam_version[:2] != target_version[:2]:
            continue
        if steam_version > target_version:
            continue
        if kernel != target_kernel:
            continue

        candidates.append(
            (
                steam_version,
                nvidia_version(nvidia),
                release.get("published_at", ""),
                steam,
                nvidia,
                kernel,
                tag,
            )
        )

    if not candidates:
        return None

    candidates.sort(reverse=True)
    _, _, _, steam, nvidia, kernel, tag = candidates[0]
    return steam, nvidia, kernel, tag


def main():
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: select_release.py STEAMOS_VERSION KERNEL RELEASES_JSON"
        )

    target, target_kernel, path = sys.argv[1:]
    with open(path, encoding="utf-8") as release_file:
        releases = json.load(release_file)

    selected = select_release(target, target_kernel, releases)
    if selected:
        print("\t".join(selected))


if __name__ == "__main__":
    main()
