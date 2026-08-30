#!/usr/bin/env python3
"""Create a minimal binary gpgv keyring for reviewed NVIDIA package signers."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "trust/nvidia-userspace-package-signers.json"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def run(arguments, **options):
    try:
        return subprocess.run(arguments, check=True, **options)
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Keyring preparation failed: {error}") from error


def main():
    args = parse_args()
    if not args.source.is_file():
        raise SystemExit("Source key material is not a regular file.")
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing prepared keyring.")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise SystemExit("Unsupported package signer manifest schema.")
    fingerprints = sorted(
        signer["fingerprint"].upper()
        for signer in manifest.get("signers", [])
        if signer.get("status") == "active"
    )
    if not fingerprints:
        raise SystemExit("Package signer manifest contains no active signers.")

    with tempfile.TemporaryDirectory(prefix="nvidia-package-keyring-") as home:
        os.chmod(home, 0o700)
        run(
            ["gpg", "--batch", "--homedir", home, "--import", str(args.source)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        listing = run(
            ["gpg", "--batch", "--homedir", home, "--with-colons", "--fingerprint"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        imported = {
            fields[9].upper()
            for line in listing.splitlines()
            if (fields := line.split(":"))[0] == "fpr" and len(fields) > 9
        }
        missing = [fingerprint for fingerprint in fingerprints if fingerprint not in imported]
        if missing:
            raise SystemExit("Source key material omits a reviewed package signer.")
        exported = run(
            ["gpg", "--batch", "--homedir", home, "--export", *fingerprints],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    if not exported:
        raise SystemExit("Prepared binary keyring is empty.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_bytes(exported)
    os.chmod(staged, 0o644)
    staged.replace(args.output)


if __name__ == "__main__":
    main()
