#!/usr/bin/env python3
"""Validate a canonical release set and emit its publication plan."""

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


CANONICAL_REPOSITORY = "CorniiDog/open-gpu-kernel-modules-steamos-support"
TRUST_LEVELS = {"locally-built-verified", "certified-published"}


def fail(message):
    raise SystemExit(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def require_commit(value, label):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value or ""):
        fail(f"{label} must be a complete Git commit.")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    paths = (args.archive, args.checksum, args.build_info, args.provenance)
    if any(not path.is_file() or path.is_symlink() for path in paths):
        fail("All four publication inputs must be regular files.")

    try:
        provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("Provenance is not valid JSON.")
    if provenance.get("schemaVersion") != 1:
        fail("Only schema-1 provenance can be published.")
    target = provenance.get("target", {})
    artifact = provenance.get("artifact", {})
    support = provenance.get("support", {})
    source = provenance.get("source", {})
    steamos = target.get("steamosVersion", "")
    kernel = target.get("kernelVersion", "")
    nvidia = target.get("nvidiaVersion", "")
    architecture = target.get("architecture", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", steamos):
        fail("Provenance has an invalid SteamOS version.")
    if not kernel or not re.fullmatch(r"[A-Za-z0-9._+-]+", kernel):
        fail("Provenance has an invalid target kernel.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", nvidia):
        fail("Provenance has an invalid NVIDIA version.")
    if architecture != "x86_64":
        fail("Only x86_64 artifacts can be published.")
    if provenance.get("trust") not in TRUST_LEVELS:
        fail("Artifact trust is not publishable.")
    require_commit(support.get("commit"), "Support commit")
    require_commit(source.get("commit"), "Source commit")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")):
        fail("Provenance has an invalid source repository.")
    if str(support.get("dirty")).lower() not in ("0", "false"):
        fail("Support provenance is dirty.")
    if str(source.get("dirty")).lower() not in ("0", "false"):
        fail("Source provenance is dirty.")
    if support.get("repository") != args.repository:
        fail("Provenance support repository does not match the publication repository.")

    safe_kernel = kernel.translate(str.maketrans({"/": "-", " ": "-", ":": "-", "+": "-"}))
    tag = f"steamos-{steamos}-nvidia-{nvidia}-k{safe_kernel}"
    archive_name = f"nvidia-open-{tag}-x86_64.tar.gz"
    stem = archive_name[:-7]
    expected_names = (
        archive_name,
        archive_name + ".sha256",
        stem + ".build-info.txt",
        stem + ".provenance.json",
    )
    if tuple(path.name for path in paths) != expected_names:
        fail("Publication input basenames are not the canonical matching set.")
    if artifact.get("releaseTag") != tag or artifact.get("archive") != archive_name:
        fail("Provenance release identity is not canonical.")

    checksum_fields = args.checksum.read_text(encoding="utf-8").split()
    if len(checksum_fields) != 2 or checksum_fields[1].lstrip("*") != archive_name:
        fail("Checksum sidecar does not name the canonical archive.")
    actual_sha256 = sha256(args.archive)
    if checksum_fields[0].lower() != actual_sha256:
        fail("Archive checksum verification failed.")

    info = metadata(args.build_info)
    comparisons = {
        "steamos_version": steamos,
        "kernel_version": kernel,
        "nvidia_version": nvidia,
        "build_architecture": architecture,
        "trust_classification": provenance["trust"],
        "release_tag": tag,
        "release_asset": archive_name,
        "support_repository": args.repository,
        "support_commit": support["commit"],
        "source_repository": source.get("repository", ""),
        "source_commit": source["commit"],
    }
    for key, expected in comparisons.items():
        if info.get(key) != str(expected):
            fail(f"Build information does not match provenance: {key}.")

    try:
        with tarfile.open(args.archive, "r:gz") as archive_file:
            embedded_provenance = archive_file.extractfile("PROVENANCE.json")
            embedded_info = archive_file.extractfile("BUILD-INFO.txt")
            if embedded_provenance is None or embedded_info is None:
                fail("Archive lacks embedded publication metadata.")
            if embedded_provenance.read() != args.provenance.read_bytes():
                fail("Embedded and external provenance differ.")
            if embedded_info.read() != args.build_info.read_bytes():
                fail("Embedded and external build information differ.")
    except (tarfile.TarError, KeyError, OSError):
        fail("Archive publication metadata is unreadable.")

    title = f"NVIDIA {nvidia} for SteamOS {steamos} ({kernel})"
    notes = "\n".join((
        f"Open NVIDIA kernel modules for SteamOS {steamos}.",
        "",
        f"- Target kernel: `{kernel}`",
        f"- Architecture: `{architecture}`",
        f"- Trust: `{provenance['trust']}`",
        f"- NVIDIA source commit: [{source['commit'][:7]}](https://github.com/{source['repository']}/commit/{source['commit']})",
        f"- Support commit: [{support['commit'][:7]}](https://github.com/{args.repository}/commit/{support['commit']})",
        f"- Archive SHA256: `{actual_sha256}`",
    ))
    print(json.dumps({
        "schemaVersion": 1,
        "status": "ready",
        "repository": args.repository,
        "tag": tag,
        "title": title,
        "notes": notes,
        "targetCommit": support["commit"],
        "trust": provenance["trust"],
        "archiveSha256": actual_sha256,
        "assets": [str(path.resolve()) for path in paths],
    }, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
