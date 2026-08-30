#!/usr/bin/env python3
"""Validate every immutable input to an offline-root NVIDIA installation."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

EXPECTED_MODULES = {
    "nvidia.ko",
    "nvidia-drm.ko",
    "nvidia-modeset.ko",
    "nvidia-peermem.ko",
    "nvidia-uvm.ko",
}


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
        signer = verify_signature(package, signature, args.package_keyring)
        package_records.append((expected_name, pkgver, signer, sha256(package)))
        if expected_name == "nvidia-utils" and not any(
            re.fullmatch(r"usr/lib/firmware/nvidia/[^/]+/gsp[^/]*\.bin", name)
            for name in members
        ):
            fail("gsp_firmware_missing", "nvidia-utils lacks versioned GSP firmware")
    if package_records[0][1] != package_records[1][1]:
        fail("userspace_version_mismatch", "userspace package releases differ")

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
        "packages": [
            {"name": name, "version": version, "signer": signer, "sha256": digest}
            for name, version, signer, digest in package_records
        ],
    }


def main():
    args = arguments()
    try:
        document = validate(args)
    except (ValueError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"validate_install_inputs.py: {error}", file=__import__("sys").stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    staged.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
