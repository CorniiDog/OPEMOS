#!/usr/bin/env python3
"""Fail-closed validation for an optional reviewed Valve recovery artifact."""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from atomic_output import atomic_create_bytes


MAX_MANIFEST = 64 * 1024
MAX_COMPRESSED = 8 * 1024 * 1024 * 1024
MAX_RAW = 32 * 1024 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
FILENAME = re.compile(r"steamdeck-recovery-[A-Za-z0-9._+-]+\.img\.bz2")
OFFICIAL_PAGE = "https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227"


def fail(message):
    raise SystemExit(f"validate_steamos_recovery_input.py: {message}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("manifest contains duplicate JSON keys")
        result[key] = value
    return result


def safe_file(path, maximum, label):
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
        payload = bytearray() if maximum == MAX_MANIFEST else None
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OSError
            digest.update(chunk)
            if payload is not None:
                payload.extend(chunk)
        if total != metadata.st_size:
            raise OSError
        return metadata.st_size, digest.hexdigest(), bytes(payload) if payload is not None else None
    except OSError:
        fail(f"{label} is missing, linked, changed, or excessive")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    _, manifest_sha, manifest_payload = safe_file(args.manifest, MAX_MANIFEST, "manifest")
    try:
        document = json.loads(manifest_payload, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        fail("manifest is malformed")
    if (not isinstance(document, dict) or set(document) != {
            "schemaVersion", "status", "officialPage", "publisher", "images", "requirements"}
            or document.get("schemaVersion") != 1
            or document.get("status") != "reviewed"
            or document.get("officialPage") != OFFICIAL_PAGE
            or document.get("publisher") != "Valve Corporation"):
        fail("no reviewed Valve recovery image is configured")
    requirements = document["requirements"]
    if requirements != {
            "artifactFormat": "bzip2-raw-disk", "immutableSha256": True,
            "maximumCompressedBytes": MAX_COMPRESSED, "maximumRawBytes": MAX_RAW,
            "reviewedSourceEvidence": True}:
        fail("recovery provenance requirements are unsupported")
    images = document["images"]
    if not isinstance(images, list) or not 1 <= len(images) <= 8:
        fail("reviewed recovery image set is invalid")
    matches = []
    seen = set()
    for image in images:
        if (not isinstance(image, dict) or set(image) != {
                "filename", "compressedSha256", "compressedSizeBytes", "rawSizeBytes",
                "releaseIdentity", "sourceEvidence"}
                or not isinstance(image.get("filename"), str)
                or FILENAME.fullmatch(image["filename"]) is None
                or image["filename"] in seen
                or not isinstance(image.get("compressedSha256"), str)
                or SHA256.fullmatch(image["compressedSha256"]) is None
                or not isinstance(image.get("compressedSizeBytes"), int)
                or isinstance(image.get("compressedSizeBytes"), bool)
                or not 0 < image["compressedSizeBytes"] <= MAX_COMPRESSED
                or not isinstance(image.get("rawSizeBytes"), int)
                or isinstance(image.get("rawSizeBytes"), bool)
                or not 0 < image["rawSizeBytes"] <= MAX_RAW
                or not isinstance(image.get("releaseIdentity"), str)
                or not 1 <= len(image["releaseIdentity"]) <= 128
                or not isinstance(image.get("sourceEvidence"), str)
                or not image["sourceEvidence"].startswith("https://help.steampowered.com/")):
            fail("reviewed recovery image record is malformed")
        seen.add(image["filename"])
        if image["filename"] == args.archive.name:
            matches.append(image)
    if len(matches) != 1:
        fail("archive filename is not uniquely reviewed")
    expected = matches[0]
    size, digest, _ = safe_file(args.archive, MAX_COMPRESSED, "recovery archive")
    if size != expected["compressedSizeBytes"] or digest != expected["compressedSha256"]:
        fail("recovery archive does not match reviewed size and SHA-256")
    result = {
        "schemaVersion": 1, "status": "verified", "publisher": "Valve Corporation",
        "releaseIdentity": expected["releaseIdentity"], "filename": expected["filename"],
        "compressedSizeBytes": size, "compressedSha256": digest,
        "rawSizeBytes": expected["rawSizeBytes"], "manifestSha256": manifest_sha,
    }
    try:
        atomic_create_bytes(args.output,
                            (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                            mode=0o600)
    except (FileExistsError, OSError):
        fail("verification output could not be created safely")


if __name__ == "__main__":
    main()
