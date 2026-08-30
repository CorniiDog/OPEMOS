#!/usr/bin/env python3
"""Create structured provenance from validated offline-target build records."""

import argparse
import json
import os
from pathlib import Path


def metadata(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def write_atomic(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--modules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    info = metadata(args.build_info)
    module_validation = json.loads(args.modules.read_text(encoding="utf-8"))
    if module_validation.get("status") != "verified":
        raise SystemExit("Module validation record is not verified.")

    document = {
        "schemaVersion": 1,
        "trust": info["trust_classification"],
        "target": {
            "steamosVersion": info["steamos_version"],
            "kernelVersion": info["kernel_version"],
            "nvidiaVersion": info["nvidia_version"],
            "architecture": info["build_architecture"],
        },
        "artifact": {
            "releaseTag": info["release_tag"],
            "archive": info["release_asset"],
        },
        "build": {
            "mode": info["build_mode"],
            "startedAt": info["build_started_at"],
            "completedAt": info["build_completed_at"],
            "os": info["build_os"],
            "toolchain": {
                "compilerCommand": info["compiler_command"],
                "compilerVersion": info["compiler_version"],
                "kernelCompilerVersion": info["kernel_compiler_version"],
                "compilerMajorMatch": info["compiler_major_match"],
                "kernelCompilerDefinition": info["kernel_compiler_definition"],
                "binutils": info["binutils_version"],
                "make": info["make_version"],
                "kmod": info["kmod_version"],
            },
        },
        "support": {
            "repository": info["support_repository"],
            "commit": info["support_commit"],
            "dirty": info["support_dirty"],
        },
        "source": {
            "repository": info["source_repository"],
            "branch": info["source_branch"],
            "commit": info["source_commit"],
            "dirty": info["source_dirty"],
        },
        "headers": {
            "package": info["header_package"],
            "url": info["header_url"],
            "sha256": info["header_sha256"],
            "name": info["header_package_name"],
            "version": info["header_package_version"],
            "architecture": info["header_package_architecture"],
            "signatureStatus": info["header_signature_status"],
            "signingKeyFingerprint": info["header_signing_key_fingerprint"],
            "primaryKeyFingerprint": info["header_primary_key_fingerprint"],
            "authentication": info["header_authentication"],
        },
        "modules": module_validation["modules"],
    }
    write_atomic(args.output, document)


if __name__ == "__main__":
    main()
