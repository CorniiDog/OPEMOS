#!/usr/bin/env python3
"""Validate every immutable input to an offline-root NVIDIA installation."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
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
MAX_MODULE_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MODULE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_METADATA_MEMBER_BYTES = 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACMAN_RECORD_BYTES = 1024 * 1024
MAX_PACMAN_RECORDS = 100_000
REQUIRED_BASE_PACKAGES = {"filesystem", "glibc", "pacman"}
INITRAMFS_BASE_RESERVE_BYTES = 64 * 1024 * 1024
ROOT_METADATA_RESERVE_BYTES = 64 * 1024 * 1024
VAR_RESERVE_BYTES = 16 * 1024 * 1024
EFI_RESERVE_BYTES = 1024 * 1024
MAX_PROGRESS_ATTEMPT = 1_000_000
PROGRESS_MIN_INTERVAL_SECONDS = 0.25
PROGRESS_MIN_BYTE_DELTA = 4 * 1024 * 1024


class ValidationFailure(ValueError):
    def __init__(self, reason, message, **details):
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message
        self.details = details


class ProgressReporter:
    def __init__(self, attempt):
        self.attempt = attempt
        self.last = {}

    def emit(self, phase, *, unit=None, completed=None, total=None,
             indeterminate=False, force=False):
        now = time.monotonic()
        previous = self.last.get(phase)
        if not force and previous is not None:
            previous_time, previous_completed = previous
            delta = (completed or 0) - previous_completed
            threshold = PROGRESS_MIN_BYTE_DELTA if unit == "bytes" else 100
            if now - previous_time < PROGRESS_MIN_INTERVAL_SECONDS and delta < threshold:
                return
        record = {
            "schemaVersion": 1,
            "attempt": self.attempt,
            "phase": phase,
            "indeterminate": indeterminate,
        }
        if not indeterminate:
            record.update({"unit": unit, "completed": completed, "total": total})
            self.last[phase] = (now, completed)
        print(
            "STEAMOS_NVIDIA_PROGRESS "
            + json.dumps(record, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )


def bounded_progress_attempt(value):
    try:
        attempt = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("progress attempt must be an integer") from error
    if not 0 <= attempt <= MAX_PROGRESS_ATTEMPT:
        raise argparse.ArgumentTypeError(
            f"progress attempt must be between 0 and {MAX_PROGRESS_ATTEMPT}"
        )
    return attempt


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
    parser.add_argument("--dependency-package", action="append", default=[], type=Path)
    parser.add_argument("--dependency-signature", action="append", default=[], type=Path)
    parser.add_argument("--package-keyring", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--progress-attempt", type=bounded_progress_attempt, default=0)
    return parser.parse_args()


def fail(reason, message, **details):
    raise ValidationFailure(reason, message, **details)


def sha256(path, progress=None):
    digest = hashlib.sha256()
    total = path.stat().st_size
    completed = 0
    if progress:
        progress.emit("hashing", unit="bytes", completed=0, total=total, force=True)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            completed += len(chunk)
            if progress:
                progress.emit("hashing", unit="bytes", completed=completed, total=total)
    if progress:
        progress.emit("hashing", unit="bytes", completed=completed, total=total, force=True)
    return digest.hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def require_safe_destination(root, relative):
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        fail("target_path_unsafe", f"unsafe target destination: {relative}")
    current = root
    for component in relative_path.parts:
        if component in ("", "."):
            continue
        current = current / component
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except NotADirectoryError:
            fail("target_path_unsafe", f"target destination traverses a non-directory: {relative}")
        if stat.S_ISLNK(mode):
            fail("target_path_unsafe", f"target destination traverses a symlink: {relative}")
        try:
            current.resolve(strict=True).relative_to(root)
        except (FileNotFoundError, RuntimeError, ValueError):
            fail("target_path_unsafe", f"target destination escapes the root: {relative}")


def pacman_desc_fields(path):
    record_name = path.parent.name
    if path.stat().st_size > MAX_PACMAN_RECORD_BYTES:
        fail("target_pacman_database_invalid", f"oversized package record: {record_name}/desc")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError:
        fail("target_pacman_database_invalid", f"non-UTF-8 package record: {record_name}/desc")
    fields = {}
    duplicate_fields = set()
    index = 0
    while index < len(lines):
        marker = lines[index]
        if re.fullmatch(r"%[A-Z0-9_]+%", marker):
            values = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"%[A-Z0-9_]+%", lines[index]):
                if lines[index]:
                    values.append(lines[index])
                index += 1
            field_name = marker.strip("%")
            if field_name in fields:
                duplicate_fields.add(field_name)
            else:
                fields[field_name] = values
            continue
        index += 1
    name = fields.get("NAME", [])
    version = fields.get("VERSION", [])
    installed_size = fields.get("ISIZE", [])
    invalid_fields = sorted(duplicate_fields)
    if len(name) != 1 or not re.fullmatch(r"[A-Za-z0-9@._+:-]+", name[0] if name else ""):
        invalid_fields.append("NAME")
    if (len(version) != 1 or not version[0]
            or any(character.isspace() for character in version[0])):
        invalid_fields.append("VERSION")
    if invalid_fields:
        invalid_fields = sorted(set(invalid_fields))
        fail(
            "target_pacman_database_invalid",
            f"malformed package record {record_name}/desc; invalid fields: "
            + ", ".join(invalid_fields),
            packageRecord=record_name,
            invalidFields=invalid_fields,
        )
    if path.parent.name != f"{name[0]}-{version[0]}":
        fail(
            "target_pacman_database_invalid",
            f"package record directory has wrong identity: {record_name}; "
            f"expected {name[0]}-{version[0]}",
            packageRecord=record_name,
            invalidFields=["NAME", "VERSION"],
        )
    installed_size_valid = (
        len(installed_size) == 1 and installed_size[0].isdigit()
    )
    return {
        "name": name[0],
        "version": version[0],
        "installedSize": int(installed_size[0]) if installed_size_valid else None,
        "installedSizeValid": installed_size_valid,
        "installedSizeIssue": (
            "missing" if not installed_size else "duplicate-or-nonnumeric"
        ) if not installed_size_valid else None,
        "packageRecord": record_name,
        "depends": fields.get("DEPENDS", []),
        "provides": fields.get("PROVIDES", []),
    }


def dependency_name(specification):
    name = re.split(r"[<>=]", specification, maxsplit=1)[0]
    if not re.fullmatch(r"[A-Za-z0-9@._+:-]+", name):
        fail("package_dependency_invalid", f"invalid dependency expression: {specification}")
    return name


def version_satisfies(candidate, operator, required):
    try:
        comparison = int(subprocess.run(
            ["vercmp", candidate, required], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        fail("package_dependency_invalid", f"cannot compare dependency versions: {error}")
    return {
        "=": comparison == 0,
        ">": comparison > 0,
        ">=": comparison >= 0,
        "<": comparison < 0,
        "<=": comparison <= 0,
    }[operator]


def record_satisfies(record, specification):
    match = re.fullmatch(r"([A-Za-z0-9@._+:-]+)(?:(>=|<=|=|>|<)(\S+))?", specification)
    if not match:
        fail("package_dependency_invalid", f"invalid dependency expression: {specification}")
    name, operator, required = match.groups()
    versions = []
    if record["name"] == name:
        versions.append(record["version"])
    for provided in record.get("provides", []):
        provided_match = re.fullmatch(r"([A-Za-z0-9@._+:-]+)(?:=(\S+))?", provided)
        if provided_match and provided_match.group(1) == name:
            versions.append(provided_match.group(2))
    if not versions:
        return False
    if operator is None:
        return True
    return any(version is not None and version_satisfies(version, operator, required) for version in versions)


def dependency_closure(incoming, installed):
    candidates = {record["name"]: record for record in installed.values()}
    candidates.update({record["name"]: record for record in incoming.values()})
    providers = {}
    for record in candidates.values():
        providers.setdefault(record["name"], []).append(record)
        for provided in record.get("provides", []):
            providers.setdefault(dependency_name(provided), []).append(record)
    closure = {}
    pending = list(incoming.values())
    while pending:
        record = pending.pop()
        if record["name"] in closure:
            continue
        closure[record["name"]] = record
        for specification in record.get("depends", []):
            name = dependency_name(specification)
            matches = [
                candidate for candidate in providers.get(name, [])
                if record_satisfies(candidate, specification)
            ]
            if not matches:
                fail(
                    "package_dependency_unsatisfied",
                    f"no incoming or installed package satisfies {specification}",
                    missingDependencies=[specification],
                    dependencyRequestedBy=record["name"],
                )
            exact = [candidate for candidate in matches if candidate["name"] == name]
            selected = sorted(exact or matches, key=lambda item: item["name"])[0]
            pending.append(selected)
    return [
        {
            "name": record["name"],
            "version": record["version"],
            "source": record["source"],
        }
        for record in sorted(closure.values(), key=lambda item: item["name"])
    ]


def available_bytes(path, test_name):
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        override = os.environ.get(f"PROJECT_TEST_{test_name}_AVAILABLE_BYTES")
        if override is not None:
            return int(override)
    filesystem = os.statvfs(path)
    return filesystem.f_bavail * filesystem.f_frsize


def tree_regular_bytes(path):
    if not path.exists():
        return 0
    total = 0
    for current_root, directories, files in os.walk(path, followlinks=False):
        current_root = Path(current_root)
        for name in directories + files:
            candidate = current_root / name
            if candidate.is_symlink():
                fail("target_path_unsafe", f"replacement tree contains a symlink: {candidate}")
            if candidate.is_file():
                total += candidate.stat().st_size
    return total


def compressed_module_bytes(path):
    if path.name.endswith(".zst"):
        return path.stat().st_size
    try:
        with tempfile.TemporaryFile() as compressed:
            subprocess.run(
                ["zstd", "-q", "-c", str(path)],
                check=True,
                stdout=compressed,
                stderr=subprocess.PIPE,
            )
            return compressed.tell()
    except (OSError, subprocess.CalledProcessError) as error:
        fail("module_size_unavailable", f"cannot estimate compressed module size: {error}")


def compression_context(root):
    try:
        completed = subprocess.run(
            ["findmnt", "-rn", "-T", str(root), "-o", "FSTYPE,OPTIONS"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        fields = completed.stdout.strip().split(maxsplit=1)
    except (OSError, subprocess.CalledProcessError):
        fields = []
    filesystem = fields[0] if fields else "unknown"
    options = fields[1].split(",") if len(fields) == 2 else []
    compression_options = [option for option in options if option.startswith("compress")]
    return {
        "filesystem": filesystem,
        "enabled": bool(compression_options) if filesystem == "btrfs" else False,
        "options": compression_options,
        "admissionBasis": "logical-uncompressed-conservative",
        "compressionSavingsCreditedBytes": 0,
    }


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
    dependencies = []
    provides = []
    for line in completed.stdout.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            fields.setdefault(key, value)
            if key == "depend":
                dependencies.append(value)
            elif key == "provides":
                provides.append(value)
    fields["depends"] = dependencies
    fields["provides"] = provides
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


def verify_signature(package, signature, keyring, package_name):
    try:
        completed = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(signature), str(package)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        fail(
            "userspace_signature_invalid",
            f"signature verification failed for package {package_name}",
            packageName=package_name,
            signerFingerprint=None,
        )
    fingerprints = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "VALIDSIG":
            fingerprints.append(fields[2].upper())
    if len(fingerprints) != 1 or not re.fullmatch(r"[0-9A-F]{40}", fingerprints[0]):
        fail(
            "userspace_signature_invalid",
            f"no unique full signer fingerprint for package {package_name}",
            packageName=package_name,
            signerFingerprint=fingerprints[0] if len(fingerprints) == 1 else None,
        )
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
            packageName=package_name,
            signerFingerprint=fingerprint,
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


def validate(args, progress):
    appliance_architecture = os.uname().machine
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        appliance_architecture = os.environ.get(
            "PROJECT_TEST_APPLIANCE_ARCH", appliance_architecture
        )
    if appliance_architecture != "x86_64":
        fail("unsupported_appliance_architecture", "installation validation requires x86_64")
    if len(args.dependency_package) != len(args.dependency_signature):
        fail(
            "dependency_input_invalid",
            "each dependency package requires one positionally paired signature",
        )
    if not re.fullmatch(r"[A-Za-z0-9._+~-]+", args.kernel):
        fail("invalid_target", "target kernel contains unsupported characters")
    root = args.root.resolve(strict=True)
    if root == Path("/"):
        fail("unsafe_target_root", "refusing the appliance root")
    for relative in (
        "etc",
        "etc/os-release",
        "etc/modprobe.d",
        "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
        "etc/mkinitcpio.conf.d",
        "etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf",
        "usr",
        "usr/lib",
        "usr/lib/firmware",
        "usr/lib/modules",
        f"usr/lib/modules/{args.kernel}",
        f"usr/lib/modules/{args.kernel}/updates",
        f"usr/lib/modules/{args.kernel}/updates/open-gpu-kernel-modules-steamos",
        "boot",
        "efi",
        "efi/EFI",
        "efi/EFI/steamos",
        "efi/EFI/steamos/grub.cfg",
        "var",
        "var/lib",
        "var/lib/open-gpu-kernel-modules-steamos-support",
        "var/lib/open-gpu-kernel-modules-steamos-support/offline-install",
        "dev",
        "proc",
        "sys",
    ):
        require_safe_destination(root, relative)
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
    target_var = root / "var"
    if target_var.is_symlink() or not target_var.is_dir():
        fail("target_var_invalid", "target var-A mount is absent or unsafe")
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
        re.match(
            r"^\s*(?:steamenv_boot\s+)?(?:linux|linuxefi|linux16)\s+\S+",
            line,
        )
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
    installed_packages = {}
    local_entries = list(pacman_local.iterdir())
    if len(local_entries) > MAX_PACMAN_RECORDS + 1:
        fail("target_pacman_database_invalid", "target Holo pacman database has too many records")
    record_entries = [entry for entry in local_entries if entry.name != "ALPM_DB_VERSION"]
    progress.emit(
        "holo_database", unit="items", completed=0, total=len(record_entries), force=True
    )
    completed_records = 0
    for entry in local_entries:
        if entry.name == "ALPM_DB_VERSION" and entry.is_file() and not entry.is_symlink():
            continue
        if entry.is_symlink() or not entry.is_dir():
            fail("target_pacman_database_invalid", f"unexpected local database entry: {entry.name}")
        description = entry / "desc"
        try:
            description.resolve(strict=True).relative_to(resolved_database)
        except (FileNotFoundError, RuntimeError, ValueError):
            fail("target_pacman_database_invalid", "target package database escapes its root")
        if description.is_symlink() or not description.is_file():
            fail("target_pacman_database_invalid", f"package record lacks a safe desc file: {entry.name}")
        record = pacman_desc_fields(description)
        name = record["name"]
        if name in installed_packages:
            fail("target_pacman_database_invalid", f"duplicate installed package record: {name}")
        record["source"] = "installed"
        installed_packages[name] = record
        package_descriptions.append(description)
        completed_records += 1
        progress.emit(
            "holo_database", unit="items", completed=completed_records,
            total=len(record_entries),
        )
    progress.emit(
        "holo_database", unit="items", completed=completed_records,
        total=len(record_entries), force=True,
    )
    if not package_descriptions:
        fail("target_pacman_database_empty", "target Holo pacman database has no package records")
    if len(package_descriptions) > MAX_PACMAN_RECORDS:
        fail("target_pacman_database_invalid", "target Holo pacman database has too many records")
    missing_base_packages = sorted(REQUIRED_BASE_PACKAGES - set(installed_packages))
    if missing_base_packages:
        fail(
            "target_pacman_database_invalid",
            "target Holo pacman database lacks base records: " + ", ".join(missing_base_packages),
        )

    for path in (
        args.archive,
        args.checksum,
        args.provenance,
        args.nvidia_utils,
        args.nvidia_utils_signature,
        args.lib32_nvidia_utils,
        args.lib32_nvidia_utils_signature,
        args.package_keyring,
        *args.dependency_package,
        *args.dependency_signature,
    ):
        if not path.is_file() or path.is_symlink():
            fail("input_missing", f"required input is absent: {path}")
    if args.archive.stat().st_size > MAX_MODULE_ARCHIVE_BYTES:
        fail("archive_too_large", "module archive exceeds the compressed-size limit")

    expected = args.checksum.read_text(encoding="utf-8").split()
    if (len(expected) != 2
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected[0])
            or expected[1].lstrip("*") != args.archive.name):
        fail("archive_checksum_invalid", "checksum sidecar is invalid")
    archive_sha = sha256(args.archive, progress)
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

    progress.emit("archive_layout", indeterminate=True, force=True)
    with tempfile.TemporaryDirectory(prefix="offline-root-modules-") as temporary:
        temporary = Path(temporary)
        with tarfile.open(args.archive, "r:gz") as archive:
            members = archive.getmembers()
            if not all(safe_member(member.name) for member in members):
                fail("archive_path_unsafe", "module archive contains an unsafe path")
            normalized_members = {}
            for member in members:
                name = str(PurePosixPath(member.name))
                canonical_spelling = {name, f"{name}/"} if member.isdir() else {name}
                if member.name not in canonical_spelling:
                    fail("archive_layout_invalid", "module archive contains a noncanonical entry")
                if name in normalized_members:
                    fail("archive_layout_invalid", "module archive contains a duplicate entry")
                normalized_members[name] = member
            allowed_names = {
                "modules",
                "BUILD-INFO.txt",
                "PROVENANCE.json",
                *(f"modules/{name}" for name in EXPECTED_MODULES),
                *(f"modules/{name}.zst" for name in EXPECTED_MODULES),
            }
            if any(
                name not in allowed_names
                or (not member.isfile() and not member.isdir())
                or (member.isdir() and name != "modules")
                for name, member in normalized_members.items()
            ):
                fail("archive_layout_invalid", "module archive contains an unexpected entry")
            if "modules" not in normalized_members or not normalized_members["modules"].isdir():
                fail("archive_layout_invalid", "module archive lacks its canonical directory")
            if any(
                name not in normalized_members or not normalized_members[name].isfile()
                for name in ("BUILD-INFO.txt", "PROVENANCE.json")
            ):
                fail("archive_layout_invalid", "module archive lacks canonical metadata")
            total_member_bytes = sum(member.size for member in members)
            if total_member_bytes > MAX_TOTAL_MEMBER_BYTES:
                fail("archive_too_large", "module archive exceeds the decompressed-size limit")
            for name, member in normalized_members.items():
                if not member.isfile():
                    continue
                limit = (
                    MAX_METADATA_MEMBER_BYTES
                    if name in ("BUILD-INFO.txt", "PROVENANCE.json")
                    else MAX_MODULE_MEMBER_BYTES
                )
                if member.size > limit:
                    fail("archive_member_too_large", f"oversized archive member: {name}")
            embedded = archive.extractfile(normalized_members["PROVENANCE.json"])
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
        module_installed_bytes = 0
        progress.emit("modules", unit="items", completed=0, total=len(modules), force=True)
        for module_index, module in enumerate(modules, 1):
            version = module_metadata(module, "version")
            vermagic = module_metadata(module, "vermagic")
            if version != nvidia or vermagic.split(maxsplit=1)[0] != args.kernel:
                fail("module_metadata_mismatch", f"{module.name} does not match target")
            records.append((module.name.removesuffix(".zst"), sha256(module, progress)))
            module_installed_bytes += compressed_module_bytes(module)
            progress.emit(
                "modules", unit="items", completed=module_index, total=len(modules)
            )
        progress.emit(
            "modules", unit="items", completed=len(modules), total=len(modules), force=True
        )
        expected_modules = {
            item.get("name"): item.get("sha256", "").lower()
            for item in provenance.get("modules", [])
        }
        if dict(records) != expected_modules:
            fail("module_hash_mismatch", "module hashes do not match provenance")

    package_records = []
    incoming_packages = {}
    package_installed_bytes = 0
    package_compressed_bytes = 0
    package_inputs = [
        (args.nvidia_utils, args.nvidia_utils_signature, "nvidia-utils", True),
        (args.lib32_nvidia_utils, args.lib32_nvidia_utils_signature, "lib32-nvidia-utils", True),
    ] + [
        (package, signature, None, False)
        for package, signature in zip(args.dependency_package, args.dependency_signature)
    ]
    progress.emit(
        "userspace_packages", unit="items", completed=0,
        total=len(package_inputs), force=True,
    )
    for package_index, (package, signature, expected_name, is_nvidia_package) in enumerate(package_inputs, 1):
        metadata = package_metadata(package)
        package_name = metadata.get("pkgname", "")
        if (expected_name is not None and package_name != expected_name):
            fail("userspace_package_mismatch", f"{package.name} has wrong identity")
        if not re.fullmatch(r"[A-Za-z0-9@._+:-]+", package_name) or metadata.get("arch") not in ("x86_64", "any"):
            fail("userspace_package_mismatch", f"{package.name} has wrong identity or architecture")
        if package_name in incoming_packages:
            fail("dependency_input_invalid", f"duplicate incoming package identity: {package_name}")
        pkgver = metadata.get("pkgver", "")
        if is_nvidia_package and pkgver.rsplit("-", 1)[0] != nvidia:
            fail("userspace_version_mismatch", f"{expected_name} does not match {nvidia}")
        members = package_members(package)
        for member in members:
            require_safe_destination(root, member)
        validate_package_links(package)
        signer = verify_signature(
            package, signature, args.package_keyring, package_name
        )
        require_reviewed_signer(signer, package_name)
        pkgver_only, separator, pkgrel = pkgver.rpartition("-")
        if not separator or not pkgver_only or not pkgrel:
            fail("userspace_package_invalid", f"{package_name} has invalid pkgver/pkgrel")
        if is_nvidia_package and pkgver_only != nvidia:
            fail("userspace_version_mismatch", f"{expected_name} has invalid pkgver/pkgrel")
        package_records.append(
            (package_name, pkgver, pkgver_only, pkgrel, signer, sha256(package, progress), not is_nvidia_package)
        )
        installed_size = metadata.get("size", "")
        if not installed_size.isdigit():
            fail(
                "userspace_package_invalid",
                f"{package_name} lacks a valid declared installed size",
            )
        installed_size = int(installed_size)
        package_installed_bytes += installed_size
        package_compressed_bytes += package.stat().st_size
        incoming_packages[package_name] = {
            "name": package_name,
            "version": pkgver,
            "installedSize": installed_size,
            "depends": metadata["depends"],
            "provides": metadata["provides"],
            "source": "incoming",
        }
        if package_name == "nvidia-utils" and not any(
            re.fullmatch(
                rf"usr/lib/firmware/nvidia/{re.escape(nvidia)}/gsp[^/]*\.bin",
                name,
            )
            for name in members
        ):
            fail("gsp_firmware_missing", "nvidia-utils lacks exact-version GSP firmware")
        progress.emit(
            "userspace_packages", unit="items", completed=package_index,
            total=len(package_inputs),
        )
    progress.emit(
        "userspace_packages", unit="items", completed=len(package_inputs),
        total=len(package_inputs), force=True,
    )

    progress.emit("dependency_closure", indeterminate=True, force=True)
    closure = dependency_closure(incoming_packages, installed_packages)
    replaced_package_bytes = 0
    for name in incoming_packages:
        replaced = installed_packages.get(name)
        if replaced is None:
            continue
        if not replaced["installedSizeValid"]:
            fail(
                "target_pacman_database_invalid",
                f"replacement package record {replaced['packageRecord']}/desc has "
                f"{replaced['installedSizeIssue']} ISIZE",
                packageRecord=replaced["packageRecord"],
                invalidFields=["ISIZE"],
            )
        replaced_package_bytes += replaced["installedSize"]
    existing_module_bytes = tree_regular_bytes(
        root / "usr/lib/modules" / args.kernel / "updates/open-gpu-kernel-modules-steamos"
    )
    initramfs_images = [
        path for path in (root / "boot").glob("initramfs*.img")
        if path.is_file() and not path.is_symlink()
    ]
    initramfs_reserve_bytes = (
        INITRAMFS_BASE_RESERVE_BYTES
        + sum(path.stat().st_size for path in initramfs_images)
        + module_installed_bytes * max(1, len(initramfs_images))
    )
    root_required_bytes = (
        max(0, package_installed_bytes - replaced_package_bytes)
        + max(0, module_installed_bytes - existing_module_bytes)
        + initramfs_reserve_bytes
        + ROOT_METADATA_RESERVE_BYTES
    )
    progress.emit("storage_calculation", indeterminate=True, force=True)
    storage = {
        "rootAvailableBytes": available_bytes(root, "ROOT"),
        "rootRequiredBytes": root_required_bytes,
        "varAvailableBytes": available_bytes(root / "var", "VAR"),
        "varRequiredBytes": VAR_RESERVE_BYTES,
        "efiAvailableBytes": available_bytes(root / "efi", "EFI"),
        "efiRequiredBytes": EFI_RESERVE_BYTES + grub_configuration.stat().st_size,
        "packageInstalledBytes": package_installed_bytes,
        "packageCompressedBytes": package_compressed_bytes,
        "packageReplacedBytes": replaced_package_bytes,
        "moduleInstalledBytes": module_installed_bytes,
        "moduleReplacedBytes": existing_module_bytes,
        "initramfsReserveBytes": initramfs_reserve_bytes,
    }
    compression = compression_context(root)
    compression.update({
        "declaredPackageBytes": package_installed_bytes,
        "packageArchiveBytes": package_compressed_bytes,
        "packageArchiveSavingsBytes": max(
            0, package_installed_bytes - package_compressed_bytes
        ),
        "declaredSizesLikelyConservative": (
            compression["enabled"]
            and package_compressed_bytes < package_installed_bytes
        ),
        "assessment": "informational-package-archive-proxy-not-admission-credit",
    })
    insufficient = [
        name
        for name, available, required in (
            ("root", storage["rootAvailableBytes"], storage["rootRequiredBytes"]),
            ("var", storage["varAvailableBytes"], storage["varRequiredBytes"]),
            ("efi", storage["efiAvailableBytes"], storage["efiRequiredBytes"]),
        )
        if required > available
    ]
    if insufficient:
        fail(
            "target_space_insufficient",
            "insufficient conservative free space on: " + ", ".join(insufficient),
            storage=storage,
            packageDependencyClosure=closure,
            compression=compression,
        )

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
        "storage": storage,
        "packageDependencyClosure": closure,
        "compression": compression,
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
            "sha256": sha256(args.package_keyring, progress),
        },
        "packages": [
            {
                "name": name,
                "fullVersion": full_version,
                "pkgver": pkgver,
                "pkgrel": pkgrel,
                "signer": signer,
                "sha256": digest,
                "role": "dependency" if dependency else "nvidia-userspace",
            }
            for name, full_version, pkgver, pkgrel, signer, digest, dependency in package_records
        ],
    }


def main():
    args = arguments()
    progress = ProgressReporter(args.progress_attempt)
    try:
        document = validate(args, progress)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError) as error:
        if isinstance(error, ValidationFailure):
            reason, message = error.reason, error.message
            details = error.details
        else:
            text = str(error)
            reason, separator, message = text.partition(": ")
            if not separator:
                reason, message = "validation_failed", text
            details = {}
        document = {
            "schemaVersion": 1,
            "status": "failed",
            "reason": reason,
            "message": message,
            **details,
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
