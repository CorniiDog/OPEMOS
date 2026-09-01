#!/usr/bin/env python3
"""Create a minimal binary gpgv keyring for reviewed NVIDIA package signers."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "trust/nvidia-userspace-package-signers.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024


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
    if (args.source.is_symlink() or not args.source.is_file()
            or args.source.stat().st_size > MAX_SOURCE_BYTES):
        raise SystemExit("Source key material is not a regular file.")
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("Refusing to overwrite an existing prepared keyring.")
    if (args.manifest.is_symlink() or not args.manifest.is_file()
            or args.manifest.stat().st_size > MAX_MANIFEST_BYTES):
        raise SystemExit("Package signer manifest is unsafe or excessive.")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Package signer manifest is unreadable or malformed.") from None
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1
            or not isinstance(manifest.get("signers"), list)
            or len(manifest["signers"]) > 256):
        raise SystemExit("Unsupported package signer manifest schema.")
    try:
        fingerprints = sorted({
            signer["fingerprint"].upper()
            for signer in manifest["signers"]
            if signer.get("status") == "active"
            and re.fullmatch(r"[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}",
                             signer.get("fingerprint", ""))
        })
        malformed_active = any(
            not isinstance(signer, dict)
            or signer.get("status") not in ("active", "revoked")
            or not re.fullmatch(r"[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}",
                                signer.get("fingerprint", ""))
            for signer in manifest["signers"]
        )
    except (AttributeError, KeyError, TypeError):
        raise SystemExit("Unsupported package signer manifest schema.")
    if malformed_active:
        raise SystemExit("Unsupported package signer manifest schema.")
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
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{args.output.name}.tmp-", dir=args.output.parent
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(exported)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(staged, 0o644)
        os.link(staged, args.output)
    except FileExistsError:
        raise SystemExit("Refusing to overwrite an existing prepared keyring.")
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
