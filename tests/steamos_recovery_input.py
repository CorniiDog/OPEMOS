#!/usr/bin/env python3
"""Provenance and hostile-input tests for optional Valve recovery media."""

import hashlib
import bz2
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_steamos_recovery_input.py"
DECOMPRESSOR = ROOT / "lib/decompress_bzip2_image.py"
POLICY = ROOT / "trust/steamos-recovery-images.json"


def run(manifest, archive, output):
    return subprocess.run([str(VALIDATOR), "--manifest", str(manifest),
                           "--archive", str(archive), "--output", str(output)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def reviewed(archive):
    payload = archive.read_bytes()
    return {
        "schemaVersion": 1, "status": "reviewed",
        "officialPage": "https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227",
        "publisher": "Valve Corporation",
        "images": [{
            "filename": archive.name, "compressedSha256": hashlib.sha256(payload).hexdigest(),
            "compressedSizeBytes": len(payload), "rawSizeBytes": 64 * 1024 * 1024,
            "releaseIdentity": "deterministic-fixture-only",
            "sourceEvidence": "https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227#fixture",
        }],
        "requirements": {
            "artifactFormat": "bzip2-raw-disk", "immutableSha256": True,
            "maximumCompressedBytes": 8 * 1024 * 1024 * 1024,
            "maximumRawBytes": 32 * 1024 * 1024 * 1024,
            "reviewedSourceEvidence": True,
        },
    }


def main():
    with tempfile.TemporaryDirectory(prefix="steamos-recovery-input-") as temporary:
        root = Path(temporary)
        archive = root / "steamdeck-recovery-fixture.img.bz2"
        archive.write_bytes(b"BZh9deterministic fixture")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps(reviewed(archive)))
        output = root / "result.json"
        completed = run(manifest, archive, output)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(output.read_text())["status"] == "verified"

        assert run(POLICY, archive, root / "unconfigured.json").returncode != 0
        archive.write_bytes(archive.read_bytes() + b"corrupt")
        assert run(manifest, archive, root / "corrupt.json").returncode != 0
        archive.write_bytes(b"BZh9deterministic fixture")

        linked = root / "linked.img.bz2"
        linked.symlink_to(archive)
        linked_manifest = reviewed(archive)
        linked_manifest["images"][0]["filename"] = linked.name
        linked_policy = root / "linked.json"
        linked_policy.write_text(json.dumps(linked_manifest))
        assert run(linked_policy, linked, root / "linked-result.json").returncode != 0

        duplicate = root / "duplicate.json"
        duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}')
        assert run(duplicate, archive, root / "duplicate-result.json").returncode != 0

        partial = root / "steamdeck-recovery-partial.img.bz2"
        partial.write_bytes(b"BZh9")
        assert run(manifest, partial, root / "partial-result.json").returncode != 0

        oversized = root / "steamdeck-recovery-oversized.img.bz2"
        with oversized.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 * 1024 + 1)
        oversized_document = reviewed(archive)
        oversized_document["images"][0]["filename"] = oversized.name
        oversized_manifest = root / "oversized.json"
        oversized_manifest.write_text(json.dumps(oversized_document))
        assert run(oversized_manifest, oversized,
                   root / "oversized-result.json").returncode != 0

        raw = b"synthetic SteamOS disk" * 4096
        compressed = root / "steamdeck-recovery-decompress.img.bz2"
        compressed.write_bytes(bz2.compress(raw))
        image = root / "recovery.img"
        completed = subprocess.run([
            str(DECOMPRESSOR), "--input", str(compressed), "--output", str(image),
            "--expected-bytes", str(len(raw))], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        assert completed.returncode == 0, completed.stderr
        assert image.read_bytes() == raw
        preserved = root / "preserved.img"
        preserved.write_bytes(b"user data")
        assert subprocess.run([
            str(DECOMPRESSOR), "--input", str(compressed), "--output", str(preserved),
            "--expected-bytes", str(len(raw))], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL).returncode != 0
        assert preserved.read_bytes() == b"user data"
        corrupt_bzip = root / "corrupt.img.bz2"
        corrupt_bzip.write_bytes(compressed.read_bytes()[:-3])
        partial_output = root / "partial.img"
        assert subprocess.run([
            str(DECOMPRESSOR), "--input", str(corrupt_bzip), "--output",
            str(partial_output), "--expected-bytes", str(len(raw))],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
        assert not partial_output.exists() and not Path(str(partial_output) + ".partial").exists()


if __name__ == "__main__":
    main()
