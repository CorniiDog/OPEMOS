#!/usr/bin/env python3
"""Prepare the repository-pinned Valve package keyring for gpgv."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
import sys


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "trust" / "valve-package-signers.json"
sys.path.insert(0, str(ROOT / "lib"))
from bsdtar_safety import ArchiveSafetyError, extract_single_member  # noqa: E402

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_KEYRING_BYTES = 64 * 1024 * 1024
MAX_GPG_LISTING_BYTES = 16 * 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(path, expected, description):
    actual = sha256(path)
    if actual != expected:
        raise SystemExit(
            f"{description} SHA256 mismatch: expected {expected}, got {actual}"
        )


def require_command(command):
    if shutil.which(command) is None:
        raise SystemExit(
            f"Required command not found: {command}. "
            "Install bsdtar and GnuPG before preparing the Valve keyring."
        )


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise SystemExit("Valve trust manifest is malformed.")
    source = manifest.get("source")
    keyring = manifest.get("keyring")
    signers = manifest.get("signers")
    package = source.get("package") if isinstance(source, dict) else None
    url = source.get("url") if isinstance(source, dict) else None
    member = keyring.get("path") if isinstance(keyring, dict) else None
    member_path = PurePosixPath(member) if isinstance(member, str) else None
    if (manifest.get("schemaVersion") != 1
            or not isinstance(package, str) or Path(package).name != package
            or package in (".", "..")
            or not isinstance(url, str) or not url.startswith("https://")
            or not isinstance(member, str) or not member
            or member_path.is_absolute() or ".." in member_path.parts
            or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", keyring.get("sha256", ""))
            or not isinstance(signers, list) or not 1 <= len(signers) <= 256
            or any(not isinstance(signer, dict)
                   or signer.get("status") not in ("active", "revoked")
                   or not re.fullmatch(r"[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}",
                                       signer.get("fingerprint", ""))
                   for signer in signers)):
        raise SystemExit("Valve trust manifest is malformed.")


def key_fingerprints(path):
    completed = subprocess.run(
        ["gpg", "--batch", "--show-keys", "--with-colons", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    if len(completed.stdout.encode("utf-8")) > MAX_GPG_LISTING_BYTES:
        raise SystemExit("Valve keyring fingerprint listing is excessive.")
    return {
        fields[9].upper()
        for line in completed.stdout.splitlines()
        if (fields := line.split(":"))[0] == "fpr" and len(fields) > 9
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and prepare the repository-pinned Valve package keyring."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--package",
        type=Path,
        help="use an already downloaded keyring package instead of the network",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    require_command("bsdtar")
    require_command("gpg")
    if (args.manifest.is_symlink() or not args.manifest.is_file()
            or args.manifest.stat().st_size > MAX_MANIFEST_BYTES):
        raise SystemExit("Valve trust manifest is unsafe or excessive.")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Valve trust manifest is unreadable or malformed.") from None
    validate_manifest(manifest)

    with tempfile.TemporaryDirectory(prefix="valve-keyring-") as temporary:
        temporary = Path(temporary)
        package = temporary / manifest["source"]["package"]
        if args.package:
            if (args.package.is_symlink() or not args.package.is_file()
                    or args.package.stat().st_size > MAX_PACKAGE_BYTES):
                raise SystemExit("Valve keyring package is unsafe or excessive.")
            shutil.copyfile(args.package, package)
        else:
            request = urllib.request.Request(
                manifest["source"]["url"],
                headers={"User-Agent": "OPEMOS/1"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.geturl() != manifest["source"]["url"]:
                    raise SystemExit("Valve keyring download redirected unexpectedly.")
                with package.open("wb") as output:
                    total = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_PACKAGE_BYTES:
                            raise SystemExit("Valve keyring download exceeds its size limit.")
                        output.write(chunk)

        require_hash(package, manifest["source"]["sha256"], "Valve keyring package")
        try:
            keyring_bytes = extract_single_member(
                package, manifest["keyring"]["path"], maximum=MAX_KEYRING_BYTES
            )
        except ArchiveSafetyError as error:
            raise SystemExit(f"Valve keyring package is unsafe: {error}")
        keyring = temporary / "authenticated-valve-keyring.gpg"
        keyring.write_bytes(keyring_bytes)
        require_hash(keyring, manifest["keyring"]["sha256"], "Extracted Valve keyring")

        expected_signers = {
            signer["fingerprint"].upper()
            for signer in manifest["signers"]
            if signer.get("status") == "active"
        }
        if not expected_signers:
            raise SystemExit("Valve trust manifest contains no active signers.")
        missing_signers = expected_signers - key_fingerprints(keyring)
        if missing_signers:
            raise SystemExit(
                "Pinned Valve signer is absent from the authenticated keyring: "
                + ", ".join(sorted(missing_signers))
            )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{args.output.name}.tmp-", dir=args.output.parent
        )
        os.close(descriptor)
        staged = Path(staged_name)
        try:
            subprocess.run(
                [
                    "gpg", "--batch", "--yes", "--dearmor",
                    "--output", str(staged), str(keyring),
                ],
                check=True,
            )
            os.chmod(staged, 0o644)
            staged.replace(args.output)
        finally:
            staged.unlink(missing_ok=True)

    result = {
        "schemaVersion": 1,
        "status": "ready",
        "keyring": args.output.name,
        "keyringSha256": sha256(args.output),
        "sourceKeyringSha256": manifest["keyring"]["sha256"],
        "format": "gpg-binary-keyring",
        "signers": sorted(expected_signers),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
