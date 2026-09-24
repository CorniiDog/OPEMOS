#!/usr/bin/env python3
"""Stage and validate one exact materialized driver product for recovery."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path


MAX_DOCUMENT = 64 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,254}")
ROLES = ("archive", "checksum", "provenance", "buildInfo")


def fail(message):
    raise SystemExit(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            fail("materialization result contains a duplicate key")
        result[key] = value
    return result


def read_document(path):
    payload = read_regular(path, MAX_DOCUMENT, "materialization result")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                              parse_constant=lambda _value: fail("invalid JSON number"))
    except (UnicodeError, json.JSONDecodeError):
        fail("materialization result is not canonical JSON")
    if payload != canonical(document):
        fail("materialization result is not canonical JSON")
    return document


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_gid, info.st_nlink,
            stat.S_IMODE(info.st_mode))


def read_regular(path, maximum, label):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail(f"{label} is missing or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= maximum):
            fail(f"{label} is missing or unsafe")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (len(payload) != before.st_size or len(payload) > maximum
                or file_identity(before) != file_identity(after)
                or file_identity(after) != file_identity(current)):
            fail(f"{label} changed while it was read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def validate(document, expected):
    if not isinstance(document, dict) or set(document) != {
            "schemaVersion", "status", "target", "core", "source", "release",
            "representation", "outputs"}:
        fail("materialization result fields are invalid")
    if document["schemaVersion"] != 1 or document["status"] != "materialized":
        fail("materialization result identity is unsupported")
    if document["target"] != {
            "steamosVersion": expected["steamos"],
            "kernelVersion": expected["kernel"],
            "nvidiaVersion": expected["nvidia"],
            "architecture": "x86_64"}:
        fail("cached driver product does not match the exact recovery target")
    core = document["core"]
    if core != {"repository": "CorniiDog/OPEMOS", "commit": expected["revision"]}:
        fail("cached driver product does not match the installed Core revision")
    source = document["source"]
    if (not isinstance(source, dict) or set(source) != {"repository", "commit"}
            or not isinstance(source["repository"], str)
            or COMMIT.fullmatch(source["commit"] or "") is None):
        fail("cached driver product source identity is invalid")
    release = document["release"]
    if (not isinstance(release, dict) or set(release) != {"repository", "tag"}
            or release["repository"] != "CorniiDog/OPEMOS"
            or not isinstance(release["tag"], str) or not release["tag"]):
        fail("cached driver product release identity is invalid")
    if document["representation"] != {
            "productMember": "payload/nvidia-driver.tar.zst",
            "installerContainer": "tar+gzip", "modules": "ko.zst",
            "conversion": "none-byte-identical"}:
        fail("cached driver product representation is unsupported")
    outputs = document["outputs"]
    if not isinstance(outputs, dict) or set(outputs) != set(ROLES):
        fail("cached driver product output inventory is invalid")
    names = set()
    for role in ROLES:
        record = outputs[role]
        if (not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}
                or SAFE_NAME.fullmatch(record.get("name", "")) is None
                or not isinstance(record.get("bytes"), int) or record["bytes"] <= 0
                or HEX.fullmatch(record.get("sha256", "")) is None
                or record["name"] in names):
            fail("cached driver product output inventory is invalid")
        names.add(record["name"])
    archive = outputs["archive"]["name"]
    if (not archive.endswith(".tar.gz")
            or outputs["checksum"]["name"] != archive + ".sha256"):
        fail("cached driver product archive names are invalid")
    return outputs


def validate_files(directory, outputs):
    directory = Path(directory)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        fail("cached driver product directory is unsafe")
    allowed = {record["name"] for record in outputs.values()} | {"materialization.json"}
    if {entry.name for entry in directory.iterdir()} != allowed:
        fail("cached driver product directory contains an unexpected entry")
    for name in allowed:
        info = (directory / name).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            fail("cached driver product file ownership or mode is unsafe")
    payloads = {}
    for role, record in outputs.items():
        maximum = MAX_ARCHIVE if role == "archive" else MAX_METADATA
        payload = read_regular(directory / record["name"], maximum, f"cached {role}")
        if len(payload) != record["bytes"] or digest(payload) != record["sha256"]:
            fail(f"cached {role} does not match the materialization result")
        payloads[role] = payload
    checksum = payloads["checksum"].decode("ascii", errors="strict")
    expected = f'{outputs["archive"]["sha256"]}  {outputs["archive"]["name"]}\n'
    if checksum != expected:
        fail("cached checksum is not canonical")


def load(directory, expected):
    directory = Path(os.path.abspath(os.fspath(directory)))
    if directory.is_symlink():
        fail("cached driver product directory is unsafe")
    directory = directory.resolve(strict=True)
    document = read_document(directory / "materialization.json")
    outputs = validate(document, expected)
    validate_files(directory, outputs)
    return document, outputs, directory


def copy_file(source, destination, maximum):
    payload = read_regular(source, maximum, source.name)
    with destination.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(destination, 0o600)


def command_stage(args):
    source_document = read_document(args.materialization)
    expected = {"steamos": args.steamos, "kernel": args.kernel,
                "nvidia": args.nvidia, "revision": args.support_revision}
    outputs = validate(source_document, expected)
    destination = args.destination.absolute()
    parent = destination.parent
    try:
        parent_info = parent.lstat()
    except OSError:
        fail("cached driver product parent is unavailable")
    if (not stat.S_ISDIR(parent_info.st_mode) or parent.is_symlink()
            or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022):
        fail("cached driver product parent is unsafe")
    if destination.exists() or destination.is_symlink():
        fail("cached driver product destination already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    os.chmod(temporary, 0o700)
    try:
        for role, record in outputs.items():
            maximum = MAX_ARCHIVE if role == "archive" else MAX_METADATA
            copy_file(args.input_dir / record["name"], temporary / record["name"], maximum)
        result = temporary / "materialization.json"
        result.write_bytes(canonical(source_document))
        os.chmod(result, 0o600)
        validate_files(temporary, outputs)
        os.replace(temporary, destination)
        temporary = None
        descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    print(canonical(source_document).decode(), end="")


def command_show(args):
    expected = {"steamos": args.steamos, "kernel": args.kernel,
                "nvidia": args.nvidia, "revision": args.support_revision}
    document, outputs, directory = load(args.directory, expected)
    result = dict(document)
    result["paths"] = {role: str(directory / record["name"])
                       for role, record in outputs.items()}
    print(canonical(result).decode(), end="")


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "show"):
        command = subparsers.add_parser(name)
        command.add_argument("--steamos", required=True)
        command.add_argument("--kernel", required=True)
        command.add_argument("--nvidia", required=True)
        command.add_argument("--support-revision", required=True)
        if name == "stage":
            command.add_argument("--materialization", required=True, type=Path)
            command.add_argument("--input-dir", required=True, type=Path)
            command.add_argument("--destination", required=True, type=Path)
        else:
            command.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    (command_stage if args.command == "stage" else command_show)(args)


if __name__ == "__main__":
    main()
