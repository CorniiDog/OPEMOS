#!/usr/bin/env python3
"""Create structured provenance from validated offline-target build records."""

import argparse
import json
from pathlib import Path

from atomic_output import atomic_write_bytes


MAX_INPUT_BYTES = 1024 * 1024
EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
}


def metadata(path):
    values = {}
    for line in read_bounded(path, "Build information").splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            key, value = line.split("=", 1)
            if key in values:
                raise SystemExit(f"Build information duplicates field: {key}.")
            values[key] = value
    return values


def read_bounded(path, label):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_INPUT_BYTES):
            raise OSError
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise SystemExit(f"{label} is unreadable or excessive.") from None


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-info", required=True, type=Path)
    parser.add_argument("--modules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    info = metadata(args.build_info)
    try:
        module_validation = json.loads(read_bounded(args.modules, "Module validation"))
    except json.JSONDecodeError:
        raise SystemExit("Module validation is not valid JSON.") from None
    modules = module_validation.get("modules") if isinstance(module_validation, dict) else None
    if (not isinstance(module_validation, dict)
            or set(module_validation) != {"schemaVersion", "status", "modules"}
            or module_validation.get("schemaVersion") != 1
            or module_validation.get("status") != "verified"
            or not isinstance(modules, list) or len(modules) != 5
            or any(not isinstance(module, dict) or not isinstance(module.get("name"), str)
                   for module in modules)
            or {module["name"] for module in modules} != EXPECTED_MODULES):
        raise SystemExit("Module validation record is not an exact verified module set.")
    required_info = {
        "trust_classification", "steamos_version", "kernel_version",
        "nvidia_version", "build_architecture", "release_tag", "release_asset",
        "build_mode", "build_started_at", "build_completed_at", "build_os",
        "compiler_command", "compiler_version", "kernel_compiler_version",
        "compiler_major_match", "kernel_compiler_definition", "binutils_version",
        "make_version", "kmod_version", "support_repository", "support_commit",
        "support_dirty", "source_repository", "source_branch", "source_commit",
        "source_dirty", "header_package", "header_url", "header_sha256",
        "header_package_name", "header_package_version",
        "header_package_architecture", "header_signature_status",
        "header_signing_key_fingerprint", "header_primary_key_fingerprint",
        "header_authentication",
    }
    if any(key not in info or not info[key] for key in required_info):
        raise SystemExit("Build information is missing required provenance fields.")

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
        "modules": modules,
    }
    atomic_write_bytes(
        args.output,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


if __name__ == "__main__":
    main()
