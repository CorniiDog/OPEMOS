#!/usr/bin/env python3
"""Validate a canonical release set and emit its publication plan."""

import argparse
import hashlib
import json
import re
import tarfile
import subprocess
from pathlib import Path, PurePosixPath


CANONICAL_REPOSITORY = "CorniiDog/open-gpu-kernel-modules-steamos-support"
TRUST_LEVELS = {"locally-built-verified", "certified-published"}
EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko",
    "nvidia-uvm.ko",
}
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MODULE_BYTES = 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 2 * 1024 * 1024 * 1024


def fail(message):
    raise SystemExit(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stream_sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def normalized_member(name):
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        fail("Archive contains an unsafe member path.")
    return str(path)


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
    if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
        fail("Archive exceeds the compressed-size limit.")
    if args.build_info.stat().st_size > MAX_METADATA_BYTES or args.provenance.stat().st_size > MAX_METADATA_BYTES:
        fail("External publication metadata exceeds the size limit.")

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
    base_tag = f"steamos-{steamos}-nvidia-{nvidia}-k{safe_kernel}"
    representation = artifact.get("representation", "ko")
    revision = artifact.get("revision")
    if representation == "ko":
        if revision is not None or "repack" in provenance:
            fail("Raw-module provenance contains unexpected repack metadata.")
        tag = base_tag
    elif representation == "ko.zst":
        if not isinstance(revision, int) or isinstance(revision, bool) or not 1 <= revision <= 999:
            fail("Repacked provenance has an invalid revision.")
        repack = provenance.get("repack")
        if (not isinstance(repack, dict) or repack.get("schemaVersion") != 1
                or repack.get("sourceReleaseTag") != base_tag
                or repack.get("payloadIdentity") != "byte-identical"
                or repack.get("encoding") != "zstd-19-t1"
                or repack.get("encoder") != {"name": "zstd", "version": "1.5.7"}
                or not re.fullmatch(r"[0-9a-f]{64}", repack.get("sourceArchiveSha256", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", repack.get("sourceProvenanceSha256", ""))):
            fail("Repacked provenance is incomplete.")
        tag = f"{base_tag}-modules-zstd-r{revision}"
    else:
        fail("Artifact module representation is unsupported.")
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

    module_records = provenance.get("modules")
    if not isinstance(module_records, list) or len(module_records) != len(EXPECTED_MODULES):
        fail("Provenance does not contain exactly five NVIDIA modules.")
    modules = {}
    for module in module_records:
        if not isinstance(module, dict) or module.get("name") in modules:
            fail("Provenance contains a duplicate or malformed module record.")
        name = module.get("name", "")
        if (name not in EXPECTED_MODULES
                or not re.fullmatch(r"[0-9a-fA-F]{64}", module.get("sha256", ""))
                or module.get("version") != nvidia
                or module.get("architecture") != architecture
                or module.get("vermagic", "").split(maxsplit=1)[0] != kernel):
            fail("Provenance contains invalid NVIDIA module metadata.")
        if representation == "ko.zst" and (
                module.get("representation") != "ko.zst"
                or module.get("representationFilename") != name + ".zst"
                or not re.fullmatch(r"[0-9a-f]{64}", module.get("payloadSha256", ""))):
            fail("Repacked provenance contains invalid representation metadata.")
        modules[name] = module
    if set(modules) != EXPECTED_MODULES:
        fail("Provenance NVIDIA module set is incomplete.")

    try:
        with tarfile.open(args.archive, "r:gz") as archive_file:
            members = {}
            for member in archive_file.getmembers():
                name = normalized_member(member.name)
                canonical_spelling = {name, f"{name}/"} if member.isdir() else {name}
                if member.name not in canonical_spelling:
                    fail("Archive contains a noncanonical member path.")
                if name in members:
                    fail("Archive contains duplicate member paths.")
                members[name] = member
            suffix = ".zst" if representation == "ko.zst" else ""
            required_files = {"BUILD-INFO.txt", "PROVENANCE.json"} | {
                f"modules/{name}{suffix}" for name in EXPECTED_MODULES
            }
            allowed_members = required_files | {"modules"}
            if set(members) != allowed_members:
                fail("Archive contains noncanonical entries.")
            if any(name not in members or not members[name].isfile() for name in required_files):
                fail("Archive lacks a regular canonical publication file.")
            if not members["modules"].isdir():
                fail("Archive lacks its canonical modules directory.")
            if sum(member.size for member in members.values()) > MAX_TOTAL_MEMBER_BYTES:
                fail("Archive exceeds the decompressed-size limit.")
            for name, member in members.items():
                if not member.isfile():
                    continue
                limit = MAX_METADATA_BYTES if name in ("BUILD-INFO.txt", "PROVENANCE.json") else MAX_MODULE_BYTES
                if member.size > limit:
                    fail(f"Archive member exceeds the size limit: {name}.")
            archived_modules = {
                name.removeprefix("modules/").removesuffix(".zst")
                for name, member in members.items()
                if name.startswith("modules/") and member.isfile()
            }
            if archived_modules != EXPECTED_MODULES:
                fail("Archive does not contain exactly the canonical five-module set.")
            embedded_provenance = archive_file.extractfile(members["PROVENANCE.json"])
            embedded_info = archive_file.extractfile(members["BUILD-INFO.txt"])
            if embedded_provenance is None or embedded_info is None:
                fail("Archive lacks embedded publication metadata.")
            if embedded_provenance.read() != args.provenance.read_bytes():
                fail("Embedded and external provenance differ.")
            if embedded_info.read() != args.build_info.read_bytes():
                fail("Embedded and external build information differ.")
            for name, record in modules.items():
                member_name = f"modules/{name}{suffix}"
                module_stream = archive_file.extractfile(members[member_name])
                if module_stream is None:
                    fail(f"Archived module is unreadable: {name}.")
                representation_bytes = module_stream.read()
                if hashlib.sha256(representation_bytes).hexdigest() != record["sha256"].lower():
                    fail(f"Archived module does not match provenance: {name}.")
                if representation == "ko.zst":
                    try:
                        decoded = subprocess.run(
                            ["zstd", "-q", "-d", "-c"], input=representation_bytes,
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        ).stdout
                    except (OSError, subprocess.CalledProcessError):
                        fail(f"Archived module representation is unreadable: {name}.")
                    if hashlib.sha256(decoded).hexdigest() != record["payloadSha256"]:
                        fail(f"Archived module payload identity changed: {name}.")
    except (tarfile.TarError, KeyError, OSError):
        fail("Archive publication metadata is unreadable.")

    title = f"NVIDIA {nvidia} for SteamOS {steamos} ({kernel})"
    if representation == "ko.zst":
        title += f" — compressed modules revision {revision}"
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
