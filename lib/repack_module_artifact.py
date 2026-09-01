#!/usr/bin/env python3
"""Verify a raw-module release and deterministically repack it as .ko.zst."""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


MODULES = {"nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
           "nvidia-peermem.ko", "nvidia-uvm.ko"}
MAX_ARCHIVE = 1024**3
MAX_MEMBER = 1024**3
MAX_TOTAL = 2 * 1024**3
ZSTD_VERSION = "1.5.7"


def fail(message):
    raise SystemExit(f"repack_module_artifact.py: {message}")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_regular(path, maximum):
    return path.is_file() and not path.is_symlink() and path.stat().st_size <= maximum


def command_output(arguments):
    try:
        return subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        fail("module metadata verification failed")


def deterministic_tar(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo("modules")
        directory.type = tarfile.DIRTYPE
        directory.mode, directory.uid, directory.gid, directory.mtime = 0o755, 0, 0, 0
        archive.addfile(directory)
        for name in sorted(files):
            data, mode = files[name]
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), mode
            item.uid = item.gid = item.mtime = 0
            archive.addfile(item, io.BytesIO(data))
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0, compresslevel=9) as stream:
        stream.write(raw.getvalue())
    return output.getvalue()


def zstd_bytes(raw, temporary):
    source, output = temporary / "module.ko", temporary / "module.ko.zst"
    source.write_bytes(raw)
    try:
        subprocess.run(["zstd", "-q", "-19", "-T1", "-f", str(source), "-o", str(output)],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError):
        fail("deterministic zstd compression failed")
    return output.read_bytes()


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--support-commit", required=True)
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = arguments()
    inputs = (args.archive, args.checksum, args.build_info, args.provenance)
    if any(not safe_regular(path, MAX_ARCHIVE if path == args.archive else 1024**2)
           for path in inputs):
        fail("an input is missing, unsafe, or oversized")
    if not re.fullmatch(r"[0-9a-f]{40}", args.support_commit) or not 1 <= args.revision <= 999:
        fail("support commit or revision is invalid")
    if f"v{ZSTD_VERSION}" not in command_output(["zstd", "--version"]):
        fail(f"canonical repacking requires zstd {ZSTD_VERSION}")
    checksum = args.checksum.read_text(encoding="utf-8").split()
    original_sha = sha256_file(args.archive)
    if len(checksum) != 2 or checksum[1].lstrip("*") != args.archive.name or checksum[0].lower() != original_sha:
        fail("original checksum verification failed")
    provenance_bytes = args.provenance.read_bytes()
    try:
        provenance = json.loads(provenance_bytes)
    except (UnicodeError, json.JSONDecodeError):
        fail("original provenance is invalid")
    if provenance.get("schemaVersion") != 1 or provenance.get("artifact", {}).get("archive") != args.archive.name:
        fail("original provenance identity is invalid")
    if (provenance.get("trust") not in {"locally-built-verified", "certified-published"}
            or not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("support", {}).get("commit", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("source", {}).get("commit", "")))
            or str(provenance.get("support", {}).get("dirty", 1)).lower() not in {"0", "false"}
            or str(provenance.get("source", {}).get("dirty", 1)).lower() not in {"0", "false"}):
        fail("original trust or source identity is invalid")
    target = provenance.get("target", {})
    kernel, nvidia, architecture = (target.get("kernelVersion"),
                                    target.get("nvidiaVersion"), target.get("architecture"))
    if architecture != "x86_64" or not kernel or not nvidia:
        fail("original target identity is invalid")
    expected = {item.get("name"): item for item in provenance.get("modules", [])
                if isinstance(item, dict)}
    if set(expected) != MODULES or len(provenance.get("modules", [])) != 5:
        fail("original provenance module set is invalid")
    raw_modules = {}
    try:
        with tarfile.open(args.archive, "r:gz") as archive:
            member_list = archive.getmembers()
            members = {}
            for item in member_list:
                normalized = str(PurePosixPath(item.name))
                canonical = {normalized, normalized + "/"} if item.isdir() else {normalized}
                if (item.name not in canonical or PurePosixPath(item.name).is_absolute()
                        or ".." in PurePosixPath(item.name).parts or normalized in members):
                    fail("original archive has an unsafe, duplicate, or noncanonical member")
                members[normalized] = item
            allowed = {"modules", "BUILD-INFO.txt", "PROVENANCE.json"} | {
                f"modules/{name}" for name in MODULES}
            if (len(member_list) != len(allowed) or set(members) != allowed
                    or not members["modules"].isdir()
                    or any(not item.isfile() for name, item in members.items()
                           if name != "modules")):
                fail("original archive is not the canonical raw-module layout")
            if sum(item.size for item in members.values()) > MAX_TOTAL:
                fail("original archive expands beyond its limit")
            embedded_p = archive.extractfile(members["PROVENANCE.json"])
            embedded_b = archive.extractfile(members["BUILD-INFO.txt"])
            if (embedded_p is None or embedded_p.read() != provenance_bytes
                    or embedded_b is None or embedded_b.read() != args.build_info.read_bytes()):
                fail("original embedded metadata differs from sidecars")
            for name in sorted(MODULES):
                member = members[f"modules/{name}"]
                if not member.isfile() or member.size > MAX_MEMBER:
                    fail("original module member is invalid")
                stream = archive.extractfile(member)
                if stream is None:
                    fail("original module member is unreadable")
                raw = stream.read(MAX_MEMBER + 1)
                record = expected[name]
                if (len(raw) > MAX_MEMBER or sha256_bytes(raw) != str(record.get("sha256", "")).lower()
                        or record.get("version") != nvidia
                        or record.get("architecture") != architecture
                        or str(record.get("vermagic", "")).split(maxsplit=1)[0] != kernel):
                    fail("original module payload or metadata differs from provenance")
                raw_modules[name] = raw
    except (OSError, tarfile.TarError):
        fail("original archive is unreadable")

    base_tag = provenance["artifact"].get("releaseTag", "")
    if not base_tag or base_tag.endswith(tuple(f"-modules-zstd-r{x}" for x in range(1, 1000))):
        fail("original release is not a canonical raw-module release")
    tag = f"{base_tag}-modules-zstd-r{args.revision}"
    archive_name = f"nvidia-open-{tag}-x86_64.tar.gz"
    stem = archive_name[:-7]
    with tempfile.TemporaryDirectory(prefix="nvidia-module-repack-") as temporary_name:
        temporary = Path(temporary_name)
        representations = {}
        records = []
        for name in sorted(raw_modules):
            raw_path = temporary / name
            raw_path.write_bytes(raw_modules[name])
            if command_output(["modinfo", "-F", "version", str(raw_path)]) != nvidia:
                fail("module version verification failed")
            if command_output(["modinfo", "-F", "vermagic", str(raw_path)]).split(maxsplit=1)[0] != kernel:
                fail("module vermagic verification failed")
            if "Advanced Micro Devices X86-64" not in command_output(["readelf", "-h", str(raw_path)]):
                fail("module architecture verification failed")
            encoded = zstd_bytes(raw_modules[name], temporary)
            encoded_name = f"{name}.zst"
            representations[encoded_name] = encoded
            record = dict(expected[name])
            record.update({"name": name, "sha256": sha256_bytes(encoded),
                           "payloadSha256": sha256_bytes(raw_modules[name]),
                           "representation": "ko.zst", "representationFilename": encoded_name})
            records.append(record)
        new_provenance = dict(provenance)
        new_provenance["support"] = dict(provenance.get("support", {}), commit=args.support_commit, dirty=0)
        new_provenance["artifact"] = {"releaseTag": tag, "archive": archive_name,
                                      "representation": "ko.zst", "revision": args.revision}
        new_provenance["modules"] = records
        new_provenance["repack"] = {"schemaVersion": 1, "sourceReleaseTag": base_tag,
                                    "sourceArchive": args.archive.name,
                                    "sourceArchiveSha256": original_sha,
                                    "sourceProvenanceSha256": sha256_bytes(provenance_bytes),
                                    "payloadIdentity": "byte-identical", "encoding": "zstd-19-t1",
                                    "encoder": {"name": "zstd", "version": ZSTD_VERSION}}
        new_provenance_bytes = (json.dumps(new_provenance, sort_keys=True,
                                           separators=(",", ":")) + "\n").encode()
        info = args.build_info.read_text(encoding="utf-8")
        replacements = {"release_tag": tag, "release_asset": archive_name,
                        "support_commit": args.support_commit}
        lines = []
        found = set()
        for line in info.splitlines():
            key = line.split("=", 1)[0] if "=" in line else ""
            if key in replacements:
                line, found = f"{key}={replacements[key]}", found | {key}
            lines.append(line)
        if found != set(replacements):
            fail("build information lacks canonical identity fields")
        lines.extend(("module_representation=ko.zst", f"repack_revision={args.revision}",
                      f"repack_source_archive_sha256={original_sha}"))
        new_info = ("\n".join(lines) + "\n").encode()
        archive_bytes = deterministic_tar({
            "BUILD-INFO.txt": (new_info, 0o644), "PROVENANCE.json": (new_provenance_bytes, 0o644),
            **{f"modules/{name}": (data, 0o644) for name, data in representations.items()},
        })
        plan = {"schemaVersion": 1, "status": "ready", "operation": "module-repack",
                "createOnly": True, "source": {"tag": base_tag, "archiveSha256": original_sha},
                "output": {"tag": tag, "archive": archive_name,
                           "archiveSha256": sha256_bytes(archive_bytes),
                           "buildInfo": f"{stem}.build-info.txt",
                           "provenance": f"{stem}.provenance.json"},
                "modulePayloadsByteIdentical": True, "representation": "ko.zst"}
        if not args.dry_run:
            outputs = {archive_name: archive_bytes,
                       archive_name + ".sha256": f"{plan['output']['archiveSha256']}  {archive_name}\n".encode(),
                       f"{stem}.build-info.txt": new_info,
                       f"{stem}.provenance.json": new_provenance_bytes}
            if args.output_dir.exists() and (args.output_dir.is_symlink() or not args.output_dir.is_dir()):
                fail("output directory is unsafe")
            args.output_dir.mkdir(parents=True, exist_ok=True)
            if any((args.output_dir / name).exists() for name in outputs):
                fail("refusing to overwrite an existing repack output")
            staged_paths = []
            for name, data in outputs.items():
                destination = args.output_dir / name
                staged = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
                staged.write_bytes(data)
                staged_paths.append((staged, destination))
            for staged, destination in staged_paths:
                staged.replace(destination)
        print(json.dumps(plan, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
