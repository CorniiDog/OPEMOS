#!/usr/bin/env python3
"""Validate every immutable input to an offline-root NVIDIA installation."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
from update_grub_nvidia_args import REQUIRED as REQUIRED_KERNEL_ARGUMENTS

EXPECTED_MODULES = {
    "nvidia.ko",
    "nvidia-drm.ko",
    "nvidia-modeset.ko",
    "nvidia-peermem.ko",
    "nvidia-uvm.ko",
}
PACKAGE_SIGNER_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "trust/nvidia-userspace-package-signers.json"
)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia-utils", required=True, type=Path)
    parser.add_argument("--nvidia-utils-signature", required=True, type=Path)
    parser.add_argument("--lib32-nvidia-utils", required=True, type=Path)
    parser.add_argument("--lib32-nvidia-utils-signature", required=True, type=Path)
    parser.add_argument("--package-keyring", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def fail(reason, message):
    raise ValueError(f"{reason}: {message}")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def package_metadata(path):
    try:
        completed = subprocess.run(
            ["bsdtar", "-xOf", str(path), ".PKGINFO"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail("userspace_package_invalid", f"Cannot read {path.name} metadata: {error}")
    fields = {}
    for line in completed.stdout.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            fields.setdefault(key, value)
    return fields


def package_members(path):
    try:
        completed = subprocess.run(
            ["bsdtar", "-tf", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail("userspace_package_invalid", f"Cannot list {path.name}: {error}")
    members = completed.stdout.splitlines()
    if not all(safe_member(name) for name in members):
        fail("userspace_package_unsafe", f"{path.name} contains an unsafe path")
    return members


def validate_package_links(path):
    try:
        completed = subprocess.run(
            ["bsdtar", "-tvf", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail("userspace_package_invalid", f"Cannot inspect links in {path.name}: {error}")
    for line in completed.stdout.splitlines():
        if not line or line[0] not in ("l", "h"):
            continue
        fields = line.split(maxsplit=8)
        if len(fields) != 9:
            fail("userspace_package_invalid", f"Cannot parse a link in {path.name}")
        relation = " -> " if line[0] == "l" else " link to "
        if relation not in fields[8]:
            fail("userspace_package_invalid", f"Cannot parse a link target in {path.name}")
        name, target = fields[8].split(relation, 1)
        member = PurePosixPath(name)
        destination = PurePosixPath(target)
        if destination.is_absolute():
            destination = PurePosixPath(*destination.parts[1:])
        else:
            destination = member.parent / destination
        depth = 0
        for component in destination.parts:
            if component in ("", "."):
                continue
            if component == "..":
                depth -= 1
                if depth < 0:
                    fail("userspace_package_unsafe", f"{path.name} has an escaping link")
            else:
                depth += 1


def verify_signature(package, signature, keyring):
    try:
        completed = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(signature), str(package)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail("userspace_signature_invalid", f"Signature verification failed for {package.name}: {error}")
    fingerprints = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "VALIDSIG":
            fingerprints.append(fields[2].upper())
    if len(fingerprints) != 1 or not re.fullmatch(r"[0-9A-F]{40}", fingerprints[0]):
        fail("userspace_signature_invalid", f"No unique full signer fingerprint for {package.name}")
    return fingerprints[0]


def require_reviewed_signer(fingerprint, package_name):
    try:
        manifest = json.loads(PACKAGE_SIGNER_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail("userspace_trust_invalid", f"Cannot read package signer policy: {error}")
    if manifest.get("schemaVersion") != 1:
        fail("userspace_trust_invalid", "unsupported package signer policy schema")
    matches = [
        signer
        for signer in manifest.get("signers", [])
        if signer.get("fingerprint", "").upper() == fingerprint
        and signer.get("status") == "active"
        and package_name in signer.get("packages", [])
    ]
    if len(matches) != 1:
        fail(
            "userspace_signer_rejected",
            f"{package_name} signer is not active in reviewed policy",
        )


def plain_os_release(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def module_metadata(path, field):
    try:
        return subprocess.run(
            ["modinfo", "-F", field, str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        fail("module_metadata_invalid", f"Cannot read {path.name}: {error}")


def validate(args):
    appliance_architecture = os.uname().machine
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        appliance_architecture = os.environ.get(
            "PROJECT_TEST_APPLIANCE_ARCH", appliance_architecture
        )
    if appliance_architecture != "x86_64":
        fail("unsupported_appliance_architecture", "installation validation requires x86_64")
    if not re.fullmatch(r"[A-Za-z0-9._+~-]+", args.kernel):
        fail("invalid_target", "target kernel contains unsupported characters")
    root = args.root.resolve(strict=True)
    if root == Path("/"):
        fail("unsafe_target_root", "refusing the appliance root")
    os_release = root / "etc/os-release"
    if not os_release.is_file() or os_release.is_symlink():
        fail("invalid_target", "target lacks a safe regular /etc/os-release")
    identity = plain_os_release(os_release)
    if identity.get("ID") != "steamos" or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", identity.get("VERSION_ID", "")
    ):
        fail("invalid_target", "target is not a versioned SteamOS root")
    if not (root / "usr/lib/modules" / args.kernel).is_dir():
        fail("kernel_mismatch", "exact target module directory is absent")
    boot = root / "boot"
    efi = root / "efi"
    grub_configuration = efi / "EFI/steamos/grub.cfg"
    if boot.is_symlink() or not boot.is_dir():
        fail("target_boot_invalid", "target rootfs /boot is absent or unsafe")
    if efi.is_symlink() or not efi.is_dir():
        fail("target_efi_invalid", "target EFI mount is absent or unsafe")
    try:
        grub_configuration.resolve(strict=True).relative_to(efi.resolve(strict=True))
    except (FileNotFoundError, RuntimeError, ValueError):
        fail("target_grub_invalid", "target EFI GRUB configuration is absent or unsafe")
    if grub_configuration.is_symlink() or not grub_configuration.is_file():
        fail("target_grub_invalid", "target EFI GRUB configuration is not a regular file")
    if grub_configuration.stat().st_size > 1024 * 1024:
        fail("target_grub_invalid", "target EFI GRUB configuration is unexpectedly large")
    try:
        grub_text = grub_configuration.read_text(encoding="utf-8")
    except UnicodeError:
        fail("target_grub_invalid", "target EFI GRUB configuration is not UTF-8 text")
    if not any(
        re.match(r"^\s*(?:linux|linuxefi|linux16)\s+\S+", line)
        for line in grub_text.splitlines()
    ):
        fail("target_grub_invalid", "target EFI GRUB configuration has no Linux entries")

    pacman_database = root / "usr/lib/holo/pacmandb"
    pacman_local = pacman_database / "local"
    try:
        resolved_database = pacman_database.resolve(strict=True)
        resolved_database.relative_to(root)
    except (FileNotFoundError, RuntimeError, ValueError):
        fail("target_pacman_database_missing", "target Holo pacman database is absent or unsafe")
    if (pacman_database.is_symlink() or not resolved_database.is_dir()
            or pacman_local.is_symlink() or not pacman_local.is_dir()):
        fail("target_pacman_database_invalid", "target Holo pacman database is not a safe directory")
    package_descriptions = []
    for description in pacman_local.glob("*/desc"):
        try:
            description.resolve(strict=True).relative_to(resolved_database)
        except (FileNotFoundError, RuntimeError, ValueError):
            fail("target_pacman_database_invalid", "target package database escapes its root")
        if description.is_file() and not description.is_symlink():
            package_descriptions.append(description)
    if not package_descriptions:
        fail("target_pacman_database_empty", "target Holo pacman database has no package records")

    for path in (
        args.archive,
        args.checksum,
        args.provenance,
        args.nvidia_utils,
        args.nvidia_utils_signature,
        args.lib32_nvidia_utils,
        args.lib32_nvidia_utils_signature,
        args.package_keyring,
    ):
        if not path.is_file():
            fail("input_missing", f"required input is absent: {path}")

    expected = args.checksum.read_text(encoding="utf-8").split()
    if not expected or not re.fullmatch(r"[0-9a-fA-F]{64}", expected[0]):
        fail("archive_checksum_invalid", "checksum sidecar is invalid")
    archive_sha = sha256(args.archive)
    if archive_sha != expected[0].lower():
        fail("archive_checksum_mismatch", "archive checksum does not match")
    provenance_bytes = args.provenance.read_bytes()
    provenance = json.loads(provenance_bytes)
    if provenance.get("schemaVersion") != 1:
        fail("provenance_invalid", "unsupported provenance schema")
    target = provenance.get("target", {})
    nvidia = target.get("nvidiaVersion", "")
    trust = provenance.get("trust", "")
    if target.get("kernelVersion") != args.kernel or target.get("architecture") != "x86_64":
        fail("provenance_target_mismatch", "provenance does not match the exact target")
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", nvidia):
        fail("provenance_invalid", "NVIDIA version is invalid")
    if trust not in ("locally-built-verified", "certified-published"):
        fail("artifact_trust_rejected", "artifact trust is not installable")

    with tempfile.TemporaryDirectory(prefix="offline-root-modules-") as temporary:
        temporary = Path(temporary)
        with tarfile.open(args.archive, "r:gz") as archive:
            members = archive.getmembers()
            if not all(safe_member(member.name) for member in members):
                fail("archive_path_unsafe", "module archive contains an unsafe path")
            allowed_names = {
                "modules",
                "modules/",
                "BUILD-INFO.txt",
                "PROVENANCE.json",
                *(f"modules/{name}" for name in EXPECTED_MODULES),
                *(f"modules/{name}.zst" for name in EXPECTED_MODULES),
            }
            if any(
                member.name not in allowed_names
                or (not member.isfile() and not member.isdir())
                for member in members
            ):
                fail("archive_layout_invalid", "module archive contains an unexpected entry")
            embedded = archive.extractfile("PROVENANCE.json")
            if embedded is None or embedded.read() != provenance_bytes:
                fail("provenance_mismatch", "external and embedded provenance differ")
            module_members = {
                PurePosixPath(member.name).name: member
                for member in members
                if member.isfile() and member.name.startswith("modules/")
            }
            normalized = {name.removesuffix(".zst") for name in module_members}
            if normalized != EXPECTED_MODULES or len(module_members) != 5:
                fail("module_set_incomplete", "archive lacks the exact five modules")
            modules = []
            for name, member in module_members.items():
                archive.extract(member, temporary, filter="data")
                modules.append(temporary / member.name)
        records = []
        for module in modules:
            version = module_metadata(module, "version")
            vermagic = module_metadata(module, "vermagic")
            if version != nvidia or vermagic.split(maxsplit=1)[0] != args.kernel:
                fail("module_metadata_mismatch", f"{module.name} does not match target")
            records.append((module.name.removesuffix(".zst"), sha256(module)))
        expected_modules = {
            item.get("name"): item.get("sha256", "").lower()
            for item in provenance.get("modules", [])
        }
        if dict(records) != expected_modules:
            fail("module_hash_mismatch", "module hashes do not match provenance")

    package_records = []
    for package, signature, expected_name in (
        (args.nvidia_utils, args.nvidia_utils_signature, "nvidia-utils"),
        (args.lib32_nvidia_utils, args.lib32_nvidia_utils_signature, "lib32-nvidia-utils"),
    ):
        metadata = package_metadata(package)
        if metadata.get("pkgname") != expected_name or metadata.get("arch") != "x86_64":
            fail("userspace_package_mismatch", f"{package.name} has wrong identity")
        pkgver = metadata.get("pkgver", "")
        if pkgver.rsplit("-", 1)[0] != nvidia:
            fail("userspace_version_mismatch", f"{expected_name} does not match {nvidia}")
        members = package_members(package)
        validate_package_links(package)
        signer = verify_signature(package, signature, args.package_keyring)
        require_reviewed_signer(signer, expected_name)
        pkgver_only, separator, pkgrel = pkgver.rpartition("-")
        if not separator or pkgver_only != nvidia or not pkgrel:
            fail("userspace_version_mismatch", f"{expected_name} has invalid pkgver/pkgrel")
        package_records.append(
            (expected_name, pkgver, pkgver_only, pkgrel, signer, sha256(package))
        )
        if expected_name == "nvidia-utils" and not any(
            re.fullmatch(
                rf"usr/lib/firmware/nvidia/{re.escape(nvidia)}/gsp[^/]*\.bin",
                name,
            )
            for name in members
        ):
            fail("gsp_firmware_missing", "nvidia-utils lacks exact-version GSP firmware")

    return {
        "schemaVersion": 1,
        "status": "verified",
        "trust": trust,
        "target": {
            "steamosVersion": identity["VERSION_ID"],
            "kernelVersion": args.kernel,
            "nvidiaVersion": nvidia,
            "architecture": "x86_64",
        },
        "archiveSha256": archive_sha,
        "pacmanDatabase": {
            "path": "/usr/lib/holo/pacmandb",
            "packageCount": len(package_descriptions),
        },
        "boot": {
            "rootfsBootPath": "/boot",
            "efiMountPath": "/efi",
            "grubConfiguration": "/efi/EFI/steamos/grub.cfg",
            "requiredKernelArguments": list(REQUIRED_KERNEL_ARGUMENTS),
        },
        "keyring": {
            "name": args.package_keyring.name,
            "sha256": sha256(args.package_keyring),
        },
        "packages": [
            {
                "name": name,
                "fullVersion": full_version,
                "pkgver": pkgver,
                "pkgrel": pkgrel,
                "signer": signer,
                "sha256": digest,
            }
            for name, full_version, pkgver, pkgrel, signer, digest in package_records
        ],
    }


def main():
    args = arguments()
    try:
        document = validate(args)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError) as error:
        text = str(error)
        reason, separator, message = text.partition(": ")
        if not separator:
            reason, message = "validation_failed", text
        document = {
            "schemaVersion": 1,
            "status": "failed",
            "reason": reason,
            "message": message,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
        staged.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        )
        staged.replace(args.output)
        print(f"validate_install_inputs.py: {error}", file=__import__("sys").stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    staged.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
