#!/usr/bin/env python3
"""Require a Valve package signer to be active in the reviewed trust manifest."""

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--fingerprint", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise SystemExit("Unsupported Valve trust-manifest schema.")
    fingerprint = args.fingerprint.upper()
    matching = [
        signer
        for signer in manifest.get("signers", [])
        if signer.get("fingerprint", "").upper() == fingerprint
    ]
    if not matching:
        raise SystemExit("Header signer is not pinned by the reviewed trust manifest.")
    if matching[0].get("status") != "active":
        raise SystemExit("Header signer is not active in the reviewed trust manifest.")
    print(fingerprint)


if __name__ == "__main__":
    main()
