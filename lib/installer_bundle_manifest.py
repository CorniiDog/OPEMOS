#!/usr/bin/env python3
"""Create or validate the canonical OPEMOS installer-consumer bundle manifest."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


SCHEMA_VERSION = 1
KIND = "opemos-installer-bundle"
REPOSITORY = "CorniiDog/open-gpu-kernel-modules-steamos-support"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SAFE_PATH = re.compile(r"[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*")

# This is the canonical support-owned inventory. Roles are descriptive and
# bounded; consumers must use paths, modes, sizes, and hashes as authority.
FILES = (
    ("bootstrap/install_to_root.sh", "installer-entrypoint", "0755"),
    ("bootstrap/install_recovery_guardian_to_root.sh", "installer-entrypoint", "0755"),
    ("bootstrap/launch_desktop_companion.sh", "device-entrypoint", "0755"),
    ("bootstrap/launch_interstitial.sh", "device-entrypoint", "0755"),
    ("bootstrap/run_guardian_with_interstitial.sh", "device-entrypoint", "0755"),
    ("bootstrap/recoveryctl.sh", "device-entrypoint", "0755"),
    ("bootstrap/online_install.sh", "device-entrypoint", "0755"),
    ("lib/common.sh", "runtime-helper", "0644"),
    ("lib/recovery_status.py", "runtime-helper", "0755"),
    ("lib/desktop_update_generations.py", "runtime-helper", "0755"),
    ("lib/interstitial_progress.py", "runtime-helper", "0755"),
    ("lib/validate_interstitial_binary.py", "runtime-helper", "0755"),
    ("lib/recovery_transaction.py", "runtime-helper", "0755"),
    ("lib/recovery_release_plan.py", "runtime-helper", "0755"),
    ("lib/update_recovery_grub_args.py", "runtime-helper", "0755"),
    ("lib/open_opemos_contract.py", "runtime-helper", "0755"),
    ("lib/validate_recovery_install_path.py", "runtime-helper", "0755"),
    ("lib/run_in_process_group.py", "runtime-helper", "0644"),
    ("lib/verify_bind_mount.py", "installer-helper", "0755"),
    ("lib/update_grub_nvidia_args.py", "installer-helper", "0755"),
    ("lib/validate_install_inputs.py", "installer-helper", "0755"),
    ("lib/authenticated_cache_bundle.py", "installer-helper", "0644"),
    ("lib/resolve_authenticated_install_bundle.py", "installer-helper", "0755"),
    ("lib/write_install_result.py", "installer-helper", "0755"),
    ("lib/capture_bounded_command.py", "installer-helper", "0755"),
    ("lib/measure_btrfs_payload.py", "installer-helper", "0755"),
    ("lib/atomic_output.py", "installer-helper", "0644"),
    ("lib/prepare_pacman_config.py", "installer-helper", "0644"),
    ("lib/gaming_payload_profiles.py", "policy-helper", "0755"),
    ("lib/repack_gaming_userspace.py", "installer-helper", "0755"),
    ("lib/verify_installed_modules.py", "installer-helper", "0755"),
    ("lib/verify_installed_userspace.py", "installer-helper", "0755"),
    ("lib/check_initramfs_workspace.py", "installer-helper", "0755"),
    ("lib/run_pacman_transaction.py", "installer-helper", "0755"),
    ("lib/snapshot_install_input.py", "installer-helper", "0644"),
    ("lib/snapshot_target_execution.py", "installer-helper", "0755"),
    ("lib/verify_initramfs.py", "installer-helper", "0755"),
    ("lib/validate_install_contract.py", "contract-validator", "0755"),
    ("lib/generate_installer_progress_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_result_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_validation_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_module_verification_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_userspace_verification_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_initramfs_verification_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_initramfs_workspace_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_payload_receipt_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/generate_installer_gaming_payload_fixtures.py", "contract-fixture-generator", "0755"),
    ("lib/payload_receipt.py", "installer-helper", "0644"),
    ("lib/resolve_target.py", "resolver", "0755"),
    ("lib/select_release.py", "resolver", "0755"),
    ("lib/installer_bundle_manifest.py", "bundle-manifest-tool", "0755"),
    ("contracts/schemas/resolver-result-v2.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-progress-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-result-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-validation-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-module-verification-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-userspace-verification-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-initramfs-verification-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-initramfs-workspace-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-payload-receipt-v1.schema.json", "contract-schema", "0644"),
    ("contracts/schemas/installer-gaming-payload-v1.schema.json", "contract-schema", "0644"),
    ("contracts/fixtures/resolver-compatibility-v2.json", "contract-fixture", "0644"),
    ("policies/exact-target-builds-v1.json", "build-policy", "0644"),
    ("trust/nvidia-userspace-package-signers.json", "trust-policy", "0644"),
    ("trust/keyrings/archlinux-nvidia-userspace-2025-08-01.gpg", "trust-keyring", "0644"),
    ("locks/userspace/steamos-3.8.14-nvidia-575.64.05.json", "userspace-lock", "0644"),
    ("profiles/gaming/reviewed-policy-v1.json", "gaming-policy", "0644"),
    ("profiles/gaming/gaming-no-cuda-v1-steamos-3.8.14-nvidia-575.64.05-k6.16.12-valve24.4-1-neptune-616-gfe145653a794.json", "gaming-profile", "0644"),
    ("support/recovery/opemos-nvidia-guardian.service.in", "device-service", "0644"),
    ("support/recovery/opemos-interstitial.service.in", "device-service", "0644"),
    ("support/recovery/opemos-nvidia-repair.service.in", "device-service", "0644"),
    ("support/recovery/opemos-nvidia-repair.timer", "device-service", "0644"),
    ("support/recovery/90-opemos-nvidia-repair", "device-hook", "0755"),
    ("support/recovery/90-opemos-nvidia-guardian.conf", "device-config", "0644"),
    ("trust/desktop-update-signers.json", "trust-policy", "0644"),
)


class ContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def git(root, *arguments, binary=False):
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ContractError("Git could not read the requested support commit")
    return completed.stdout if binary else completed.stdout.decode("utf-8", "strict")


def validate_inventory(inventory):
    if not 1 <= len(inventory) <= 256:
        raise ContractError("bundle inventory count is invalid")
    seen = set()
    for path, role, mode in inventory:
        if (not isinstance(path, str) or len(path) > 1024
                or SAFE_PATH.fullmatch(path) is None
                or Path(path).is_absolute()
                or any(part in {"", ".", ".."} for part in Path(path).parts)
                or path in seen or not isinstance(role, str)
                or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", role) is None
                or mode not in {"0644", "0755"}):
            raise ContractError("bundle inventory contains an unsafe record")
        seen.add(path)


def commit_file(root, commit, path, expected_mode):
    listing = git(root, "ls-tree", commit, "--", path).rstrip("\n")
    try:
        identity, listed_path = listing.split("\t", 1)
        mode, kind, object_id = identity.split(" ", 2)
    except ValueError:
        raise ContractError(f"required bundle file is absent from commit: {path}") from None
    if listed_path != path or kind != "blob" or mode not in {"100644", "100755"}:
        raise ContractError(f"required bundle path is not a regular Git blob: {path}")
    actual_mode = "0755" if mode == "100755" else "0644"
    if actual_mode != expected_mode:
        raise ContractError(f"required bundle file mode differs from policy: {path}")
    try:
        size = int(git(root, "cat-file", "-s", object_id).strip())
    except ValueError:
        raise ContractError(f"required bundle file size is invalid: {path}") from None
    if not 1 <= size <= MAX_FILE_BYTES:
        raise ContractError(f"required bundle file size is invalid: {path}")
    payload = git(root, "cat-file", "blob", object_id, binary=True)
    if len(payload) != size:
        raise ContractError(f"required bundle file changed while reading commit: {path}")
    return payload


def build_manifest(root, commit, inventory=FILES):
    if root.is_symlink():
        raise ContractError("support repository or commit identity is invalid")
    root = root.resolve(strict=True)
    if not root.is_dir() or COMMIT.fullmatch(commit) is None:
        raise ContractError("support repository or commit identity is invalid")
    git(root, "cat-file", "-e", f"{commit}^{{commit}}")
    validate_inventory(inventory)
    files = []
    for path, role, mode in inventory:
        payload = commit_file(root, commit, path, mode)
        files.append({
            "path": path,
            "role": role,
            "mode": mode,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    files.sort(key=lambda record: record["path"])
    identity = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "repository": REPOSITORY,
        "supportCommit": commit,
        "files": files,
    }
    return {**identity, "bundleId": hashlib.sha256(canonical(identity)).hexdigest()}


def strict_json(payload):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ContractError("bundle manifest contains a duplicate JSON key")
            value[key] = item
        return value

    try:
        return json.loads(payload, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ContractError("bundle manifest contains a non-finite number")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("bundle manifest is not canonical JSON") from error


def validate_manifest(document, expected_commit=None, inventory=FILES):
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion", "kind", "repository", "supportCommit", "files", "bundleId"
    }:
        raise ContractError("bundle manifest fields are not canonical")
    commit = document["supportCommit"]
    if (document["schemaVersion"] != SCHEMA_VERSION or document["kind"] != KIND
            or document["repository"] != REPOSITORY or not isinstance(commit, str)
            or COMMIT.fullmatch(commit) is None
            or expected_commit is not None and commit != expected_commit):
        raise ContractError("bundle manifest identity is invalid")
    validate_inventory(inventory)
    expected = {path: (role, mode) for path, role, mode in inventory}
    files = document["files"]
    if not isinstance(files, list) or len(files) != len(expected):
        raise ContractError("bundle manifest file set is incomplete")
    previous = ""
    seen = set()
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "role", "mode", "size", "sha256"}:
            raise ContractError("bundle manifest file record is not canonical")
        path = record["path"]
        if (not isinstance(path, str) or path <= previous or path in seen or path not in expected
                or (record["role"], record["mode"]) != expected[path]
                or not isinstance(record["size"], int) or isinstance(record["size"], bool)
                or not 1 <= record["size"] <= MAX_FILE_BYTES
                or not isinstance(record["sha256"], str)
                or SHA256.fullmatch(record["sha256"]) is None):
            raise ContractError("bundle manifest file record is invalid")
        previous = path
        seen.add(path)
    identity = {key: document[key] for key in (
        "schemaVersion", "kind", "repository", "supportCommit", "files"
    )}
    if document["bundleId"] != hashlib.sha256(canonical(identity)).hexdigest():
        raise ContractError("bundle manifest identity hash is invalid")
    return document


def read_manifest(path):
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode)
            or not 1 <= info.st_size <= MAX_MANIFEST_BYTES):
        raise ContractError("bundle manifest file is unsafe or excessive")
    payload = path.read_bytes()
    document = strict_json(payload)
    if payload != canonical(document) + b"\n":
        raise ContractError("bundle manifest serialization is not canonical")
    return document


def write_create_only(path, payload):
    parent = path.parent.resolve(strict=True)
    if (not parent.is_dir() or path.name in {"", ".", ".."}
            or "/" in path.name or "\\" in path.name):
        raise ContractError("bundle manifest output directory is unsafe")
    output = parent / path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = None
    created = False
    complete = False
    try:
        descriptor = os.open(output, flags, 0o644)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = None
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        complete = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created and not complete:
            try:
                output.unlink()
            except FileNotFoundError:
                pass


def parse_args():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser("create", help="create a manifest from an immutable Git commit")
    create.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    create.add_argument("--support-commit", required=True)
    create.add_argument("--output", type=Path)
    create.add_argument("--dry-run", action="store_true")
    validate = commands.add_parser("validate", help="validate a manifest and its committed blobs")
    validate.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--expected-support-commit")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.operation == "create":
        if args.dry_run == (args.output is not None):
            raise ContractError("choose exactly one of --dry-run or --output")
        document = build_manifest(args.root, args.support_commit)
        payload = canonical(document) + b"\n"
        if args.dry_run:
            sys.stdout.buffer.write(payload)
        else:
            write_create_only(args.output, payload)
        return
    document = validate_manifest(
        read_manifest(args.manifest), args.expected_support_commit
    )
    expected = build_manifest(args.root, document["supportCommit"])
    if document != expected:
        raise ContractError("bundle manifest differs from its immutable support commit")
    print(json.dumps({
        "schemaVersion": 1,
        "status": "verified",
        "bundleId": document["bundleId"],
        "supportCommit": document["supportCommit"],
        "files": len(document["files"]),
    }, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ContractError, OSError) as error:
        raise SystemExit(f"installer_bundle_manifest.py: {error}") from None
