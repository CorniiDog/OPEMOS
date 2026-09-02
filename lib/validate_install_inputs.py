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
from gaming_payload_profiles import ProfileError, validate_profile
from repack_gaming_userspace import RepackError, materialize as materialize_gaming_payload
from atomic_output import atomic_write_bytes

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
MAX_MODULE_ARCHIVE_MEMBERS = 13
MAX_MODULE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_METADATA_MEMBER_BYTES = 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_PROVENANCE_BYTES = 1024 * 1024
MAX_USERSPACE_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_KEYRING_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_LISTING_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 250_000
MAX_PACKAGE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACKAGE_EXPANDED_BYTES = 16 * 1024 * 1024 * 1024
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
MAX_USERSPACE_PACKAGES = 64
MAX_USERSPACE_LOCK_BYTES = 1024 * 1024
MAX_PACKAGE_RELATIONS = 64
MAX_INSTALLED_PACKAGE_RELATIONS = 1024
MAX_REVIEWED_SIGNERS = 256
COMPRESSION_PROFILE = "btrfs-zstd3"
COMPRESSION_WRITE_POLICY = "compress-force=zstd:3"
MAX_DIAGNOSTIC_VALUE_CHARS = 256
MAX_MEASUREMENT_STDERR_CHARS = 512
MEASUREMENT_PHASES = {
    "dependency_check", "image_create", "filesystem_create", "mount",
    "baseline_usage", "package_extraction", "package_usage",
    "module_extraction", "module_compression", "final_usage", "cleanup",
    "launcher",
}
MEASUREMENT_COMMANDS = {
    None, "btrfs", "findmnt", "mkfs.btrfs", "mount", "umount", "zstd",
    "image-create", "btrfs-filesystem-usage", "package-archive",
    "module-archive", "zstd-compress", "zstd-decompress", "measurement-helper",
}
LOCK_PACKAGE_FIELDS = (
    "filename",
    "signatureFilename",
    "version",
    "architecture",
    "packageSha256",
    "signatureSha256",
    "signerFingerprint",
    "installedSize",
    "dependencies",
    "provides",
)


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
    parser.add_argument("--userspace-lock", required=True, type=Path)
    parser.add_argument("--gaming-payload-profile", type=Path)
    parser.add_argument("--gaming-payload-output-dir", type=Path)
    parser.add_argument("--compression-profile", choices=(COMPRESSION_PROFILE,))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--progress-attempt", type=bounded_progress_attempt, default=0)
    parser.add_argument("--input-source", choices=("direct", "authenticated-bundle"), default="direct")
    parser.add_argument("--input-bundle-id", default="")
    return parser.parse_args()


def fail(reason, message, **details):
    raise ValidationFailure(reason, message, **details)


def sanitized_measurement_stderr(value):
    if not value:
        return None
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s/:]+/)*[^\s:]*", "<path>", value)
    value = re.sub(
        r"(?i)\b(token|password|secret|authorization|credential)\s*[:=]\s*\S+",
        r"\1=<redacted>", value,
    )
    value = " ".join(value.replace("\x00", " ").split())
    value = "".join(character if 32 <= ord(character) < 127 else "?" for character in value)
    return value[:MAX_MEASUREMENT_STDERR_CHARS] or None


def safe_lock_string(value, pattern=r"[A-Za-z0-9@._+<>=:-]+"):
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_DIAGNOSTIC_VALUE_CHARS
        and re.fullmatch(pattern, value) is not None
    )


def normalized_relations(
    values, source, reason="userspace_lock_invalid", maximum=MAX_PACKAGE_RELATIONS
):
    if (not isinstance(values, list) or len(values) > maximum
            or any(not safe_lock_string(value) for value in values)):
        fail(reason, f"{source} has invalid dependency/provides metadata")
    return sorted(set(values))


def normalized_lock_package(record, source):
    expected_keys = {"name", *LOCK_PACKAGE_FIELDS}
    if not isinstance(record, dict) or set(record) != expected_keys:
        fail("userspace_lock_invalid", f"{source} has an invalid package record")
    name = record.get("name")
    if not safe_lock_string(name):
        fail("userspace_lock_invalid", f"{source} has an invalid package identity")
    for field in ("filename", "signatureFilename"):
        value = record.get(field)
        if (not safe_lock_string(value, r"[A-Za-z0-9@._+:-]+")
                or Path(value).name != value or value in (".", "..")):
            fail("userspace_lock_invalid", f"{source} has an invalid {field}")
    for field in ("version", "architecture"):
        if not safe_lock_string(record.get(field)):
            fail("userspace_lock_invalid", f"{source} has an invalid {field}")
    for field in ("packageSha256", "signatureSha256"):
        if not isinstance(record.get(field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", record[field]):
            fail("userspace_lock_invalid", f"{source} has an invalid {field}")
    if not isinstance(record.get("signerFingerprint"), str) or not re.fullmatch(
            r"[0-9A-F]{40}|[0-9A-F]{64}", record["signerFingerprint"]):
        fail("userspace_lock_invalid", f"{source} has an invalid signerFingerprint")
    if (not isinstance(record.get("installedSize"), int)
            or isinstance(record["installedSize"], bool)
            or not 0 <= record["installedSize"] <= MAX_PACKAGE_EXPANDED_BYTES):
        fail("userspace_lock_invalid", f"{source} has an invalid installedSize")
    normalized = dict(record)
    normalized["dependencies"] = normalized_relations(
        record["dependencies"], f"{source} dependencies"
    )
    normalized["provides"] = normalized_relations(
        record["provides"], f"{source} provides"
    )
    return normalized


def compare_userspace_lock_packages(expected_records, actual_records):
    if len(expected_records) > MAX_USERSPACE_PACKAGES:
        fail("userspace_lock_invalid", "reviewed lock exceeds the package-count limit")
    if len(actual_records) > MAX_USERSPACE_PACKAGES:
        fail("userspace_package_limit_exceeded", "incoming package set exceeds the package-count limit")
    expected_by_name = {}
    for index, record in enumerate(expected_records):
        normalized = normalized_lock_package(record, f"reviewed package record {index}")
        if normalized["name"] in expected_by_name:
            fail("userspace_lock_invalid", "reviewed lock contains duplicate package identities")
        expected_by_name[normalized["name"]] = normalized
    actual_by_name = {}
    for index, record in enumerate(actual_records):
        normalized = normalized_lock_package(record, f"incoming package record {index}")
        actual_by_name.setdefault(normalized["name"], []).append(normalized)

    expected_names = set(expected_by_name)
    actual_names = set(actual_by_name)
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names)
    duplicates = sorted(
        name for name, records in actual_by_name.items() if len(records) > 1
    )
    mismatches = []
    for name in sorted(expected_names & actual_names):
        expected = expected_by_name[name]
        actual_values = {}
        invalid_fields = []
        for field in LOCK_PACKAGE_FIELDS:
            values = {
                json.dumps(actual[field], sort_keys=True, separators=(",", ":")):
                actual[field]
                for actual in actual_by_name[name]
            }
            if len(values) != 1 or next(iter(values.values())) != expected[field]:
                invalid_fields.append(field)
                ordered = [values[key] for key in sorted(values)]
                actual_values[field] = ordered[0] if len(ordered) == 1 else ordered
        if invalid_fields:
            mismatches.append({
                "packageName": name,
                "invalidFields": invalid_fields,
                "expected": {field: expected[field] for field in invalid_fields},
                "actual": {field: actual_values[field] for field in invalid_fields},
            })
    if missing or unexpected or duplicates or mismatches:
        def quantity(count, singular, plural=None):
            return f"{count} {singular if count == 1 else (plural or singular + 's')}"

        message = (
            "Userspace lock validation failed: "
            f"{quantity(len(missing), 'missing package')}, "
            f"{quantity(len(unexpected), 'unexpected package')}, "
            f"{quantity(len(duplicates), 'duplicate package identity', 'duplicate package identities')}, "
            f"and {quantity(len(mismatches), 'package')} with mismatched metadata. "
            "No mutation began."
        )
        fail(
            "userspace_lock_mismatch",
            message,
            missingPackages=missing,
            unexpectedPackages=unexpected,
            duplicatePackages=duplicates,
            packageMismatches=mismatches,
        )


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AggregateInputHasher:
    """Hash immutable installer inputs as one monotonic progress operation."""

    def __init__(self, paths, progress):
        try:
            self.inputs = [(path, path.stat().st_size) for path in paths]
        except OSError:
            fail("input_changed", "an authenticated input changed before hashing")
        self.progress = progress
        self.digests = {}
        self.total = sum(size for _, size in self.inputs)
        self.completed = 0

    def hash_all(self):
        # A valid installation always has non-empty package and module inputs,
        # so the schema's required positive total is guaranteed here.
        if self.total <= 0:
            fail("input_missing", "authenticated installer inputs are empty")
        self.progress.emit(
            "hashing", unit="bytes", completed=0, total=self.total, force=True
        )
        for path, expected_size in self.inputs:
            digest = hashlib.sha256()
            consumed = 0
            try:
                with path.open("rb") as stream:
                    while consumed < expected_size:
                        chunk = stream.read(min(1024 * 1024, expected_size - consumed))
                        if not chunk:
                            fail(
                                "input_changed",
                                "an authenticated input changed while hashing",
                            )
                        digest.update(chunk)
                        consumed += len(chunk)
                        self.completed += len(chunk)
                        self.progress.emit(
                            "hashing", unit="bytes", completed=self.completed,
                            total=self.total,
                        )
                    if stream.read(1):
                        fail(
                            "input_changed",
                            "an authenticated input changed while hashing",
                        )
                current_size = path.stat().st_size
            except OSError:
                fail("input_changed", "an authenticated input changed while hashing")
            if current_size != expected_size:
                fail("input_changed", "an authenticated input changed while hashing")
            self.digests[path] = digest.hexdigest()
        if self.completed != self.total:
            fail("input_changed", "authenticated input sizes changed while hashing")
        self.progress.emit(
            "hashing", unit="bytes", completed=self.completed,
            total=self.total, force=True,
        )

    def digest(self, path):
        try:
            return self.digests[path]
        except KeyError:
            fail("validation_internal_error", "an input was absent from the hashing plan")


def safe_member(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def require_regular_input(path, label, maximum):
    try:
        if path.is_symlink() or not path.is_file():
            fail("input_missing", f"required {label} input is absent or unsafe")
        size = path.stat().st_size
    except OSError:
        fail("input_missing", f"required {label} input is absent or unsafe")
    if size > maximum:
        fail("input_too_large", f"required {label} input exceeds its size limit")
    return size


def require_safe_destination(root, relative):
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        fail("target_path_unsafe", "a target destination is unsafe")
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
            fail("target_path_unsafe", "a target destination traverses a non-directory")
        if stat.S_ISLNK(mode):
            fail("target_path_unsafe", "a target destination traverses a symlink")
        try:
            current.resolve(strict=True).relative_to(root)
        except (FileNotFoundError, RuntimeError, ValueError):
            fail("target_path_unsafe", "a target destination escapes the root")


def require_root_filesystem_destination(root, relative):
    """Require the deepest existing ancestor to remain on the root filesystem."""
    require_safe_destination(root, relative)
    root_device = root.stat().st_dev
    candidate = root / PurePosixPath(relative)
    while not candidate.exists():
        if candidate == root:
            break
        candidate = candidate.parent
    try:
        if candidate.stat().st_dev != root_device:
            fail(
                "compression_target_mount_mismatch",
                "a userspace payload destination is outside the compressed root filesystem",
            )
    except OSError:
        fail(
            "compression_target_mount_mismatch",
            "a userspace payload destination mount identity cannot be verified",
        )
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        if os.environ.get("PROJECT_TEST_COMPRESSION_DESTINATION_INELIGIBLE") == "1":
            fail(
                "compression_target_ineligible",
                "a payload destination disables Btrfs compression",
            )
        return
    try:
        completed = subprocess.run(
            ["lsattr", "-d", "--", str(candidate)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        attributes = completed.stdout.split(maxsplit=1)[0]
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
        fail(
            "compression_target_ineligible",
            "a payload destination's Btrfs attributes cannot be verified",
        )
    if "C" in attributes or "m" in attributes:
        fail(
            "compression_target_ineligible",
            "a payload destination disables Btrfs compression",
        )


def pacman_desc_fields(path):
    record_name = path.parent.name
    if not safe_lock_string(record_name):
        fail("target_pacman_database_invalid", "package record has an unsafe identity")
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
        len(installed_size) == 1
        and installed_size[0].isdigit()
        and len(installed_size[0]) <= 20
        and int(installed_size[0]) <= MAX_PACKAGE_EXPANDED_BYTES
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
        "depends": normalized_relations(
            fields.get("DEPENDS", []), f"package record {record_name} dependencies",
            "target_pacman_database_invalid", MAX_INSTALLED_PACKAGE_RELATIONS,
        ),
        "provides": normalized_relations(
            fields.get("PROVIDES", []), f"package record {record_name} provides",
            "target_pacman_database_invalid", MAX_INSTALLED_PACKAGE_RELATIONS,
        ),
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
    except (OSError, subprocess.CalledProcessError, ValueError):
        fail("package_dependency_invalid", "cannot compare dependency versions")
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
                fail("target_path_unsafe", "replacement module tree contains a symlink")
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
    except (OSError, subprocess.CalledProcessError):
        fail("module_size_unavailable", "cannot estimate compressed module size")


def module_payload_sha256(path):
    digest = hashlib.sha256()
    process = None
    try:
        if path.name.endswith(".zst"):
            process = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stream = process.stdout
        else:
            stream = path.open("rb")
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if process is not None and process.wait() != 0:
            raise OSError
        return digest.hexdigest()
    except OSError:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        fail("module_size_unavailable", "cannot inspect an existing module payload")


def existing_module_set_is_exact(root, kernel, expected_payload_hashes):
    destination = (
        root / "usr/lib/modules" / kernel
        / "updates/open-gpu-kernel-modules-steamos"
    )
    if not destination.exists():
        return False
    try:
        entries = list(destination.iterdir())
    except OSError:
        fail("target_path_unsafe", "existing module destination is unreadable")
    if any(path.is_symlink() or not path.is_file() for path in entries):
        fail("target_path_unsafe", "existing module destination has an unsafe entry")
    normalized = {path.name.removesuffix(".zst"): path for path in entries}
    if set(normalized) != EXPECTED_MODULES or len(entries) != len(EXPECTED_MODULES):
        return False
    return all(
        module_payload_sha256(normalized[name]) == expected_payload_hashes[name]
        for name in EXPECTED_MODULES
    )


def require_installed_package_integrity(root, package_name, deadline):
    database = root / "usr/lib/holo/pacmandb"
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C", "SYSTEMD_OFFLINE": "1"})
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail(
            "existing_package_integrity_unverified",
            "installed package integrity checks exceeded their time limit",
        )
    try:
        completed = subprocess.run(
            [
                "pacman", "--root", str(root), "--dbpath", str(database),
                "-Qkk", package_name,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=min(120, remaining),
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail(
            "existing_package_integrity_unverified",
            "an exact-version installed package could not be integrity checked",
            packageName=package_name,
        )
    if completed.returncode != 0:
        fail(
            "existing_package_integrity_unverified",
            "an exact-version installed package failed its integrity check",
            packageName=package_name,
        )


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
    compression_options = [
        option for option in options
        if re.fullmatch(
            r"compress(?:-force)?(?:=(?:no|zlib|lzo|zstd)(?::[0-9]+)?)?",
            option,
        )
    ]
    invalid_compression_options = sorted(
        option for option in options
        if option.startswith("compress") and option not in compression_options
    )
    incompatible_options = sorted(
        option for option in options if option in ("nodatacow", "nodatasum")
    )
    return {
        "filesystem": filesystem,
        "enabled": (
            any(option != "compress=no" for option in compression_options)
            if filesystem == "btrfs" else False
        ),
        "options": compression_options,
        "invalidOptions": invalid_compression_options,
        "writeIncompatibleOptions": incompatible_options,
        "admissionBasis": "logical-uncompressed-conservative",
        "compressionSavingsCreditedBytes": 0,
        "pacmanCheckSpaceBypassAuthorized": False,
        "pacmanCheckSpacePolicy": "preserve",
    }


def require_exclusive_btrfs_mount(root):
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        if os.environ.get("PROJECT_TEST_BTRFS_SHARED_MOUNT") == "1":
            fail(
                "compression_mount_not_exclusive",
                "the target Btrfs filesystem is mounted more than once",
            )
        return
    try:
        target_device = subprocess.run(
            ["findmnt", "-rn", "-T", str(root), "-o", "MAJ:MIN"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        ).stdout.strip()
        mounted_devices = subprocess.run(
            ["findmnt", "-rn", "-o", "MAJ:MIN"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        fail(
            "compression_mount_identity_unavailable",
            "the target Btrfs mount identity cannot be verified",
        )
    if (re.fullmatch(r"[0-9]+:[0-9]+", target_device) is None
            or sum(device.strip() == target_device for device in mounted_devices) != 1):
        fail(
            "compression_mount_not_exclusive",
            "the target Btrfs filesystem must have exactly one appliance mount",
        )


def measured_btrfs_payload(args, package_paths, declared_payload_bytes):
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        if os.environ.get("PROJECT_TEST_BTRFS_MEASUREMENT_FAIL") == "1":
            fail(
                "compression_measurement_mkfs_failed",
                "Scratch Btrfs filesystem creation failed.",
                measurementFailure={
                    "phase": "filesystem_create", "command": "mkfs.btrfs",
                    "exitStatus": 1, "stderr": "synthetic measurement failure",
                },
            )
        try:
            payload = int(os.environ["PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES"])
            data = int(os.environ.get("PROJECT_TEST_BTRFS_DATA_ALLOCATED_BYTES", payload))
            metadata = int(os.environ.get("PROJECT_TEST_BTRFS_METADATA_ALLOCATED_BYTES", "0"))
            system = int(os.environ.get("PROJECT_TEST_BTRFS_SYSTEM_ALLOCATED_BYTES", "0"))
        except (KeyError, ValueError):
            fail(
                "compression_measurement_failed",
                "scratch-Btrfs test measurement is incomplete",
            )
        if (payload <= 0 or data <= 0 or data > payload
                or metadata < 0 or system < 0):
            fail(
                "compression_measurement_invalid",
                "scratch-Btrfs test measurement is invalid",
            )
        return {
            "schemaVersion": 1,
            "status": "measured",
            "profile": COMPRESSION_PROFILE,
            "writePolicy": COMPRESSION_WRITE_POLICY,
            "measurementMethod": "scratch-btrfs-filesystem-usage-used-delta",
            "declaredPayloadBytes": declared_payload_bytes,
            "scratchFilesystemBytes": max(2 * 1024**3, declared_payload_bytes + 1024**3),
            "payloadAllocatedBytes": payload,
            "dataAllocatedBytes": data,
            "metadataAllocatedBytes": metadata,
            "systemAllocatedBytes": system,
            "filesystemOverheadBytes": max(0, payload - data),
            "packageMeasurements": [
                {"filename": path.name, "allocatedBytes": 0}
                for path in package_paths
            ],
            "moduleAllocatedBytes": payload,
        }
    if os.geteuid() != 0:
        fail(
            "compression_measurement_privilege_required",
            "scratch-Btrfs payload measurement requires the managed root appliance",
        )
    with tempfile.TemporaryDirectory(prefix="offline-root-btrfs-result-") as temporary:
        output = Path(temporary) / "measurement.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve().with_name("measure_btrfs_payload.py")),
            "--module-archive", str(args.archive),
            "--declared-payload-bytes", str(declared_payload_bytes),
            "--output", str(output),
        ]
        for package in package_paths:
            command.extend(("--package", str(package)))
        completed = None
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if output.is_symlink() or output.stat().st_size > MAX_METADATA_MEMBER_BYTES:
                raise OSError
            measurement = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            stderr = None
            status = None
            if completed is not None:
                status = completed.returncode
                stderr = completed.stderr.decode("utf-8", errors="replace")
            elif isinstance(error, OSError):
                stderr = str(error)
            stderr = sanitized_measurement_stderr(stderr)
            fail(
                "compression_measurement_launcher_failed",
                "Scratch-Btrfs measurement did not return structured metadata.",
                measurementFailure={
                    "phase": "launcher", "command": "measurement-helper",
                    "exitStatus": status, "stderr": stderr,
                },
            )
    if isinstance(measurement, dict) and measurement.get("status") == "failed":
        detail = measurement.get("measurementFailure")
        if (set(measurement) != {"schemaVersion", "status", "reason", "message",
                                "measurementFailure"}
                or measurement.get("schemaVersion") != 1
                or not safe_lock_string(measurement.get("reason"), r"[a-z][a-z0-9_]{0,63}")
                or not isinstance(measurement.get("message"), str)
                or not 0 < len(measurement["message"]) <= 512
                or not isinstance(detail, dict)
                or set(detail) != {"phase", "command", "exitStatus", "stderr"}
                or detail.get("phase") not in MEASUREMENT_PHASES
                or detail.get("command") not in MEASUREMENT_COMMANDS
                or (detail.get("exitStatus") is not None
                    and (not isinstance(detail["exitStatus"], int)
                         or isinstance(detail["exitStatus"], bool)
                         or not -255 <= detail["exitStatus"] <= 255))
                or (detail.get("stderr") is not None
                    and (not isinstance(detail["stderr"], str)
                         or len(detail["stderr"]) > MAX_MEASUREMENT_STDERR_CHARS
                         or any(not 32 <= ord(character) < 127
                                for character in detail["stderr"])))):
            fail(
                "compression_measurement_invalid",
                "scratch-Btrfs failure metadata is malformed",
            )
        fail(measurement["reason"], measurement["message"], measurementFailure=detail)
    expected = {
        "schemaVersion", "status", "profile", "writePolicy", "measurementMethod",
        "declaredPayloadBytes", "scratchFilesystemBytes", "payloadAllocatedBytes",
        "packageMeasurements", "moduleAllocatedBytes",
        "dataAllocatedBytes", "metadataAllocatedBytes", "systemAllocatedBytes",
        "filesystemOverheadBytes",
    }
    numeric = expected - {
        "status", "profile", "writePolicy", "measurementMethod",
        "packageMeasurements",
    }
    package_measurements = measurement.get("packageMeasurements")
    measured_package_names = [path.name for path in package_paths]
    if (not isinstance(measurement, dict) or set(measurement) != expected
            or measurement.get("schemaVersion") != 1
            or measurement.get("status") != "measured"
            or measurement.get("profile") != COMPRESSION_PROFILE
            or measurement.get("writePolicy") != COMPRESSION_WRITE_POLICY
            or measurement.get("measurementMethod")
            != "scratch-btrfs-filesystem-usage-used-delta"
            or any(not isinstance(measurement.get(field), int)
                   or isinstance(measurement.get(field), bool)
                   or measurement[field] < 0 for field in numeric)
            or measurement["declaredPayloadBytes"] != declared_payload_bytes
            or measurement["payloadAllocatedBytes"] <= 0
            or not isinstance(package_measurements, list)
            or len(package_measurements) != len(measured_package_names)
            or any(
                not isinstance(item, dict)
                or set(item) != {"filename", "allocatedBytes"}
                or item.get("filename") != expected_name
                or not isinstance(item.get("allocatedBytes"), int)
                or isinstance(item.get("allocatedBytes"), bool)
                or item["allocatedBytes"] < 0
                for item, expected_name in zip(
                    package_measurements, measured_package_names
                )
            )
            or sum(item["allocatedBytes"] for item in package_measurements)
            + measurement["moduleAllocatedBytes"]
            > measurement["payloadAllocatedBytes"]
            or measurement["dataAllocatedBytes"] <= 0
            or measurement["dataAllocatedBytes"] > measurement["payloadAllocatedBytes"]):
        fail(
            "compression_measurement_invalid",
            "scratch-Btrfs payload measurement returned invalid metadata",
        )
    return measurement


def bounded_command_text(arguments, maximum, reason, message):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        fail(reason, message)
    try:
        output = process.stdout.read(maximum + 1)
        if len(output) > maximum:
            process.kill()
            process.wait()
            fail(reason, message + " Output exceeds the size limit.")
        if process.wait() != 0:
            fail(reason, message)
        return output.decode("utf-8", errors="strict")
    except UnicodeError:
        process.kill()
        process.wait()
        fail(reason, message + " Output is not UTF-8 text.")


def package_metadata(path):
    output = bounded_command_text(
        ["bsdtar", "-xOf", str(path), ".PKGINFO"],
        MAX_METADATA_MEMBER_BYTES,
        "userspace_package_invalid",
        "Cannot read userspace package metadata.",
    )
    fields = {}
    dependencies = []
    provides = []
    for line in output.splitlines():
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
    listing = bounded_command_text(
        ["bsdtar", "-tf", str(path)],
        MAX_PACKAGE_LISTING_BYTES,
        "userspace_package_invalid",
        "Cannot list a userspace package.",
    )
    verbose = bounded_command_text(
        ["bsdtar", "-tvf", str(path)],
        MAX_PACKAGE_LISTING_BYTES,
        "userspace_package_invalid",
        "Cannot inspect userspace package archive metadata.",
    )
    members = listing.splitlines()
    verbose_lines = verbose.splitlines()
    if (not members or len(members) != len(verbose_lines)
            or len(members) > MAX_PACKAGE_MEMBERS):
        fail("userspace_package_invalid", "userspace package has an invalid member listing")
    if not all(safe_member(name) for name in members):
        fail("userspace_package_unsafe", "userspace package contains an unsafe path")
    normalized = [str(PurePosixPath(name)) for name in members]
    if len(set(normalized)) != len(normalized):
        fail("userspace_package_unsafe", "userspace package contains duplicate member paths")
    member_names = set(normalized)
    total_size = 0
    for original_name, name, line in zip(members, normalized, verbose_lines):
        fields = line.split(maxsplit=8)
        if len(fields) != 9 or not fields[4].isdigit():
            fail("userspace_package_invalid", "Cannot parse a userspace package member")
        kind = line[0]
        if kind not in ("-", "d", "l", "h"):
            fail("userspace_package_unsafe", "userspace package contains a special archive entry")
        size = int(fields[4])
        if size > MAX_PACKAGE_MEMBER_BYTES:
            fail("userspace_package_invalid", "userspace package contains an oversized member")
        total_size += size
        if total_size > MAX_PACKAGE_EXPANDED_BYTES:
            fail("userspace_package_invalid", "userspace package exceeds the expansion limit")
        canonical = {name, f"{name}/"} if kind == "d" else {name}
        if original_name not in canonical:
            fail("userspace_package_unsafe", "userspace package contains a noncanonical member path")
        if kind not in ("l", "h"):
            if fields[8] != original_name:
                fail("userspace_package_invalid", "Cannot parse a userspace package member name")
            continue
        relation = " -> " if line[0] == "l" else " link to "
        prefix = original_name + relation
        if not fields[8].startswith(prefix):
            fail("userspace_package_invalid", "Cannot parse a userspace package link target")
        target = fields[8][len(prefix):]
        member = PurePosixPath(name)
        destination = PurePosixPath(target)
        if destination.is_absolute():
            destination = PurePosixPath(*destination.parts[1:])
        elif kind == "l":
            destination = member.parent / destination
        depth = 0
        for component in destination.parts:
            if component in ("", "."):
                continue
            if component == "..":
                depth -= 1
                if depth < 0:
                    fail("userspace_package_unsafe", "userspace package has an escaping link")
            else:
                depth += 1
        confined_target = str(destination)
        if not confined_target or confined_target == ".":
            fail("userspace_package_unsafe", "userspace package has an unsafe link target")
        if kind == "h" and confined_target not in member_names:
            fail("userspace_package_unsafe", "userspace package has a hardlink to an absent member")
    return normalized


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
    except (OSError, json.JSONDecodeError):
        fail("userspace_trust_invalid", "Cannot read package signer policy")
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        fail("userspace_trust_invalid", "unsupported package signer policy schema")
    signers = manifest.get("signers")
    if (not isinstance(signers, list) or len(signers) > MAX_REVIEWED_SIGNERS
            or any(not isinstance(signer, dict) for signer in signers)):
        fail("userspace_trust_invalid", "package signer policy is malformed")
    matches = [
        signer
        for signer in signers
        if signer.get("fingerprint", "").upper() == fingerprint
        and signer.get("status") == "active"
        and isinstance(signer.get("packages"), list)
        and package_name in signer["packages"]
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
    except (OSError, subprocess.CalledProcessError):
        fail("module_metadata_invalid", "Cannot read NVIDIA module metadata")


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
    try:
        if not args.root.is_absolute() or args.root.is_symlink():
            raise OSError
        root = args.root.resolve(strict=True)
    except (OSError, RuntimeError):
        fail("unsafe_target_root", "target root must be an absolute non-symlink path")
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
        "usr/lib/open-gpu-kernel-modules-steamos-support",
        "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install",
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
            fail("target_pacman_database_invalid", "unexpected local package database entry")
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

    require_regular_input(args.archive, "module archive", MAX_MODULE_ARCHIVE_BYTES)
    require_regular_input(args.checksum, "checksum", MAX_CHECKSUM_BYTES)
    require_regular_input(args.provenance, "provenance", MAX_PROVENANCE_BYTES)
    require_regular_input(args.nvidia_utils, "nvidia-utils package", MAX_USERSPACE_PACKAGE_BYTES)
    require_regular_input(
        args.nvidia_utils_signature, "nvidia-utils signature", MAX_SIGNATURE_BYTES
    )
    require_regular_input(
        args.lib32_nvidia_utils, "lib32-nvidia-utils package", MAX_USERSPACE_PACKAGE_BYTES
    )
    require_regular_input(
        args.lib32_nvidia_utils_signature,
        "lib32-nvidia-utils signature",
        MAX_SIGNATURE_BYTES,
    )
    require_regular_input(args.package_keyring, "package keyring", MAX_KEYRING_BYTES)
    require_regular_input(args.userspace_lock, "userspace lock", MAX_USERSPACE_LOCK_BYTES)
    if args.gaming_payload_profile:
        require_regular_input(args.gaming_payload_profile, "gaming payload profile", MAX_USERSPACE_LOCK_BYTES)
        if (args.gaming_payload_output_dir is None
                or args.gaming_payload_output_dir.is_symlink()
                or not args.gaming_payload_output_dir.is_dir()
                or any(args.gaming_payload_output_dir.iterdir())):
            fail(
                "gaming_payload_staging_invalid",
                "gaming payload staging must be an empty private directory",
            )
    elif args.gaming_payload_output_dir is not None:
        fail(
            "gaming_payload_staging_invalid",
            "gaming payload staging requires an exact reviewed profile",
        )
    for package in args.dependency_package:
        require_regular_input(package, "dependency package", MAX_USERSPACE_PACKAGE_BYTES)
    for signature in args.dependency_signature:
        require_regular_input(signature, "dependency signature", MAX_SIGNATURE_BYTES)

    authenticated_inputs = [
        args.archive,
        args.checksum,
        args.provenance,
        args.nvidia_utils,
        args.nvidia_utils_signature,
        args.lib32_nvidia_utils,
        args.lib32_nvidia_utils_signature,
        *args.dependency_package,
        *args.dependency_signature,
        args.package_keyring,
        args.userspace_lock,
    ]
    if args.gaming_payload_profile:
        authenticated_inputs.append(args.gaming_payload_profile)
    input_hashes = AggregateInputHasher(authenticated_inputs, progress)
    input_hashes.hash_all()

    expected = args.checksum.read_text(encoding="utf-8").split()
    if (len(expected) != 2
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected[0])
            or expected[1].lstrip("*") != args.archive.name):
        fail("archive_checksum_invalid", "checksum sidecar is invalid")
    archive_sha = input_hashes.digest(args.archive)
    if archive_sha != expected[0].lower():
        fail("archive_checksum_mismatch", "archive checksum does not match")
    provenance_bytes = args.provenance.read_bytes()
    provenance_sha = input_hashes.digest(args.provenance)
    try:
        provenance = json.loads(provenance_bytes)
    except json.JSONDecodeError:
        fail("provenance_invalid", "provenance is not valid JSON")
    if not isinstance(provenance, dict) or provenance.get("schemaVersion") != 1:
        fail("provenance_invalid", "unsupported provenance schema")
    target = provenance.get("target", {})
    if not isinstance(target, dict):
        fail("provenance_invalid", "provenance target is malformed")
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
            members = []
            for member in archive:
                members.append(member)
                if len(members) > MAX_MODULE_ARCHIVE_MEMBERS:
                    fail("archive_layout_invalid", "module archive has too many entries")
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
        module_payload_hashes = {}
        module_installed_bytes = 0
        progress.emit("modules", unit="items", completed=0, total=len(modules), force=True)
        for module_index, module in enumerate(modules, 1):
            version = module_metadata(module, "version")
            vermagic = module_metadata(module, "vermagic")
            if version != nvidia or vermagic.split(maxsplit=1)[0] != args.kernel:
                fail("module_metadata_mismatch", f"{module.name} does not match target")
            normalized_module_name = module.name.removesuffix(".zst")
            records.append((normalized_module_name, sha256(module)))
            module_payload_hashes[normalized_module_name] = module_payload_sha256(module)
            module_installed_bytes += compressed_module_bytes(module)
            progress.emit(
                "modules", unit="items", completed=module_index, total=len(modules)
            )
        progress.emit(
            "modules", unit="items", completed=len(modules), total=len(modules), force=True
        )
        provenance_modules = provenance.get("modules")
        if not isinstance(provenance_modules, list) or len(provenance_modules) != 5:
            fail("provenance_invalid", "provenance does not describe exactly five modules")
        expected_modules = {}
        for item in provenance_modules:
            if not isinstance(item, dict):
                fail("provenance_invalid", "provenance contains a malformed module record")
            name = item.get("name")
            digest = item.get("sha256")
            if (name not in EXPECTED_MODULES or name in expected_modules
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                fail("provenance_invalid", "provenance module identities or hashes are invalid")
            if (item.get("version") not in (None, nvidia)
                    or item.get("architecture") not in (None, "x86_64")
                    or (item.get("vermagic") is not None
                        and not isinstance(item.get("vermagic"), str))):
                fail("provenance_invalid", "provenance module metadata does not match target")
            expected_modules[name] = digest.lower()
        if dict(records) != expected_modules:
            fail("module_hash_mismatch", "module hashes do not match provenance")

    lock_package_records = []
    parsed_packages = []
    package_installed_bytes = 0
    package_compressed_bytes = 0
    package_inputs = [
        (args.nvidia_utils, args.nvidia_utils_signature, "nvidia-utils", True),
        (args.lib32_nvidia_utils, args.lib32_nvidia_utils_signature, "lib32-nvidia-utils", True),
    ] + [
        (package, signature, None, False)
        for package, signature in zip(args.dependency_package, args.dependency_signature)
    ]
    if len(package_inputs) > MAX_USERSPACE_PACKAGES:
        fail(
            "userspace_package_limit_exceeded",
            "incoming package set exceeds the package-count limit",
        )
    progress.emit(
        "userspace_packages", unit="items", completed=0,
        total=len(package_inputs), force=True,
    )
    for package_index, (package, signature, expected_name, is_nvidia_package) in enumerate(package_inputs, 1):
        metadata = package_metadata(package)
        package_name = metadata.get("pkgname", "")
        architecture = metadata.get("arch", "")
        pkgver = metadata.get("pkgver", "")
        if (not safe_lock_string(package_name)
                or not safe_lock_string(architecture)
                or not safe_lock_string(pkgver)):
            fail("userspace_package_mismatch", "userspace package has unsafe identity metadata")
        dependencies = normalized_relations(
            metadata["depends"], f"incoming package {package_name} dependencies",
            "userspace_package_invalid",
        )
        provides = normalized_relations(
            metadata["provides"], f"incoming package {package_name} provides",
            "userspace_package_invalid",
        )
        members = package_members(package)
        for member in members:
            require_safe_destination(root, member)
        signer = verify_signature(
            package, signature, args.package_keyring, package_name
        )
        installed_size = metadata.get("size", "")
        if (not installed_size.isdigit() or len(installed_size) > 20
                or int(installed_size) > MAX_PACKAGE_EXPANDED_BYTES):
            fail(
                "userspace_package_invalid",
                f"{package_name} lacks a valid declared installed size",
            )
        installed_size = int(installed_size)
        package_digest = input_hashes.digest(package)
        signature_digest = input_hashes.digest(signature)
        package_installed_bytes += installed_size
        package_compressed_bytes += package.stat().st_size
        lock_record = {
            "name": package_name,
            "filename": package.name,
            "signatureFilename": signature.name,
            "version": pkgver,
            "architecture": architecture,
            "packageSha256": package_digest,
            "signatureSha256": signature_digest,
            "signerFingerprint": signer,
            "installedSize": installed_size,
            "dependencies": dependencies,
            "provides": provides,
        }
        lock_package_records.append(lock_record)
        parsed_packages.append({
            "lockRecord": lock_record,
            "expectedName": expected_name,
            "isNvidiaPackage": is_nvidia_package,
            "members": members,
        })
        progress.emit(
            "userspace_packages", unit="items", completed=package_index,
            total=len(package_inputs),
        )
    progress.emit(
        "userspace_packages", unit="items", completed=len(package_inputs),
        total=len(package_inputs), force=True,
    )

    try:
        if args.userspace_lock.stat().st_size > MAX_USERSPACE_LOCK_BYTES:
            fail("userspace_lock_invalid", "userspace lock exceeds the size limit")
        userspace_lock = json.loads(args.userspace_lock.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("userspace_lock_invalid", "cannot read userspace lock")
    if not isinstance(userspace_lock, dict):
        fail("userspace_lock_invalid", "reviewed userspace lock is not an object")
    lock_target = userspace_lock.get("target", {})
    if not isinstance(lock_target, dict):
        fail("userspace_lock_invalid", "reviewed userspace lock target is malformed")
    if (userspace_lock.get("schemaVersion") != 1
            or userspace_lock.get("status") != "reviewed"
            or userspace_lock.get("missingReview") != []
            or lock_target != {
                "steamosVersion": identity["VERSION_ID"],
                "nvidiaVersion": nvidia,
                "architecture": "x86_64",
            }):
        fail("userspace_lock_invalid", "userspace lock is not reviewed for the exact target")
    gaming_payload = None
    if args.gaming_payload_profile:
        try:
            gaming_payload = validate_profile(args.gaming_payload_profile, args.userspace_lock, {
                "steamosVersion": identity["VERSION_ID"], "kernelVersion": args.kernel,
                "nvidiaVersion": nvidia, "architecture": "x86_64",
            })
        except ProfileError as error:
            fail("gaming_payload_profile_invalid", str(error))
    lock_keyring = userspace_lock.get("keyring", {})
    if not isinstance(lock_keyring, dict):
        fail("userspace_lock_invalid", "reviewed userspace lock keyring is malformed")
    if (lock_keyring.get("filename") != args.package_keyring.name
            or lock_keyring.get("sha256") != input_hashes.digest(args.package_keyring)):
        fail("userspace_lock_mismatch", "minimal reviewed keyring does not match userspace lock")
    expected_lock_packages = userspace_lock.get("packages")
    if not isinstance(expected_lock_packages, list):
        fail("userspace_lock_invalid", "reviewed lock package set is not a list")
    compare_userspace_lock_packages(expected_lock_packages, lock_package_records)

    # The gaming profile authenticates exact deterministic derivatives of the
    # already signature-verified NVIDIA seed packages.  Repacking happens only
    # after the complete signed source set matches the normal reviewed lock.
    # Dependencies remain byte-identical.  Pacman therefore receives complete
    # packages with exact ownership records; target files are never deleted by
    # an installer-side filename heuristic.
    if gaming_payload is not None:
        try:
            derived_paths = materialize_gaming_payload(
                args.gaming_payload_profile,
                [args.nvidia_utils, args.lib32_nvidia_utils],
                args.gaming_payload_output_dir,
                progress=lambda completed, total: progress.emit(
                    "gaming_payload_repack", unit="items", completed=completed,
                    total=total, force=True,
                ),
            )
        except RepackError as error:
            fail("gaming_payload_repack_failed", str(error))
        derived_by_name = {}
        profile_records = {
            record["name"]: record for record in gaming_payload["packageRecords"]
        }
        source_by_name = {
            parsed["lockRecord"]["name"]: parsed
            for parsed in parsed_packages
            if parsed["isNvidiaPackage"]
        }
        for derived in derived_paths:
            metadata = package_metadata(derived)
            name = metadata.get("pkgname", "")
            profile_record = profile_records.get(name)
            source = source_by_name.get(name)
            if profile_record is None or source is None or name in derived_by_name:
                fail("gaming_payload_repack_invalid", "derived package identity is invalid")
            members = package_members(derived)
            for member in members:
                require_safe_destination(root, member)
            dependencies = normalized_relations(
                metadata["depends"], f"derived package {name} dependencies",
                "gaming_payload_repack_invalid",
            )
            provides = normalized_relations(
                metadata["provides"], f"derived package {name} provides",
                "gaming_payload_repack_invalid",
            )
            installed_size = metadata.get("size", "")
            source_record = source["lockRecord"]
            derived_digest = sha256(derived)
            if (not installed_size.isdigit()
                    or derived.name != profile_record["filename"]
                    or derived_digest != profile_record["sha256"]
                    or metadata.get("pkgver") != profile_record["version"]
                    or metadata.get("arch") != source_record["architecture"]
                    or int(installed_size) != profile_record["installedSize"]
                    or dependencies != source_record["dependencies"]
                    or provides != source_record["provides"]):
                fail(
                    "gaming_payload_repack_invalid",
                    f"derived package metadata differs from reviewed profile: {name}",
                )
            derived_record = dict(source_record)
            derived_record.update({
                "filename": derived.name,
                "version": metadata["pkgver"],
                "packageSha256": derived_digest,
                "installedSize": int(installed_size),
            })
            derived_by_name[name] = {
                "lockRecord": derived_record,
                "expectedName": name,
                "isNvidiaPackage": True,
                "members": members,
                "package": derived,
            }
        if set(derived_by_name) != {"nvidia-utils", "lib32-nvidia-utils"}:
            fail("gaming_payload_repack_invalid", "derived package set is incomplete")
        dependency_parsed = [
            parsed for parsed in parsed_packages if not parsed["isNvidiaPackage"]
        ]
        parsed_packages = [
            derived_by_name["nvidia-utils"], derived_by_name["lib32-nvidia-utils"],
            *dependency_parsed,
        ]
        package_inputs = [
            (derived_by_name["nvidia-utils"]["package"],
             args.nvidia_utils_signature, "nvidia-utils", True),
            (derived_by_name["lib32-nvidia-utils"]["package"],
             args.lib32_nvidia_utils_signature, "lib32-nvidia-utils", True),
        ] + [
            (package, signature, None, False)
            for package, signature in zip(
                args.dependency_package, args.dependency_signature
            )
        ]
        package_installed_bytes = sum(
            parsed["lockRecord"]["installedSize"] for parsed in parsed_packages
        )
        package_compressed_bytes = sum(
            package.stat().st_size for package, _, _, _ in package_inputs
        )

    package_records = []
    incoming_packages = {}
    for parsed in parsed_packages:
        record = parsed["lockRecord"]
        package_name = record["name"]
        expected_name = parsed["expectedName"]
        is_nvidia_package = parsed["isNvidiaPackage"]
        if expected_name is not None and package_name != expected_name:
            fail("userspace_package_mismatch", f"{package_name} has wrong seed identity")
        if record["architecture"] not in ("x86_64", "any"):
            fail("userspace_package_mismatch", f"{package_name} has wrong architecture")
        pkgver_only, separator, pkgrel = record["version"].rpartition("-")
        if not separator or not pkgver_only or not pkgrel:
            fail("userspace_package_invalid", f"{package_name} has invalid pkgver/pkgrel")
        if is_nvidia_package and pkgver_only != nvidia:
            fail("userspace_version_mismatch", f"{expected_name} does not match {nvidia}")
        require_reviewed_signer(record["signerFingerprint"], package_name)
        if package_name == "nvidia-utils" and not any(
            re.fullmatch(
                rf"usr/lib/firmware/nvidia/{re.escape(nvidia)}/gsp[^/]*\.bin",
                name,
            )
            for name in parsed["members"]
        ):
            fail("gsp_firmware_missing", "nvidia-utils lacks exact-version GSP firmware")
        package_records.append({
            "name": package_name,
            "role": "dependency" if not is_nvidia_package else "nvidia-userspace",
            "filename": record["filename"],
            "signatureFilename": record["signatureFilename"],
            "fullVersion": record["version"],
            "pkgver": pkgver_only,
            "pkgrel": pkgrel,
            "architecture": record["architecture"],
            "signer": record["signerFingerprint"],
            "sha256": record["packageSha256"],
            "signatureSha256": record["signatureSha256"],
            "installedSize": record["installedSize"],
            "dependencies": record["dependencies"],
            "provides": record["provides"],
        })
        incoming_packages[package_name] = {
            "name": package_name,
            "version": record["version"],
            "installedSize": record["installedSize"],
            "depends": record["dependencies"],
            "provides": record["provides"],
            "source": "incoming",
        }

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
    module_destination_relative = (
        f"usr/lib/modules/{args.kernel}/updates/open-gpu-kernel-modules-steamos"
    )
    require_safe_destination(root, module_destination_relative)
    existing_module_bytes = tree_regular_bytes(root / module_destination_relative)
    exact_existing_modules = existing_module_set_is_exact(
        root, args.kernel, module_payload_hashes
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
    conservative_root_required_bytes = (
        max(0, package_installed_bytes - replaced_package_bytes)
        + max(0, module_installed_bytes - existing_module_bytes)
        + initramfs_reserve_bytes
        + ROOT_METADATA_RESERVE_BYTES
    )
    progress.emit("storage_calculation", indeterminate=True, force=True)
    storage = {
        "rootAvailableBytes": available_bytes(root, "ROOT"),
        "rootRequiredBytes": conservative_root_required_bytes,
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
    if args.compression_profile:
        if compression["filesystem"] != "btrfs":
            fail(
                "compression_profile_unsupported",
                "the requested compression profile requires a Btrfs target root",
                storage=storage,
                packageDependencyClosure=closure,
                compression=compression,
            )
        if (compression["writeIncompatibleOptions"]
                or compression["invalidOptions"]
                or len(compression["options"]) > 1):
            fail(
                "compression_profile_unsupported",
                "the target Btrfs mount disables checksummed copy-on-write data",
                storage=storage,
                packageDependencyClosure=closure,
                compression=compression,
            )
        require_exclusive_btrfs_mount(root)
        for parsed in parsed_packages:
            for member in parsed["members"]:
                require_root_filesystem_destination(root, member)
        for relative in (
            module_destination_relative,
            "etc/modprobe.d",
            "etc/mkinitcpio.conf.d",
            "boot",
        ):
            require_root_filesystem_destination(root, relative)
        measurement = measured_btrfs_payload(
            args,
            [package for package, _, _, _ in package_inputs],
            package_installed_bytes + module_installed_bytes,
        )
        package_measurement_by_name = {
            item["filename"]: item["allocatedBytes"]
            for item in measurement["packageMeasurements"]
        }
        if len(package_measurement_by_name) != len(measurement["packageMeasurements"]):
            fail(
                "compression_measurement_invalid",
                "scratch-Btrfs package allocation identities are ambiguous",
            )
        exact_noop_packages = [
            parsed
            for parsed in parsed_packages
            if installed_packages.get(parsed["lockRecord"]["name"], {}).get("version")
            == parsed["lockRecord"]["version"]
        ]
        package_integrity_deadline = time.monotonic() + 600
        for parsed in exact_noop_packages:
            require_installed_package_integrity(
                root, parsed["lockRecord"]["name"], package_integrity_deadline
            )
        exact_noop_package_credit = sum(
            package_measurement_by_name[parsed["lockRecord"]["filename"]]
            for parsed in exact_noop_packages
        )
        # Upgrades receive no physical replacement credit: pacman may need the
        # old and new extents concurrently. Exact --needed no-ops are safe to
        # credit because those payloads are not written at all.
        exact_noop_module_credit = (
            measurement["moduleAllocatedBytes"] if exact_existing_modules else 0
        )
        replacement_credit_bytes = min(
            exact_noop_package_credit + exact_noop_module_credit,
            measurement["payloadAllocatedBytes"],
        )
        admitted_payload_bytes = (
            measurement["payloadAllocatedBytes"] - replacement_credit_bytes
        )
        measured_required_bytes = (
            admitted_payload_bytes
            + initramfs_reserve_bytes
            + ROOT_METADATA_RESERVE_BYTES
        )
        logical_payload_bytes = package_installed_bytes + module_installed_bytes
        ratio_millionths = (
            measurement["payloadAllocatedBytes"] * 1_000_000
            // max(1, logical_payload_bytes)
        )
        final_margin_bytes = storage["rootAvailableBytes"] - measured_required_bytes
        storage.update({
            "rootConservativeRequiredBytes": conservative_root_required_bytes,
            "rootLogicalRequiredBytes": (
                logical_payload_bytes + initramfs_reserve_bytes
                + ROOT_METADATA_RESERVE_BYTES
            ),
            "rootMeasuredRequiredBytes": measured_required_bytes,
            "measuredPayloadAllocatedBytes": measurement["payloadAllocatedBytes"],
            "compressionPayloadAllocatedBytes": measurement["payloadAllocatedBytes"],
            "compressionFilesystemOverheadBytes": measurement["filesystemOverheadBytes"],
            "compressionSafetyReserveBytes": ROOT_METADATA_RESERVE_BYTES,
            "compressionReserveBytes": (
                initramfs_reserve_bytes + ROOT_METADATA_RESERVE_BYTES
            ),
            "replacementCandidateLogicalBytes": (
                replaced_package_bytes + existing_module_bytes
            ),
            "replacementCreditBytes": replacement_credit_bytes,
            "packageNoopCreditBytes": exact_noop_package_credit,
            "moduleNoopCreditBytes": exact_noop_module_credit,
            "rootFinalMarginBytes": final_margin_bytes,
            "rootShortfallBytes": max(0, -final_margin_bytes),
            "rootRequiredBytes": measured_required_bytes,
        })
        admission_authorized = all(
            storage[f"{name}RequiredBytes"] <= storage[f"{name}AvailableBytes"]
            for name in ("root", "var", "efi")
        )
        compression.update({
            "requestedProfile": COMPRESSION_PROFILE,
            "writePolicy": COMPRESSION_WRITE_POLICY,
            "measurement": measurement,
            "admissionBasis": "scratch-btrfs-allocated-physical-bytes-minus-noop-credit-plus-reserves",
            "compressionSavingsCreditedBytes": max(
                0, conservative_root_required_bytes - measured_required_bytes
            ),
            "measuredPayloadSavingsBytes": max(
                0,
                package_installed_bytes + module_installed_bytes
                - measurement["payloadAllocatedBytes"],
            ),
            "declaredSizesLikelyConservative": (
                measurement["payloadAllocatedBytes"]
                < package_installed_bytes + module_installed_bytes
            ),
            "admissionAuthorized": admission_authorized,
            "pacmanCheckSpaceBypassAuthorized": admission_authorized,
            "pacmanCheckSpacePolicy": (
                "temporary-config-disable-after-live-revalidation"
                if admission_authorized else "preserve"
            ),
            "compressionRatio": (
                f"{ratio_millionths // 1_000_000}."
                f"{ratio_millionths % 1_000_000:06d}"
            ),
            "allPayloadDestinationsOnRootFilesystem": True,
            "filesystemMountExclusive": True,
            "replacementCreditPolicy": "exact-payload-noop-only",
            "modulePayloadNoop": exact_existing_modules,
            "mutationProfileImplemented": True,
            "assessment": "measured-profile-admission-ready",
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
        "inputSource": {
            "mode": args.input_source,
            "bundleCacheId": args.input_bundle_id or None,
        },
        "target": {
            "steamosVersion": identity["VERSION_ID"],
            "kernelVersion": args.kernel,
            "nvidiaVersion": nvidia,
            "architecture": "x86_64",
        },
        "archiveSha256": archive_sha,
        "provenanceSha256": provenance_sha,
        "userspaceLock": {
            "name": args.userspace_lock.name,
            "sha256": input_hashes.digest(args.userspace_lock),
        },
        "gamingPayload": gaming_payload or {
            "schemaVersion": 1, "status": "not-requested",
            "profileId": "gaming-no-cuda-v1",
        },
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
            "sha256": input_hashes.digest(args.package_keyring),
        },
        "packages": package_records,
        "modules": [
            {"name": name, "payloadSha256": module_payload_hashes[name]}
            for name in sorted(module_payload_hashes)
        ],
    }


def main():
    args = arguments()
    progress = ProgressReporter(args.progress_attempt)
    try:
        if ((args.input_source == "authenticated-bundle")
                != bool(re.fullmatch(r"[0-9a-f]{64}", args.input_bundle_id))):
            fail("input_source_invalid", "authenticated bundle source requires one exact cache identity")
        document = validate(args, progress)
    except Exception as error:
        if isinstance(error, ValidationFailure):
            reason, message = error.reason, error.message
            details = error.details
        else:
            # Always leave the caller a stable, non-sensitive result even when
            # an unexpected parser/tool failure reaches this outer boundary.
            reason = "validation_internal_error"
            message = "Offline-root validation failed unexpectedly before mutation."
            details = {}
        document = {
            "schemaVersion": 1,
            "status": "failed",
            "reason": reason,
            "message": message,
            **details,
        }
        atomic_write_bytes(
            args.output,
            (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        print(
            f"validate_install_inputs.py: {reason}: {message}",
            file=__import__("sys").stderr,
        )
        return 1
    atomic_write_bytes(
        args.output,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
