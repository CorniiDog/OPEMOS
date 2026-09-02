#!/usr/bin/env python3
"""Produce bounded exact initramfs verification metadata."""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from atomic_output import atomic_create_bytes


MAX_IMAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_LISTING_BYTES = 8 * 1024 * 1024
MAX_LISTING_RECORDS = 200000
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
KERNEL = re.compile(r"[A-Za-z0-9._+~-]{1,255}")
SHA256 = re.compile(r"[0-9a-f]{64}")
REQUIRED_MODULES = (
    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
)
ROOTFS_ONLY_MODULES = ("nvidia-peermem.ko",)
COMPRESSIONS = ("", ".gz", ".xz", ".zst", ".lz4", ".lzo")
CONFIG_PATH = "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"


def fail(message):
    raise SystemExit(f"verify_initramfs.py: {message}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("JSON input contains duplicate keys")
        result[key] = value
    return result


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--execution-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path)
    parser.add_argument("--listing", action="append", default=[], type=Path)
    parser.add_argument("--image-sha256", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (not KERNEL.fullmatch(args.kernel) or not args.image
            or len(args.image) != len(args.listing)
            or len(args.image) != len(args.image_sha256)
            or any(SHA256.fullmatch(value) is None for value in args.image_sha256)):
        parser.error("a safe kernel and paired image/listing inputs are required")
    if len(args.image) > 32:
        parser.error("too many initramfs images")
    if not args.module:
        args.module = list(REQUIRED_MODULES)
    if tuple(args.module) != REQUIRED_MODULES:
        parser.error("the exact ordered early-boot NVIDIA module set is required")
    return args


def regular_bytes(path, maximum, label):
    descriptor = None
    try:
        metadata = path.lstat()
        if (path.is_symlink() or not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= maximum):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size)
                != (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size)):
            raise OSError
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) != metadata.st_size:
            raise OSError
        return bytes(payload)
    except OSError:
        fail(f"{label} is missing, linked, changed, or excessive")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def regular_digest(path, maximum, label):
    descriptor = None
    try:
        metadata = path.lstat()
        if (path.is_symlink() or not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= maximum):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size)
                != (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size)):
            raise OSError
        digest = hashlib.sha256()
        read_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            read_bytes += len(chunk)
            if read_bytes > maximum:
                raise OSError
            digest.update(chunk)
        if read_bytes != metadata.st_size:
            raise OSError
        return read_bytes, digest.hexdigest()
    except OSError:
        fail(f"{label} is missing, linked, changed, or excessive")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def execution_identity(path):
    payload = regular_bytes(path, MAX_MANIFEST_BYTES, "execution manifest")
    try:
        document = json.loads(payload, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        fail("execution manifest is malformed")
    if (not isinstance(document, dict) or document.get("schemaVersion") != 1
            or document.get("status") != "verified" or not isinstance(document.get("files"), list)):
        fail("execution manifest schema is malformed")
    indexed = {}
    for record in document["files"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            fail("execution manifest record is malformed")
        if record["path"] in indexed:
            fail("execution manifest contains duplicate paths")
        indexed[record["path"]] = record
    identities = {}
    for name in ("usr/bin/mkinitcpio", "usr/bin/lsinitcpio"):
        record = indexed.get(name)
        if (not isinstance(record, dict) or record.get("kind") != "file"
                or not isinstance(record.get("size"), int)
                or not 0 < record["size"] <= 8 * 1024 * 1024
                or not isinstance(record.get("sha256"), str)
                or SHA256.fullmatch(record["sha256"]) is None):
            fail(f"execution manifest lacks trusted {name}")
        identities[name.rsplit("/", 1)[1]] = {
            "path": "/" + name, "sizeBytes": record["size"], "sha256": record["sha256"],
        }
    return identities, indexed


def normalized_listing(path):
    payload = regular_bytes(path, MAX_LISTING_BYTES, "initramfs listing")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError:
        fail("initramfs listing is not UTF-8")
    if not 0 < len(lines) <= MAX_LISTING_RECORDS:
        fail("initramfs listing count is invalid")
    records = []
    for line in lines:
        # lsinitcpio may prefix modes/owners; its final whitespace-delimited
        # field is the archive path in both short and verbose output.
        value = line.strip().removeprefix("./")
        item = Path(value)
        if (not value or value.startswith("/") or value in (".", "..")
                or ".." in item.parts or any(char.isspace() or ord(char) < 32 for char in value)
                or len(value.encode()) > 1024):
            fail("initramfs listing contains an unsafe path")
        normalized = item.as_posix()
        records.append(normalized)
    return records


def main():
    args = arguments()
    tools, execution_records = execution_identity(args.execution_manifest)
    config = regular_bytes(args.config, 1024 * 1024, "managed modprobe configuration")
    config_record = execution_records.get(CONFIG_PATH)
    if (not isinstance(config_record, dict) or config_record.get("kind") != "file"
            or config_record.get("size") != len(config)
            or config_record.get("sha256") != hashlib.sha256(config).hexdigest()):
        fail("managed modprobe configuration does not match the execution snapshot")
    images = []
    image_names = set()
    for image_path, listing_path, expected_sha256 in zip(
            args.image, args.listing, args.image_sha256):
        if image_path.name in image_names:
            fail("duplicate initramfs image identity")
        image_names.add(image_path.name)
        image_size, image_sha256 = regular_digest(
            image_path, MAX_IMAGE_BYTES, "initramfs image")
        if image_sha256 != expected_sha256:
            fail("initramfs image changed after listing")
        listing = normalized_listing(listing_path)
        found = {}
        prefix = f"usr/lib/modules/{args.kernel}/"
        for module in args.module:
            candidates = sorted(
                path for path in listing
                if path.startswith(prefix)
                and Path(path).name in {module + suffix for suffix in COMPRESSIONS}
            )
            if len(candidates) != 1:
                fail(f"initramfs does not contain exactly one {module}")
            found[module] = candidates[0]
        for module in ROOTFS_ONLY_MODULES:
            if any(
                    path.startswith(prefix)
                    and Path(path).name in {
                        module + suffix for suffix in COMPRESSIONS
                    }
                    for path in listing):
                fail(f"initramfs unexpectedly contains rootfs-only module {module}")
        if listing.count(CONFIG_PATH) != 1:
            fail("initramfs lacks exactly one managed NVIDIA modprobe configuration")
        images.append({
            "filename": image_path.name, "sizeBytes": image_size,
            "sha256": image_sha256,
            "listingSha256": hashlib.sha256(("\n".join(listing) + "\n").encode()).hexdigest(),
            "entries": len(listing), "modules": found, "configPath": CONFIG_PATH,
        })
    document = {
        "schemaVersion": 1, "status": "verified", "kernelVersion": args.kernel,
        "requiredModules": list(REQUIRED_MODULES),
        "rootfsOnlyModules": list(ROOTFS_ONLY_MODULES),
        "tools": tools,
        "config": {"path": "/" + CONFIG_PATH, "sizeBytes": len(config),
                   "sha256": hashlib.sha256(config).hexdigest()},
        "images": images,
    }
    try:
        atomic_create_bytes(args.output,
                            (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                            mode=0o600)
    except FileExistsError:
        fail("refusing to overwrite initramfs verification metadata")
    except OSError:
        fail("initramfs verification metadata could not be created")


if __name__ == "__main__":
    main()
