#!/usr/bin/env python3
"""Commit and verify the exact module receipt for cached automatic repair."""

import argparse
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path

from atomic_output import atomic_write_bytes
import recovery_cached_product

MODULES = ("nvidia", "nvidia-drm", "nvidia-modeset", "nvidia-peermem", "nvidia-uvm")
RELATIVE = Path("var/lib/open-gpu-kernel-modules-steamos-support/recovery/cached-repair-receipt.json")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def safe_root(root):
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.resolve().is_dir():
        raise ValueError("cached repair receipt root is unsafe")
    return root.resolve()


def receipt_path(root):
    return safe_root(root) / RELATIVE


def expected_owner(root):
    return 0 if root == Path("/") else os.geteuid()


def confined(root, relative):
    current = root
    for component in Path(relative).parts:
        current /= component
        if current.is_symlink():
            raise ValueError("cached repair receipt path contains a symlink")
    return current


def module_records(root, kernel, archive=None):
    archived = {}
    if archive is not None:
        with tarfile.open(archive, "r:gz") as product:
            names = [member.name for member in product.getmembers()]
            for name in MODULES:
                if names.count(f"modules/{name}.ko.zst") != 1:
                    raise ValueError("cached repair archive module inventory is ambiguous")
                member = product.getmember(f"modules/{name}.ko.zst")
                if not member.isfile() or member.size <= 0 or member.size > 256 * 1024 * 1024:
                    raise ValueError("cached repair archive module is unsafe")
                archived[name] = product.extractfile(member).read()
    records = []
    for name in MODULES:
        relative = (Path("usr/lib/modules") / kernel /
                    "updates/open-gpu-kernel-modules-steamos" / f"{name}.ko.zst")
        path = confined(root, relative)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != expected_owner(root) or stat.S_IMODE(info.st_mode) != 0o644):
            raise ValueError("installed cached repair module is unsafe")
        payload = path.read_bytes()
        if not payload or len(payload) > 256 * 1024 * 1024:
            raise ValueError("installed cached repair module is unsafe")
        if archive is not None and payload != archived[name]:
            raise ValueError("installed module differs from authenticated cached archive")
        records.append({"name": name, "path": str(path.relative_to(root)),
                        "bytes": len(payload), "sha256": sha(payload)})
    return records


def commit(args):
    root = safe_root(args.root)
    expected = {"steamos": args.steamos, "kernel": args.kernel,
                "nvidia": args.nvidia, "revision": args.support_revision}
    document, outputs, cache = recovery_cached_product.load(args.cache, expected)
    archive = cache / outputs["archive"]["name"]
    result = {
        "schemaVersion": 1, "status": "verified", "reason": "cached_repair_receipt_committed",
        "target": document["target"], "core": document["core"], "source": document["source"],
        "archive": {"bytes": outputs["archive"]["bytes"], "sha256": outputs["archive"]["sha256"]},
        "provenanceSha256": outputs["provenance"]["sha256"],
        "buildInfoSha256": outputs["buildInfo"]["sha256"],
        "modules": module_records(root, args.kernel, archive),
    }
    path = confined(root, RELATIVE)
    current = root
    for component in RELATIVE.parts[:-1]:
        current /= component
        current.mkdir(mode=0o700, exist_ok=True)
        info = current.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_owner(root)
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise ValueError("cached repair receipt parent is unsafe")
    atomic_write_bytes(path, canonical(result), mode=0o644)
    return result


def verify(root, kernel, nvidia, support_revision):
    root = safe_root(root)
    path = confined(root, RELATIVE)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != expected_owner(root) or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_size > 64 * 1024):
        raise ValueError("cached repair receipt is unsafe")
    payload = path.read_bytes()
    document = json.loads(payload)
    if payload != canonical(document) or set(document) != {
            "schemaVersion", "status", "reason", "target", "core", "source", "archive",
            "provenanceSha256", "buildInfoSha256", "modules"}:
        raise ValueError("cached repair receipt is malformed")
    if (document["schemaVersion"] != 1 or document["status"] != "verified"
            or document["reason"] != "cached_repair_receipt_committed"
            or document["target"].get("kernelVersion") != kernel
            or document["target"].get("nvidiaVersion") != nvidia
            or document["target"].get("architecture") != "x86_64"
            or document["core"] != {"repository": "CorniiDog/OPEMOS", "commit": support_revision}
            or module_records(root, kernel) != document["modules"]):
        raise ValueError("cached repair receipt does not match installed modules")
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("commit", "verify"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--support-revision", required=True)
    parser.add_argument("--steamos")
    parser.add_argument("--cache")
    args = parser.parse_args()
    if args.command == "commit":
        if not args.steamos or not args.cache:
            parser.error("commit requires --steamos and --cache")
        result = commit(args)
    else:
        result = verify(args.root, args.kernel, args.nvidia, args.support_revision)
    print(canonical(result).decode(), end="")


if __name__ == "__main__":
    main()
