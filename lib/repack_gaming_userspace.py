#!/usr/bin/env python3
"""Deterministically derive reviewed gaming userspace packages.

The caller must authenticate the source package and its detached signature
before invoking this helper.  This helper then binds the derived package to the
reviewed profile by checking every omitted member and the exact output digest.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath


MAX_PACKAGE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_MEMBERS = 100_000
ZSTD_VERSION = "1.5.7"
PROFILE_ID = "gaming-no-cuda-v1"
SAFE_NAME = re.compile(r"[A-Za-z0-9@._+:-]{1,256}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class RepackError(ValueError):
    pass


def fail(message):
    raise RepackError(message)


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def regular(path, label, maximum=MAX_PACKAGE_BYTES):
    try:
        stat = path.stat()
    except OSError as error:
        fail(f"{label} is unavailable")
    if path.is_symlink() or not path.is_file() or stat.st_size > maximum:
        fail(f"{label} is unsafe")
    return stat.st_size


def safe_member(name):
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and ".." not in path.parts
            and "" not in path.parts and name == str(path) and len(name) <= 512)


def command_version():
    try:
        output = subprocess.run(
            ["zstd", "--version"], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise RepackError("canonical zstd is unavailable") from error
    match = re.search(r"\bv([0-9]+\.[0-9]+\.[0-9]+)\b", output)
    if match is None or match.group(1) != ZSTD_VERSION:
        fail(f"canonical repacking requires zstd {ZSTD_VERSION}")


def load_profile(path):
    regular(path, "gaming payload profile", 1024 * 1024)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RepackError("gaming payload profile is unreadable") from error
    if (not isinstance(document, dict)
            or document.get("schemaVersion") != 1
            or document.get("status") != "reviewed"
            or document.get("profileId") != PROFILE_ID):
        fail("gaming payload profile is not a reviewed schema-1 profile")
    packages = document.get("packages")
    if (not isinstance(packages, list) or len(packages) != 2
            or any(not isinstance(record, dict) for record in packages)):
        fail("gaming payload profile package records are malformed")
    if {record.get("name") for record in packages} != {
            "nvidia-utils", "lib32-nvidia-utils"}:
        fail("gaming payload profile has the wrong package identities")
    return document


def load_source_tar(package, temporary):
    regular(package, "source userspace package")
    source_tar = temporary / "source.tar"
    with source_tar.open("xb") as output:
        process = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(package)], stdout=output,
            stderr=subprocess.PIPE, timeout=300,
        )
    if process.returncode != 0:
        fail("source userspace package could not be decompressed")
    if source_tar.stat().st_size > MAX_EXPANDED_BYTES:
        fail("source userspace package exceeds the expanded-size limit")
    return source_tar


def member_payload(archive, member):
    if member.isfile():
        stream = archive.extractfile(member)
        if stream is None:
            fail("source package member is unreadable")
        return stream.read()
    if member.issym() or member.islnk():
        return member.linkname.encode("utf-8")
    return b""


def replace_metadata_line(payload, key, value):
    text = payload.decode("utf-8")
    pattern = re.compile(rf"^{re.escape(key)} = .*?$", re.MULTILINE)
    if len(pattern.findall(text)) != 1:
        fail(f"source package has ambiguous {key} metadata")
    return pattern.sub(f"{key} = {value}", text).encode("utf-8")


def update_mtree(payload, omitted, pkginfo, buildinfo, metadata_mtime):
    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise RepackError("source package mtree is unreadable") from error
    filtered = []
    seen = set()
    for line in text.splitlines():
        identity = line.split(" ", 1)[0]
        path = identity[2:] if identity.startswith("./") else None
        if path in omitted:
            seen.add(path)
            continue
        if path == ".PKGINFO":
            line = (f"./.PKGINFO time={metadata_mtime}.0 size={len(pkginfo)} "
                    f"sha256digest={digest_bytes(pkginfo)}")
        elif path == ".BUILDINFO":
            line = (f"./.BUILDINFO time={metadata_mtime}.0 size={len(buildinfo)} "
                    f"sha256digest={digest_bytes(buildinfo)}")
        filtered.append(line)
    if seen != omitted:
        fail("source package mtree does not contain every reviewed omission")
    canonical = ("\n".join(filtered) + "\n").encode("utf-8")
    # DEFLATE stored blocks make this small manifest slightly larger, but keep
    # it byte-identical across the different zlib builds on macOS and Fedora.
    output = bytearray(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff")
    chunks = [canonical[index:index + 65535]
              for index in range(0, len(canonical), 65535)] or [b""]
    for index, chunk in enumerate(chunks):
        output.append(1 if index == len(chunks) - 1 else 0)
        length = len(chunk)
        output.extend(length.to_bytes(2, "little"))
        output.extend((0xffff ^ length).to_bytes(2, "little"))
        output.extend(chunk)
    output.extend((zlib.crc32(canonical) & 0xffffffff).to_bytes(4, "little"))
    output.extend((len(canonical) & 0xffffffff).to_bytes(4, "little"))
    return bytes(output)


def copy_info(member):
    copied = tarfile.TarInfo(member.name)
    copied.mode = member.mode
    copied.uid = member.uid
    copied.gid = member.gid
    copied.size = member.size
    copied.mtime = member.mtime
    copied.type = member.type
    copied.linkname = member.linkname
    copied.uname = member.uname
    copied.gname = member.gname
    copied.devmajor = member.devmajor
    copied.devminor = member.devminor
    return copied


def validate_omissions(record, archive, members):
    omissions = record.get("omittedMembers")
    if (not isinstance(omissions, list) or not omissions or len(omissions) > 64
            or any(not isinstance(item, dict) for item in omissions)):
        fail("gaming payload omission records are malformed")
    required = record.get("requiredMembers")
    if (not isinstance(required, list) or not required or len(required) > 64
            or required != sorted(set(required))
            or any(not isinstance(name, str) or not safe_member(name)
                   for name in required)):
        fail("gaming payload preserved-member records are malformed")
    expected = {}
    saved = 0
    for item in omissions:
        if set(item) != {"path", "type", "size", "sha256"}:
            fail("gaming payload omission record has unknown fields")
        name = item.get("path")
        if (not isinstance(name, str) or not safe_member(name) or name in expected
                or item.get("type") not in ("file", "symlink")
                or not isinstance(item.get("size"), int)
                or isinstance(item["size"], bool) or item["size"] < 0
                or not SHA256.fullmatch(item.get("sha256", ""))):
            fail("gaming payload omission record is invalid")
        expected[name] = item
    for name, item in expected.items():
        member = members.get(name)
        if member is None:
            fail("a reviewed gaming payload omission is missing from its source package")
        kind = "file" if member.isfile() else "symlink" if member.issym() else None
        payload = member_payload(archive, member)
        if (kind != item["type"] or len(payload) != item["size"]
                or digest_bytes(payload) != item["sha256"]):
            fail("a reviewed gaming payload omission differs from its audited source")
        if kind == "file":
            saved += len(payload)
    if any(name not in members or name in expected for name in required):
        fail("a required gaming graphics member is absent or omitted")
    return set(expected), saved


def repack_one(package, record, output_dir, allow_unpinned=False):
    if (record.get("sourceFilename") != package.name
            or record.get("sourceSha256") != digest_file(package)
            or not SAFE_NAME.fullmatch(record.get("name", ""))
            or not SAFE_NAME.fullmatch(record.get("outputVersion", ""))
            or Path(record.get("outputFilename", "")).name
               != record.get("outputFilename")
            or not SHA256.fullmatch(record.get("outputSha256", ""))):
        fail("gaming payload source or output identity is invalid")
    output = output_dir / record["outputFilename"]
    if output.exists() or output.is_symlink():
        fail("refusing to overwrite an existing gaming payload package")
    with tempfile.TemporaryDirectory(prefix="gaming-userspace-repack-") as name:
        temporary = Path(name)
        source_tar = load_source_tar(package, temporary)
        try:
            source = tarfile.open(source_tar, "r:")
        except tarfile.TarError as error:
            raise RepackError("source userspace package tar is unreadable") from error
        with source:
            members_list = source.getmembers()
            if not 1 <= len(members_list) <= MAX_MEMBERS:
                fail("source userspace package member count is invalid")
            members = {}
            for member in members_list:
                if (not safe_member(member.name) or member.name in members
                        or member.ischr() or member.isblk() or member.isfifo()
                        or member.isdev()):
                    fail("source userspace package has unsafe members")
                members[member.name] = member
            for metadata_name in (".PKGINFO", ".BUILDINFO", ".MTREE"):
                if metadata_name not in members or not members[metadata_name].isfile():
                    fail("source userspace package lacks canonical metadata")
            omitted, saved = validate_omissions(record, source, members)
            if saved != record.get("savedBytes"):
                fail("gaming payload saved-byte accounting is invalid")
            pkginfo = member_payload(source, members[".PKGINFO"])
            buildinfo = member_payload(source, members[".BUILDINFO"])
            old_size_match = re.search(rb"^size = ([0-9]+)$", pkginfo, re.MULTILINE)
            if old_size_match is None:
                fail("source package has no declared installed size")
            installed_size = int(old_size_match.group(1)) - saved
            if installed_size != record.get("installedSize") or installed_size < 0:
                fail("gaming payload installed-size accounting is invalid")
            pkginfo = replace_metadata_line(pkginfo, "pkgver", record["outputVersion"])
            pkginfo = replace_metadata_line(pkginfo, "size", installed_size)
            buildinfo = replace_metadata_line(buildinfo, "pkgver", record["outputVersion"])
            metadata_mtime = int(members[".PKGINFO"].mtime)
            mtree = update_mtree(
                member_payload(source, members[".MTREE"]), omitted,
                pkginfo, buildinfo, metadata_mtime,
            )
            metadata = {".PKGINFO": pkginfo, ".BUILDINFO": buildinfo, ".MTREE": mtree}
            output_tar = temporary / "output.tar"
            with tarfile.open(output_tar, "x:", format=tarfile.GNU_FORMAT) as target:
                for member in members_list:
                    if member.name in omitted:
                        continue
                    copied = copy_info(member)
                    payload = metadata.get(member.name)
                    if payload is None and member.isfile():
                        stream = source.extractfile(member)
                        if stream is None:
                            fail("source package member became unreadable")
                        target.addfile(copied, stream)
                    elif payload is not None:
                        copied.size = len(payload)
                        target.addfile(copied, io.BytesIO(payload))
                    else:
                        target.addfile(copied)
            temporary_output = output_dir / f".{output.name}.partial-{os.getpid()}"
            try:
                with temporary_output.open("xb") as stream:
                    process = subprocess.run(
                        ["zstd", "-q", "-19", "-T1", "-c", str(output_tar)],
                        stdout=stream, stderr=subprocess.PIPE, timeout=600,
                    )
                if process.returncode != 0:
                    fail("derived gaming payload package compression failed")
                if (not allow_unpinned
                        and digest_file(temporary_output) != record["outputSha256"]):
                    fail("derived gaming payload package hash differs from reviewed profile")
                os.chmod(temporary_output, 0o644)
                os.replace(temporary_output, output)
            finally:
                try:
                    temporary_output.unlink()
                except FileNotFoundError:
                    pass
    return output


def materialize(profile_path, packages, output_dir, allow_unpinned=False,
                progress=None):
    command_version()
    profile = load_profile(profile_path)
    if output_dir.is_symlink() or not output_dir.is_dir():
        fail("gaming payload output directory is unsafe")
    source_by_name = {path.name: path for path in packages}
    if len(source_by_name) != len(packages):
        fail("gaming payload source package filenames are ambiguous")
    outputs = []
    records = sorted(profile["packages"], key=lambda item: item["name"])
    if progress is not None:
        progress(0, len(records))
    for index, record in enumerate(records, 1):
        source = source_by_name.get(record.get("sourceFilename"))
        if source is None:
            fail("gaming payload source package is missing")
        outputs.append(repack_one(source, record, output_dir, allow_unpinned))
        if progress is not None:
            progress(index, len(records))
    return outputs


def parse_args():
    parser = argparse.ArgumentParser(
        description="Derive exact reviewed gaming userspace packages from signed sources."
    )
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--nvidia-utils", required=True, type=Path)
    parser.add_argument("--lib32-nvidia-utils", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument(
        "--candidate", action="store_true",
        help="maintainer-only: report derived hashes before pinning them",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        outputs = materialize(
            args.profile, [args.nvidia_utils, args.lib32_nvidia_utils], args.output_dir,
            args.candidate,
        )
        result = {"schemaVersion": 1, "status": "ready", "profileId": PROFILE_ID,
                  "packages": [{"filename": path.name, "sha256": digest_file(path),
                                "size": path.stat().st_size} for path in outputs]}
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        if args.result_json:
            if args.result_json.exists() or args.result_json.is_symlink():
                fail("refusing to overwrite an existing result")
            args.result_json.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
    except RepackError as error:
        print(f"repack_gaming_userspace.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
