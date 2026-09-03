#!/usr/bin/env python3
"""Commit and verify a rootfs-resident OPEMOS payload receipt."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from atomic_output import atomic_write_bytes


RECEIPT_RELATIVE = Path(
    "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install"
)
MANIFEST_NAME = "receipt.json"
MAX_INPUTS = {
    "buildInfo": ("BUILD-INFO.txt", 1024 * 1024, False),
    "provenance": ("PROVENANCE.json", 1024 * 1024, True),
    "validation": ("validation.json", 16 * 1024 * 1024, True),
    "moduleVerification": ("module-verification.json", 1024 * 1024, True),
    "userspaceVerification": ("userspace-verification.json", 256 * 1024, True),
    "initramfsVerification": ("initramfs-verification.json", 256 * 1024, True),
}
MAX_MANIFEST_BYTES = 64 * 1024
EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_regular(path, maximum, expected_owner=None, require_nonwritable=False):
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= maximum
                or expected_owner is not None and before.st_uid != expected_owner
                or require_nonwritable and stat.S_IMODE(before.st_mode) & 0o022):
            raise ValueError("receipt input is not a bounded regular file")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
        if (len(payload) > maximum or len(payload) != after.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns, before.st_uid,
                    before.st_gid, stat.S_IMODE(before.st_mode), before.st_nlink)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns, after.st_uid,
                    after.st_gid, stat.S_IMODE(after.st_mode), after.st_nlink)
                or (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns, after.st_uid,
                    after.st_gid, stat.S_IMODE(after.st_mode), after.st_nlink)
                != (path_after.st_dev, path_after.st_ino, path_after.st_size,
                    path_after.st_mtime_ns, path_after.st_ctime_ns,
                    path_after.st_uid, path_after.st_gid,
                    stat.S_IMODE(path_after.st_mode), path_after.st_nlink)):
            raise ValueError("receipt input changed while being read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def load_json(payload):
    def reject_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def safe_root(root, allow_live_root=False):
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("target root must be an absolute non-symlink directory")
    resolved = root.resolve(strict=True)
    if (resolved == Path("/") and not allow_live_root) or not resolved.is_dir():
        raise ValueError("target root is unsafe")
    return resolved


def receipt_directory(root, create=False, allow_live_root=False,
                      expected_owner=None):
    current = safe_root(root, allow_live_root=allow_live_root)
    for component in RECEIPT_RELATIVE.parts:
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise ValueError("payload receipt directory is missing")
            current.mkdir(mode=0o755)
            metadata = os.lstat(current)
        if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
                or expected_owner is not None
                and (metadata.st_uid != expected_owner
                     or stat.S_IMODE(metadata.st_mode) & 0o022)):
            raise ValueError("payload receipt path is unsafe")
    return current


def target_from_documents(documents):
    validation = documents["validation"]
    provenance = documents["provenance"]
    modules = documents["moduleVerification"]
    userspace = documents["userspaceVerification"]
    initramfs = documents["initramfsVerification"]
    if any(not isinstance(document, dict) for document in (
        validation, provenance, modules, userspace, initramfs
    )):
        raise ValueError("receipt evidence document is malformed")
    target = validation.get("target") if isinstance(validation, dict) else None
    if (validation.get("schemaVersion") != 1
            or validation.get("status") != "verified"
            or not isinstance(target, dict)
            or target.get("architecture") != "x86_64"
            or provenance.get("schemaVersion") != 1
            or provenance.get("target") != target):
        raise ValueError("receipt target identity is inconsistent")
    required = {"steamosVersion", "kernelVersion", "nvidiaVersion", "architecture"}
    if not required <= set(target) or any(
            not isinstance(target[field], str) or not target[field]
            for field in required
    ):
        raise ValueError("receipt target identity is incomplete")
    module_records = modules.get("modules") if isinstance(modules, dict) else None
    if (modules.get("schemaVersion") != 1 or modules.get("status") != "verified"
            or not isinstance(module_records, list)
            or len(module_records) != len(EXPECTED_MODULES)
            or {record.get("moduleName") for record in module_records
                if isinstance(record, dict)} != EXPECTED_MODULES):
        raise ValueError("receipt module verification is incomplete")
    if (userspace.get("schemaVersion") != 1 or userspace.get("status") != "verified"
            or not isinstance(userspace.get("packages"), list)
            or not userspace["packages"]):
        raise ValueError("receipt userspace verification is incomplete")
    if (initramfs.get("schemaVersion") != 1 or initramfs.get("status") != "verified"
            or initramfs.get("kernelVersion") != target["kernelVersion"]):
        raise ValueError("receipt initramfs verification is inconsistent")
    return {field: target[field] for field in (
        "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture"
    )}


def receipt_id(target, records):
    identity = json.dumps(
        {"schemaVersion": 1, "target": target, "records": records},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(identity).hexdigest()


def commit_receipt(args):
    sources = {
        "buildInfo": args.build_info,
        "provenance": args.provenance,
        "validation": args.validation,
        "moduleVerification": args.module_verification,
        "userspaceVerification": args.userspace_verification,
        "initramfsVerification": args.initramfs_verification,
    }
    payloads = {}
    documents = {}
    for role, source in sources.items():
        _, maximum, structured = MAX_INPUTS[role]
        payload = read_regular(source, maximum)
        payloads[role] = payload
        if structured:
            documents[role] = load_json(payload)
    target = target_from_documents(documents)
    directory = receipt_directory(args.root, create=True)
    manifest = directory / MANIFEST_NAME
    if manifest.is_symlink() or (manifest.exists() and not manifest.is_file()):
        raise ValueError("existing payload receipt marker is unsafe")
    manifest.unlink(missing_ok=True)
    records = []
    for role in MAX_INPUTS:
        filename = MAX_INPUTS[role][0]
        payload = payloads[role]
        atomic_write_bytes(directory / filename, payload, mode=0o644)
        records.append({
            "role": role,
            "filename": filename,
            "sizeBytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    document = {
        "schemaVersion": 1,
        "status": "verified",
        "reason": "payload_receipt_committed",
        "target": target,
        "records": records,
        "receiptId": receipt_id(target, records),
    }
    atomic_write_bytes(
        manifest,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        mode=0o644,
    )
    return verify_receipt(args.root)


def verify_receipt(root, allow_live_root=False):
    resolved_root = safe_root(root, allow_live_root=allow_live_root)
    expected_owner = 0 if resolved_root == Path("/") else os.geteuid()
    directory = receipt_directory(
        resolved_root, allow_live_root=allow_live_root,
        expected_owner=expected_owner,
    )
    manifest = load_json(read_regular(
        directory / MANIFEST_NAME, MAX_MANIFEST_BYTES,
        expected_owner=expected_owner, require_nonwritable=True,
    ))
    if (not isinstance(manifest, dict)
            or manifest.get("schemaVersion") != 1
            or manifest.get("status") != "verified"
            or manifest.get("reason") != "payload_receipt_committed"
            or not isinstance(manifest.get("target"), dict)
            or not isinstance(manifest.get("records"), list)
            or len(manifest["records"]) != len(MAX_INPUTS)):
        raise ValueError("payload receipt manifest is malformed")
    expected_roles = list(MAX_INPUTS)
    if (any(not isinstance(record, dict) for record in manifest["records"])
            or [record.get("role") for record in manifest["records"]] != expected_roles):
        raise ValueError("payload receipt record set is malformed")
    documents = {}
    for record in manifest["records"]:
        role = record["role"]
        filename, maximum, structured = MAX_INPUTS[role]
        if record.get("filename") != filename:
            raise ValueError("payload receipt filename is inconsistent")
        payload = read_regular(
            directory / filename, maximum,
            expected_owner=expected_owner, require_nonwritable=True,
        )
        if (record.get("sizeBytes") != len(payload)
                or record.get("sha256") != hashlib.sha256(payload).hexdigest()):
            raise ValueError("payload receipt content differs from its manifest")
        if structured:
            documents[role] = load_json(payload)
    target = target_from_documents(documents)
    if (manifest["target"] != target
            or manifest.get("receiptId") != receipt_id(target, manifest["records"])):
        raise ValueError("payload receipt identity is inconsistent")
    return {
        "schemaVersion": 1,
        "status": "verified",
        "reason": "payload_receipt_verified",
        "target": target,
        "receiptId": manifest["receiptId"],
        "rootfsRelativePath": str(RECEIPT_RELATIVE / MANIFEST_NAME),
        "records": manifest["records"],
    }


def arguments():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    commit = subparsers.add_parser("commit")
    commit.add_argument("--root", required=True, type=Path)
    commit.add_argument("--build-info", required=True, type=Path)
    commit.add_argument("--provenance", required=True, type=Path)
    commit.add_argument("--validation", required=True, type=Path)
    commit.add_argument("--module-verification", required=True, type=Path)
    commit.add_argument("--userspace-verification", required=True, type=Path)
    commit.add_argument("--initramfs-verification", required=True, type=Path)
    commit.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = arguments()
    try:
        result = commit_receipt(args) if args.operation == "commit" else verify_receipt(args.root)
        atomic_write_bytes(
            args.output,
            (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise SystemExit(f"payload_receipt.py: {error}") from None


if __name__ == "__main__":
    main()
