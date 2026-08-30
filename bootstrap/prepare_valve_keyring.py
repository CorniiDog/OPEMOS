#!/usr/bin/env python3
"""Prepare the repository-pinned Valve package keyring for gpgv."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "trust" / "valve-package-signers.json"


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
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise SystemExit("Unsupported Valve trust-manifest schema.")

    with tempfile.TemporaryDirectory(prefix="valve-keyring-") as temporary:
        temporary = Path(temporary)
        package = temporary / manifest["source"]["package"]
        if args.package:
            shutil.copyfile(args.package, package)
        else:
            request = urllib.request.Request(
                manifest["source"]["url"],
                headers={"User-Agent": "open-gpu-kernel-modules-steamos-support/1"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.geturl() != manifest["source"]["url"]:
                    raise SystemExit("Valve keyring download redirected unexpectedly.")
                with package.open("wb") as output:
                    shutil.copyfileobj(response, output)

        require_hash(package, manifest["source"]["sha256"], "Valve keyring package")
        extraction = temporary / "extracted"
        extraction.mkdir()
        subprocess.run(["bsdtar", "-xf", str(package), "-C", str(extraction)], check=True)
        keyring = extraction / manifest["keyring"]["path"]
        require_hash(keyring, manifest["keyring"]["sha256"], "Extracted Valve keyring")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        staged = args.output.with_name(f".{args.output.name}.tmp")
        shutil.copyfile(keyring, staged)
        staged.replace(args.output)

    result = {
        "schemaVersion": 1,
        "status": "ready",
        "keyring": args.output.name,
        "keyringSha256": manifest["keyring"]["sha256"],
        "signers": [signer["fingerprint"] for signer in manifest["signers"]],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
