#!/usr/bin/env python3
"""Atomically write the offline-root installation result contract."""

import argparse
import json
import re
from pathlib import Path
from atomic_output import atomic_write_bytes


MAX_VALIDATION_BYTES = 16 * 1024 * 1024
MAX_MODULE_VERIFICATION_BYTES = 1024 * 1024
MAX_USERSPACE_VERIFICATION_BYTES = 256 * 1024
MAX_WORKSPACE_VERIFICATION_BYTES = 16 * 1024
MAX_INITRAMFS_VERIFICATION_BYTES = 256 * 1024
MAX_PAYLOAD_RECEIPT_BYTES = 64 * 1024
MAX_TARGET_EXECUTION_FAILURE_BYTES = 16 * 1024
TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
KERNEL = re.compile(r"(?:unknown|[A-Za-z0-9._+~-]{1,255})")
VERSION = re.compile(r"(?:unknown|[0-9]+\.[0-9]+(?:\.[0-9]+)?)")
TRUST_VALUES = {
    "pending-validation",
    "development-unverified",
    "locally-built-verified",
    "certified-published",
}
PLAIN_FILENAME = re.compile(r"[A-Za-z0-9@._+~:-]{1,255}")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
PACKAGE_KEYS = {
    "name", "role", "filename", "signatureFilename", "fullVersion",
    "pkgver", "pkgrel", "architecture", "signer", "sha256",
    "signatureSha256", "installedSize", "dependencies", "provides",
}
EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
}
INITRAMFS_REQUIRED_MODULES = (
    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
)
INITRAMFS_ROOTFS_ONLY_MODULES = ("nvidia-peermem.ko",)
ROOT_METADATA_RESERVE_BYTES = 64 * 1024 * 1024
VAR_RESERVE_BYTES = 16 * 1024 * 1024
REQUIRED_KERNEL_ARGUMENTS = (
    "rd.driver.blacklist=nouveau", "modprobe.blacklist=nouveau",
    "nvidia-drm.modeset=1", "nvidia-drm.fbdev=1",
)
MODULE_MISMATCH_FIELDS = (
    "presence", "representation", "payloadSha256", "mode", "uid", "gid",
    "decompression",
)
MODULE_DECOMPRESSION_STATUSES = {
    "verified", "not-required", "failed", "timeout", "size-limit", "empty",
    "not-attempted", "missing", "ambiguous", "unreadable",
}
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


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--status", required=True, choices=("success", "failed", "cancelled", "validated"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--steamos", default="unknown")
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--nvidia", default="unknown")
    parser.add_argument("--trust", default="pending-validation")
    parser.add_argument("--archive", default="")
    parser.add_argument("--provenance", default="")
    parser.add_argument("--nvidia-utils", default="")
    parser.add_argument("--lib32-nvidia-utils", default="")
    parser.add_argument("--mounts-released", choices=("true", "false"), default="true")
    parser.add_argument(
        "--compression-policy-restored",
        choices=("true", "false"),
        default="true",
    )
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--module-verification", type=Path)
    parser.add_argument("--userspace-verification", type=Path)
    parser.add_argument("--initramfs-workspace", type=Path)
    parser.add_argument("--initramfs-verification", type=Path)
    parser.add_argument("--payload-receipt", type=Path)
    parser.add_argument("--target-execution-failure", type=Path)
    parser.add_argument("--runtime-mounts-expected", type=int, default=0)
    parser.add_argument("--runtime-mounts-released", type=int, default=0)
    return parser.parse_args()


def plain_name(value):
    return (
        not value
        or (
            len(value) <= 255
            and PLAIN_FILENAME.fullmatch(value) is not None
            and Path(value).name == value
            and value not in (".", "..")
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )
    )


def bounded_message(value):
    return (
        0 < len(value) <= 2048
        and "\x00" not in value
        and all(character in "\n\t" or ord(character) >= 32 for character in value)
    )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_validation(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_VALIDATION_BYTES:
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Result validation metadata is unreadable or exceeds its size limit.")
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise SystemExit("Result validation metadata has an unsupported schema.")
    return document


def load_module_verification(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_MODULE_VERIFICATION_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Module verification metadata is unreadable or excessive.")
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise SystemExit("Module verification metadata is malformed.")
    status = document.get("status")
    records = (document.get("modules") if status == "verified"
               else document.get("moduleMismatches"))
    expected_keys = {
        "moduleName", "targetRelativePath", "representation",
        "expectedPayloadSha256", "actualPayloadSha256", "expectedMode",
        "actualMode", "expectedUid", "actualUid", "expectedGid", "actualGid",
        "compressedSizeBytes", "decompressionStatus", "invalidFields",
    }
    if not isinstance(records, list) or not 1 <= len(records) <= 6:
        raise SystemExit("Module verification records are malformed.")
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit("Module verification records are malformed.")
        keys = set(record)
        if keys not in (expected_keys, expected_keys | {"unexpectedEntries"}):
            raise SystemExit("Module verification records are malformed.")
        name = record.get("moduleName")
        relative = record.get("targetRelativePath")
        invalid = record.get("invalidFields")
        if (name not in EXPECTED_MODULES | {"unexpected"}
                or not isinstance(relative, str) or not 1 <= len(relative) <= 512
                or Path(relative).is_absolute() or ".." in Path(relative).parts
                or not relative.startswith("usr/lib/modules/")
                or re.fullmatch(r"[A-Za-z0-9._+~/-]+", relative) is None
                or record.get("representation") not in (None, ".ko", ".ko.zst")
                or record.get("decompressionStatus")
                not in MODULE_DECOMPRESSION_STATUSES
                or not isinstance(invalid, list)
                or invalid != [field for field in MODULE_MISMATCH_FIELDS if field in invalid]
                or any(field not in MODULE_MISMATCH_FIELDS for field in invalid)):
            raise SystemExit("Module verification records are malformed.")
        if name in EXPECTED_MODULES and (
                record.get("expectedPayloadSha256") is None
                or record.get("expectedMode") != "0644"
                or record.get("expectedUid") != 0
                or record.get("expectedGid") != 0):
            raise SystemExit("Module verification expectations are malformed.")
        for field in ("expectedPayloadSha256", "actualPayloadSha256"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str)
                                      or HEX_SHA256.fullmatch(value) is None):
                raise SystemExit("Module verification records are malformed.")
        for field in ("expectedMode", "actualMode"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str)
                                      or re.fullmatch(r"[0-7]{4}", value) is None):
                raise SystemExit("Module verification records are malformed.")
        for field in ("expectedUid", "actualUid", "expectedGid", "actualGid"):
            value = record.get(field)
            if value is not None and (not isinstance(value, int)
                                      or isinstance(value, bool)
                                      or not 0 <= value <= 2**32 - 1):
                raise SystemExit("Module verification records are malformed.")
        compressed_size = record.get("compressedSizeBytes")
        if compressed_size is not None and (
                not isinstance(compressed_size, int)
                or isinstance(compressed_size, bool)
                or not 0 <= compressed_size <= 1024 * 1024 * 1024):
            raise SystemExit("Module verification records are malformed.")
        unexpected = record.get("unexpectedEntries")
        if unexpected is not None and (
                name != "unexpected" or not isinstance(unexpected, list)
                or not 1 <= len(unexpected) <= 16
                or unexpected != sorted(set(unexpected))
                or any(not isinstance(value, str)
                       or PLAIN_FILENAME.fullmatch(value) is None
                       for value in unexpected)):
            raise SystemExit("Module verification records are malformed.")
    if status == "verified":
        if (set(document) != {"schemaVersion", "status", "reason", "modules"}
                or document.get("reason") != "installed_modules_verified"
                or len(records) != len(EXPECTED_MODULES)
                or {record["moduleName"] for record in records} != EXPECTED_MODULES
                or any(record["invalidFields"] for record in records)):
            raise SystemExit("Successful module verification metadata is inconsistent.")
    elif status == "failed":
        if (set(document) != {"schemaVersion", "status", "reason", "message",
                             "moduleMismatches"}
                or document.get("reason") != "installed_module_mismatch"
                or not isinstance(document.get("message"), str)
                or not bounded_message(document["message"])
                or any(not record["invalidFields"] for record in records)):
            raise SystemExit("Failed module verification metadata is inconsistent.")
    else:
        raise SystemExit("Module verification status is unsupported.")
    return document


def load_initramfs_workspace(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_WORKSPACE_VERIFICATION_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Initramfs workspace metadata is unreadable or excessive.")
    common = {
        "schemaVersion", "status", "reason", "phase", "condition",
        "requiredBytes", "requiredInodes",
    }
    optional = {
        "message", "availableBytes", "availableInodes", "mode",
        "expectedMode", "actualMode", "inodeCapacityMode",
    }
    if (not isinstance(document, dict)
            or not common <= set(document)
            or not set(document) <= common | optional
            or document.get("schemaVersion") != 1
            or document.get("reason") not in {
                "initramfs_workspace_available", "initramfs_workspace_unavailable",
                "initramfs_workspace_target_available",
                "initramfs_workspace_target_missing",
            }
            or document.get("phase") not in {
                "target_directory", "backing_directory", "backing_capacity",
                "mounted_workspace", "target_capacity",
            }
            or document.get("condition") not in {
                "available", "missing_directory", "invalid_type", "permissions",
                "insufficient_bytes", "insufficient_inodes",
            }):
        raise SystemExit("Initramfs workspace metadata is malformed.")
    inode_capacity_mode = document.get("inodeCapacityMode")
    if (inode_capacity_mode is not None
            and inode_capacity_mode not in {
                "finite-statvfs", "dynamic-probed",
                "dynamic-probe-failed", "not-applicable-bind-target",
            }):
        raise SystemExit("Initramfs workspace inode metadata is malformed.")
    for field in ("requiredBytes", "requiredInodes", "availableBytes", "availableInodes"):
        value = document.get(field)
        if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
                or not 0 <= value <= 2**63 - 1):
            raise SystemExit("Initramfs workspace capacity metadata is malformed.")
    for field in ("mode", "expectedMode", "actualMode"):
        value = document.get(field)
        if value is not None and (
                not isinstance(value, str) or re.fullmatch(r"[0-7]{4}", value) is None):
            raise SystemExit("Initramfs workspace permission metadata is malformed.")
    if document["status"] == "verified":
        verified_shape = (
            document["reason"] == "initramfs_workspace_available"
            and document["phase"] in {"backing_capacity", "mounted_workspace"}
        ) or (
            document["reason"] == "initramfs_workspace_target_available"
            and document["phase"] == "target_directory"
        )
        finite_capacity = inode_capacity_mode == "finite-statvfs"
        dynamic_capacity = inode_capacity_mode == "dynamic-probed"
        bind_target_capacity = inode_capacity_mode == "not-applicable-bind-target"
        if (not verified_shape
                or document["condition"] != "available"
                or document.get("mode") != "1777"
                or inode_capacity_mode is None
                or not (finite_capacity or dynamic_capacity
                        or bind_target_capacity)
                or (finite_capacity and (
                    document.get("availableInodes") is None
                    or document["availableInodes"] < document["requiredInodes"]
                ))
                or (dynamic_capacity and (
                    document.get("availableInodes") is not None
                    or document["reason"] != "initramfs_workspace_available"
                ))
                or (bind_target_capacity and (
                    document.get("availableInodes") is not None
                    or document["reason"]
                    != "initramfs_workspace_target_available"
                ))
                or "message" in document):
            raise SystemExit("Verified initramfs workspace metadata is inconsistent.")
    elif document["status"] == "preparation-required":
        if (document["reason"] != "initramfs_workspace_target_missing"
                or document["phase"] != "target_directory"
                or document["condition"] != "missing_directory"
                or document.get("mode") is not None
                or "message" in document):
            raise SystemExit("Target workspace preparation metadata is inconsistent.")
    elif document["status"] == "failed":
        if (document["reason"] != "initramfs_workspace_unavailable"
                or document["condition"] == "available"
                or not isinstance(document.get("message"), str)
                or not bounded_message(document["message"])):
            raise SystemExit("Failed initramfs workspace metadata is inconsistent.")
    else:
        raise SystemExit("Initramfs workspace status is unsupported.")
    return document


def load_initramfs_verification(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_INITRAMFS_VERIFICATION_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"),
                              object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SystemExit("Initramfs verification metadata is unreadable or excessive.")
    if (not isinstance(document, dict) or set(document) != {
            "schemaVersion", "status", "kernelVersion", "requiredModules",
            "rootfsOnlyModules", "tools", "config", "images"}
            or document.get("schemaVersion") != 1 or document.get("status") != "verified"
            or not KERNEL.fullmatch(document.get("kernelVersion", ""))
            or document.get("requiredModules") != list(INITRAMFS_REQUIRED_MODULES)
            or document.get("rootfsOnlyModules")
            != list(INITRAMFS_ROOTFS_ONLY_MODULES)):
        raise SystemExit("Initramfs verification metadata is malformed.")
    tools = document["tools"]
    if not isinstance(tools, dict) or set(tools) != {"mkinitcpio", "lsinitcpio"}:
        raise SystemExit("Initramfs tool verification metadata is malformed.")
    for name, record in tools.items():
        if (not isinstance(record, dict) or set(record) != {"path", "sizeBytes", "sha256"}
                or record.get("path") != f"/usr/bin/{name}"
                or not isinstance(record.get("sizeBytes"), int)
                or isinstance(record.get("sizeBytes"), bool)
                or not 0 < record["sizeBytes"] <= 8 * 1024 * 1024
                or not isinstance(record.get("sha256"), str)
                or HEX_SHA256.fullmatch(record["sha256"]) is None):
            raise SystemExit("Initramfs tool verification metadata is malformed.")
    config = document["config"]
    if (not isinstance(config, dict) or set(config) != {"path", "sizeBytes", "sha256"}
            or config.get("path") != "/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
            or not isinstance(config.get("sizeBytes"), int)
            or isinstance(config.get("sizeBytes"), bool)
            or not 0 < config["sizeBytes"] <= 1024 * 1024
            or not isinstance(config.get("sha256"), str)
            or HEX_SHA256.fullmatch(config["sha256"]) is None):
        raise SystemExit("Initramfs configuration verification metadata is malformed.")
    images = document["images"]
    if not isinstance(images, list) or not 1 <= len(images) <= 32:
        raise SystemExit("Initramfs image verification metadata is malformed.")
    filenames = set()
    for image in images:
        if (not isinstance(image, dict) or set(image) != {
                "filename", "sizeBytes", "sha256", "listingSha256", "entries",
                "modules", "configPath"}
                or not plain_name(image.get("filename", ""))
                or image["filename"] in filenames
                or not isinstance(image.get("sizeBytes"), int)
                or isinstance(image.get("sizeBytes"), bool)
                or not 0 < image["sizeBytes"] <= 2 * 1024 * 1024 * 1024
                or not isinstance(image.get("entries"), int)
                or isinstance(image.get("entries"), bool)
                or not 1 <= image["entries"] <= 200000
                or any(not isinstance(image.get(field), str)
                       or HEX_SHA256.fullmatch(image[field]) is None
                       for field in ("sha256", "listingSha256"))
                or image.get("configPath")
                != "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
                or not isinstance(image.get("modules"), dict)
                or set(image["modules"]) != set(INITRAMFS_REQUIRED_MODULES)):
            raise SystemExit("Initramfs image verification metadata is malformed.")
        filenames.add(image["filename"])
        module_values = list(image["modules"].values())
        if (len(set(module_values)) != len(module_values)
                or any(
                    not isinstance(value, str) or len(value) > 1024
                    or value.startswith("/") or ".." in Path(value).parts
                    or not value.startswith(
                        f"usr/lib/modules/{document['kernelVersion']}/")
                    or Path(value).name not in {
                        module + suffix for suffix in ("", ".gz", ".xz", ".zst", ".lz4", ".lzo")
                    }
                    for module, value in image["modules"].items()
                )):
            raise SystemExit("Initramfs module verification metadata is malformed.")
    return document


def load_userspace_verification(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_USERSPACE_VERIFICATION_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SystemExit("Userspace verification metadata is unreadable or excessive.")
    if (not isinstance(document, dict)
            or set(document) != {
                "schemaVersion", "status", "reason", "pacmanDatabase",
                "packages", "gspFirmware",
            }
            or document.get("schemaVersion") != 1
            or document.get("status") != "verified"
            or document.get("reason") != "installed_userspace_verified"):
        raise SystemExit("Userspace verification metadata is malformed.")
    packages = document.get("packages")
    package_keys = {
        "packageName", "version", "packageSha256", "packageQueryVerified",
        "pacmanIntegrityVerified", "payloadVerified", "directories",
        "regularFiles", "symlinks", "hardlinks", "sharedLibraries",
    }
    if (not isinstance(packages, list) or not 2 <= len(packages) <= 64
            or any(not isinstance(record, dict) or set(record) != package_keys
                   for record in packages)):
        raise SystemExit("Userspace verification package records are malformed.")
    names = []
    for record in packages:
        names.append(record.get("packageName"))
        if (not isinstance(record.get("packageName"), str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", record["packageName"])
                is None
                or not isinstance(record.get("version"), str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", record["version"])
                is None
                or not isinstance(record.get("packageSha256"), str)
                or HEX_SHA256.fullmatch(record["packageSha256"]) is None
                or any(record.get(field) is not True for field in (
                    "packageQueryVerified", "pacmanIntegrityVerified",
                    "payloadVerified",
                ))
                or any(not isinstance(record.get(field), int)
                       or isinstance(record[field], bool)
                       or not 0 <= record[field] <= 250_000
                       for field in (
                           "directories", "regularFiles", "symlinks", "hardlinks",
                           "sharedLibraries",
                       ))
                or record["sharedLibraries"] > (
                    record["regularFiles"] + record["symlinks"]
                    + record["hardlinks"]
                )):
            raise SystemExit("Userspace verification package records are invalid.")
    if len(names) != len(set(names)):
        raise SystemExit("Userspace verification package identities are duplicated.")
    database = document.get("pacmanDatabase")
    if (not isinstance(database, dict)
            or set(database) != {
                "path", "status", "verifiedPackageCount", "consistencyVerified",
            }
            or database.get("path") != "/usr/lib/holo/pacmandb"
            or database.get("status") != "verified"
            or database.get("consistencyVerified") is not True
            or not isinstance(database.get("verifiedPackageCount"), int)
            or isinstance(database["verifiedPackageCount"], bool)
            or database["verifiedPackageCount"] != len(packages)):
        raise SystemExit("Userspace pacman database verification is malformed.")
    firmware = document.get("gspFirmware")
    if (not isinstance(firmware, dict)
            or set(firmware) != {"version", "status", "targetRelativeFiles"}
            or firmware.get("status") != "verified"
            or not isinstance(firmware.get("version"), str)
            or VERSION.fullmatch(firmware["version"]) is None
            or not isinstance(firmware.get("targetRelativeFiles"), list)
            or not 1 <= len(firmware["targetRelativeFiles"]) <= 16
            or any(not isinstance(relative, str)
                   for relative in firmware["targetRelativeFiles"])
            or firmware["targetRelativeFiles"]
            != sorted(set(firmware["targetRelativeFiles"]))):
        raise SystemExit("Userspace GSP firmware verification is malformed.")
    prefix = f"usr/lib/firmware/nvidia/{firmware['version']}/"
    for relative in firmware["targetRelativeFiles"]:
        if (not isinstance(relative, str) or not relative.startswith(prefix)
                or Path(relative).is_absolute() or ".." in Path(relative).parts
                or re.fullmatch(r"[A-Za-z0-9._+~/-]{1,512}", relative) is None
                or not Path(relative).name.startswith("gsp")
                or not Path(relative).name.endswith(".bin")):
            raise SystemExit("Userspace GSP firmware verification is malformed.")
    return document


def load_payload_receipt(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_PAYLOAD_RECEIPT_BYTES):
            raise OSError
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SystemExit("Payload receipt verification is unreadable or excessive.")
    roles = [
        "buildInfo", "provenance", "validation", "moduleVerification",
        "userspaceVerification", "initramfsVerification",
    ]
    records = document.get("records") if isinstance(document, dict) else None
    if (not isinstance(document, dict)
            or document.get("schemaVersion") != 1
            or document.get("status") != "verified"
            or document.get("reason") != "payload_receipt_verified"
            or not isinstance(document.get("receiptId"), str)
            or HEX_SHA256.fullmatch(document["receiptId"]) is None
            or document.get("rootfsRelativePath")
            != "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install/receipt.json"
            or not isinstance(document.get("target"), dict)
            or not isinstance(records, list)
            or len(records) != len(roles)
            or [record.get("role") for record in records
               if isinstance(record, dict)] != roles):
        raise SystemExit("Payload receipt verification is malformed.")
    filenames = set()
    for record in records:
        if (not isinstance(record, dict)
                or set(record) != {"role", "filename", "sizeBytes", "sha256"}
                or not plain_name(record.get("filename", ""))
                or record["filename"] in filenames
                or not isinstance(record.get("sizeBytes"), int)
                or isinstance(record["sizeBytes"], bool)
                or not 0 < record["sizeBytes"] <= 16 * 1024 * 1024
                or not isinstance(record.get("sha256"), str)
                or HEX_SHA256.fullmatch(record["sha256"]) is None):
            raise SystemExit("Payload receipt verification records are malformed.")
        filenames.add(record["filename"])
    return document


def load_target_execution_failure(path):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= MAX_TARGET_EXECUTION_FAILURE_BYTES):
            raise OSError
        document = json.loads(path.read_text(encoding="utf-8"),
                              object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SystemExit("Target execution failure metadata is unreadable or excessive.")
    if (not isinstance(document, dict) or set(document) != {
            "schemaVersion", "status", "reason", "condition", "message",
            "targetRelativePath",
            }
            or document.get("schemaVersion") != 1
            or document.get("status") != "failed"
            or document.get("reason") != "target_execution_trust_failed"
            or TOKEN.fullmatch(document.get("condition", "")) is None
            or not bounded_message(document.get("message", ""))):
        raise SystemExit("Target execution failure metadata is malformed.")
    relative = document.get("targetRelativePath")
    if relative is not None and (
            not isinstance(relative, str)
            or not 1 <= len(relative) <= 512
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or re.fullmatch(r"[A-Za-z0-9._+~/-]+", relative) is None):
        raise SystemExit("Target execution failure path is malformed.")
    return document


def validate_verified_metadata(validation):
    required = {
        "inputSource", "archiveSha256", "provenanceSha256", "userspaceLock",
        "pacmanDatabase", "boot", "keyring", "packages", "modules", "storage",
        "packageDependencyClosure", "compression", "gamingPayload",
    }
    if not required <= validation.keys():
        raise SystemExit("Verified installation metadata is incomplete.")
    input_source = validation["inputSource"]
    if (not isinstance(input_source, dict)
            or not {"mode", "bundleCacheId"} <= set(input_source)
            or input_source.get("mode") not in {"direct", "authenticated-bundle"}
            or (input_source["mode"] == "direct"
                and input_source.get("bundleCacheId") is not None)
            or (input_source["mode"] == "authenticated-bundle"
                and (not isinstance(input_source.get("bundleCacheId"), str)
                     or HEX_SHA256.fullmatch(input_source["bundleCacheId"]) is None))):
        raise SystemExit("Verified installation input-source metadata is invalid.")
    for field in ("archiveSha256", "provenanceSha256"):
        if not isinstance(validation[field], str) or not HEX_SHA256.fullmatch(
            validation[field]
        ):
            raise SystemExit("Verified installation metadata contains an invalid hash.")
    for identity in (validation["userspaceLock"], validation["keyring"]):
        if (not isinstance(identity, dict) or set(identity) != {"name", "sha256"}
                or not identity.get("name")
                or not plain_name(identity.get("name"))
                or not isinstance(identity.get("sha256"), str)
                or not HEX_SHA256.fullmatch(identity["sha256"])):
            raise SystemExit("Verified installation metadata has an invalid pinned input.")
    packages = validation["packages"]
    if (not isinstance(packages, list) or not 2 <= len(packages) <= 64
            or any(not isinstance(package, dict) or set(package) != PACKAGE_KEYS
                   for package in packages)):
        raise SystemExit("Verified installation package metadata is malformed.")
    for package in packages:
        if (not isinstance(package["name"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["name"]) is None
                or not isinstance(package["fullVersion"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["fullVersion"]) is None
                or not isinstance(package["pkgver"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["pkgver"]) is None
                or not isinstance(package["pkgrel"], str)
                or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", package["pkgrel"]) is None
                or not package["filename"]
                or not plain_name(package["filename"])
                or not package["signatureFilename"]
                or not plain_name(package["signatureFilename"])
                or package["architecture"] not in ("x86_64", "any")
                or package["role"] not in ("nvidia-userspace", "dependency")
                or not isinstance(package["installedSize"], int)
                or isinstance(package["installedSize"], bool)
                or not 0 <= package["installedSize"] <= 16 * 1024**3
                or not isinstance(package["sha256"], str)
                or not HEX_SHA256.fullmatch(package["sha256"])
                or not isinstance(package["signatureSha256"], str)
                or not HEX_SHA256.fullmatch(package["signatureSha256"])
                or not isinstance(package["signer"], str)
                or re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", package["signer"]) is None):
            raise SystemExit("Verified installation package metadata is invalid.")
        for field in ("dependencies", "provides"):
            relations = package[field]
            if (not isinstance(relations, list) or len(relations) > 64
                    or any(not isinstance(value, str) or not 0 < len(value) <= 256
                           for value in relations)):
                raise SystemExit("Verified installation package relations are invalid.")
    for field in ("name", "filename", "signatureFilename"):
        identities = [package[field] for package in packages]
        if len(identities) != len(set(identities)):
            raise SystemExit("Verified installation package identities are duplicated.")
    database = validation["pacmanDatabase"]
    if (not isinstance(database, dict)
            or set(database) != {"path", "packageCount"}
            or database.get("path") != "/usr/lib/holo/pacmandb"
            or not isinstance(database.get("packageCount"), int)
            or isinstance(database["packageCount"], bool)
            or not 1 <= database["packageCount"] <= 250_000):
        raise SystemExit("Verified installation pacman database metadata is invalid.")
    boot = validation["boot"]
    if (not isinstance(boot, dict)
            or not {"rootfsBootPath", "efiMountPath", "grubConfiguration",
                    "requiredKernelArguments"} <= set(boot)
            or boot.get("rootfsBootPath") != "/boot"
            or boot.get("efiMountPath") != "/efi"
            or boot.get("grubConfiguration") != "/efi/EFI/steamos/grub.cfg"
            or boot.get("requiredKernelArguments") != list(REQUIRED_KERNEL_ARGUMENTS)):
        raise SystemExit("Verified installation boot policy is invalid.")
    closure = validation["packageDependencyClosure"]
    if (not isinstance(closure, list) or not 2 <= len(closure) <= 4096
            or any(not isinstance(record, dict)
                   or set(record) != {"name", "version", "source"}
                   or not isinstance(record.get("name"), str)
                   or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", record["name"])
                   is None
                   or not isinstance(record.get("version"), str)
                   or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", record["version"])
                   is None
                   or record.get("source") not in {"incoming", "installed"}
                   for record in closure)
            or len({record["name"] for record in closure}) != len(closure)):
        raise SystemExit("Verified installation dependency closure is invalid.")
    incoming_closure = {
        record["name"]: record["version"]
        for record in closure if record["source"] == "incoming"
    }
    if incoming_closure != {
        package["name"]: package["fullVersion"] for package in packages
    }:
        raise SystemExit("Verified installation dependency closure is inconsistent.")
    modules = validation["modules"]
    if (not isinstance(modules, list) or len(modules) != len(EXPECTED_MODULES)
            or any(not isinstance(module, dict)
                   or set(module) != {"name", "payloadSha256"}
                   or not isinstance(module.get("name"), str)
                   or not isinstance(module.get("payloadSha256"), str)
                   or HEX_SHA256.fullmatch(module["payloadSha256"]) is None
                   for module in modules)):
        raise SystemExit("Verified installation module metadata is invalid.")
    if {module["name"] for module in modules} != EXPECTED_MODULES:
        raise SystemExit("Verified installation module metadata is invalid.")
    gaming = validation["gamingPayload"]
    if (not isinstance(gaming, dict) or gaming.get("schemaVersion") != 1
            or gaming.get("profileId") != "gaming-no-cuda-v1"
            or gaming.get("status") not in ("not-requested", "reviewed")):
        raise SystemExit("Verified gaming payload metadata is invalid.")
    if gaming["status"] == "not-requested":
        if set(gaming) != {"schemaVersion", "status", "profileId"}:
            raise SystemExit("Verified gaming payload metadata is invalid.")
    elif (set(gaming) != {"schemaVersion", "status", "profileId", "sha256",
                         "policySha256", "target", "delivery",
                         "omittedCapabilities", "preservedCapabilities",
                         "packageOwnership", "savedBytes", "packageRecords"}
          or not HEX_SHA256.fullmatch(gaming.get("sha256", ""))
          or not HEX_SHA256.fullmatch(gaming.get("policySha256", ""))
          or gaming.get("target") != validation.get("target")
          or gaming.get("delivery") != {
              "strategy": "deterministic-authenticated-source-repack-v1",
              "packageOwnership": "archive-and-pacman-database-exact",
              "sourceAuthentication": (
                  "arch-detached-signatures-and-reviewed-userspace-lock"
              ),
              "repacker": {"name": "repack_gaming_userspace.py",
                           "schemaVersion": 1, "zstdVersion": "1.5.7",
                           "compression": "zstd-19-t1"},
          }
          or gaming.get("omittedCapabilities") != ["cuda-compute"]
          or set(gaming.get("preservedCapabilities", [])) != {
              "graphics", "vulkan", "glvnd-egl", "nvenc", "nvdec",
              "gsp-firmware", "gaming-32bit", "recovery-rendering",
          }
          or gaming.get("packageOwnership") != "archive-and-pacman-database-exact"
          or not isinstance(gaming.get("savedBytes"), int)
          or isinstance(gaming["savedBytes"], bool) or gaming["savedBytes"] <= 0
          or not isinstance(gaming.get("packageRecords"), list)
          or len(gaming["packageRecords"]) != 2):
        raise SystemExit("Verified gaming payload metadata is invalid.")
    if gaming["status"] == "reviewed":
        record_keys = {
            "name", "sourceFilename", "sourceSignatureFilename", "sourceSha256",
            "sourceSignatureSha256", "sourceSignerFingerprint", "filename",
            "version", "sha256", "installedSize", "savedBytes",
        }
        names = set()
        saved = 0
        for record in gaming["packageRecords"]:
            if (not isinstance(record, dict) or set(record) != record_keys
                    or record.get("name") not in {
                        "nvidia-utils", "lib32-nvidia-utils"}
                    or record["name"] in names
                    or any(not plain_name(record.get(field)) for field in (
                        "sourceFilename", "sourceSignatureFilename", "filename"
                    ))
                    or any(not HEX_SHA256.fullmatch(record.get(field, ""))
                           for field in ("sourceSha256", "sourceSignatureSha256", "sha256"))
                    or re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}",
                                    record.get("sourceSignerFingerprint", "")) is None
                    or not isinstance(record.get("version"), str)
                    or re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", record["version"]) is None
                    or any(not isinstance(record.get(field), int)
                           or isinstance(record[field], bool) or record[field] < 0
                           for field in ("installedSize", "savedBytes"))):
                raise SystemExit("Verified gaming payload package metadata is invalid.")
            names.add(record["name"])
            saved += record["savedBytes"]
        if (names != {"nvidia-utils", "lib32-nvidia-utils"}
                or saved != gaming["savedBytes"]):
            raise SystemExit("Verified gaming payload package metadata is invalid.")
        validated_by_name = {item["name"]: item for item in packages}
        for record in gaming["packageRecords"]:
            installed = validated_by_name.get(record["name"])
            if (installed is None
                    or installed["filename"] != record["filename"]
                    or installed["signatureFilename"]
                    != record["sourceSignatureFilename"]
                    or installed["fullVersion"] != record["version"]
                    or installed["sha256"] != record["sha256"]
                    or installed["signatureSha256"]
                    != record["sourceSignatureSha256"]
                    or installed["signer"] != record["sourceSignerFingerprint"]
                    or installed["installedSize"] != record["installedSize"]):
                raise SystemExit(
                    "Verified gaming payload differs from package metadata."
                )
    storage = validation["storage"]
    compression = validation["compression"]
    base_storage = {
        "rootAvailableBytes", "rootRequiredBytes", "varAvailableBytes",
        "varRequiredBytes", "efiAvailableBytes", "efiRequiredBytes",
        "packageInstalledBytes", "packageCompressedBytes", "packageReplacedBytes",
        "moduleInstalledBytes", "moduleReplacedBytes", "initramfsReserveBytes",
    }
    compression_required = {
        "filesystem", "enabled", "options", "invalidOptions",
        "writeIncompatibleOptions", "admissionBasis",
        "compressionSavingsCreditedBytes", "declaredPackageBytes",
        "packageArchiveBytes", "packageArchiveSavingsBytes",
        "declaredSizesLikelyConservative", "assessment",
        "pacmanCheckSpaceBypassAuthorized", "pacmanCheckSpacePolicy",
    }
    if (not isinstance(storage, dict) or not base_storage <= storage.keys()
            or any(not isinstance(storage[field], int) or isinstance(storage[field], bool)
                   or not 0 <= storage[field] <= 2**63 - 1 for field in base_storage)
            or storage["varRequiredBytes"] != VAR_RESERVE_BYTES
            or not isinstance(compression, dict)
            or not compression_required <= compression.keys()):
        raise SystemExit("Verified installation storage metadata is invalid.")
    for field in (
        "compressionSavingsCreditedBytes", "declaredPackageBytes",
        "packageArchiveBytes", "packageArchiveSavingsBytes",
    ):
        if (not isinstance(compression[field], int)
                or isinstance(compression[field], bool)
                or not 0 <= compression[field] <= 2**63 - 1):
            raise SystemExit("Verified filesystem compression context is invalid.")
    if (compression["declaredPackageBytes"] != storage["packageInstalledBytes"]
            or compression["packageArchiveBytes"] != storage["packageCompressedBytes"]
            or compression["packageArchiveSavingsBytes"] != max(
                0, compression["declaredPackageBytes"]
                - compression["packageArchiveBytes"]
            )
            or not isinstance(compression["declaredSizesLikelyConservative"], bool)
            or compression["assessment"] not in {
                "informational-package-archive-proxy-not-admission-credit",
                "measured-profile-admission-ready",
            }):
        raise SystemExit("Verified filesystem compression context is inconsistent.")
    for option_field in ("options", "invalidOptions", "writeIncompatibleOptions"):
        options = compression.get(option_field)
        if (not isinstance(options, list) or len(options) > 8
                or any(not isinstance(option, str)
                       or re.fullmatch(r"[a-z0-9=:+_-]{1,64}", option) is None
                       for option in options)):
            raise SystemExit("Verified filesystem compression context is invalid.")
    if (not isinstance(compression.get("filesystem"), str)
            or re.fullmatch(r"[a-z0-9._+-]{1,32}", compression["filesystem"])
            is None
            or not isinstance(compression.get("enabled"), bool)):
        raise SystemExit("Verified filesystem compression context is invalid.")
    if "requestedProfile" in compression:
        measured_storage = {
            "rootConservativeRequiredBytes", "rootMeasuredRequiredBytes",
            "rootLogicalRequiredBytes", "measuredPayloadAllocatedBytes",
            "compressionPayloadAllocatedBytes", "compressionFilesystemOverheadBytes",
            "compressionSafetyReserveBytes", "compressionReserveBytes",
            "replacementCandidateLogicalBytes", "replacementCreditBytes",
            "packageNoopCreditBytes", "moduleNoopCreditBytes",
            "rootShortfallBytes",
        }
        measurement = compression.get("measurement")
        measurement_numbers = {
            "schemaVersion", "declaredPayloadBytes", "scratchFilesystemBytes",
            "payloadAllocatedBytes", "dataAllocatedBytes", "metadataAllocatedBytes",
            "systemAllocatedBytes", "filesystemOverheadBytes",
        }
        measurement_keys = {
            *measurement_numbers,
            "status", "profile", "writePolicy", "measurementMethod",
            "packageMeasurements", "moduleAllocatedBytes",
        }
        package_measurements = measurement.get("packageMeasurements", []) \
            if isinstance(measurement, dict) else []
        expected_package_filenames = [
            package["filename"] for package in validation["packages"]
        ]
        ratio_millionths = (
            measurement.get("payloadAllocatedBytes", 0) * 1_000_000
            // max(1, measurement.get("declaredPayloadBytes", 0))
        ) if isinstance(measurement, dict) else -1
        if (compression.get("requestedProfile") != "btrfs-zstd3"
                or compression.get("writePolicy") != "compress-force=zstd:3"
                or compression.get("admissionBasis")
                != "scratch-btrfs-allocated-physical-bytes-minus-noop-credit-plus-reserves"
                or not isinstance(measurement, dict)
                or not isinstance(compression.get("admissionAuthorized"), bool)
                or not isinstance(compression.get("measuredPayloadSavingsBytes"), int)
                or isinstance(compression.get("measuredPayloadSavingsBytes"), bool)
                or compression["measuredPayloadSavingsBytes"] < 0
                or compression["measuredPayloadSavingsBytes"] != max(
                    0,
                    measurement.get("declaredPayloadBytes", 0)
                    - measurement.get("payloadAllocatedBytes", 0),
                )
                or compression.get("declaredSizesLikelyConservative")
                is not (measurement.get("payloadAllocatedBytes", 0)
                        < measurement.get("declaredPayloadBytes", 0))
                or compression.get("compressionSavingsCreditedBytes") != max(
                    0,
                    storage["rootConservativeRequiredBytes"]
                    - storage["rootMeasuredRequiredBytes"],
                )
                or compression.get("mutationProfileImplemented") is not True
                or compression.get("allPayloadDestinationsOnRootFilesystem") is not True
                or compression.get("filesystemMountExclusive") is not True
                or compression.get("writeIncompatibleOptions") != []
                or compression.get("invalidOptions") != []
                or not isinstance(compression.get("options"), list)
                or len(compression["options"]) > 1
                or compression.get("replacementCreditPolicy")
                != "exact-payload-noop-only"
                or not isinstance(compression.get("modulePayloadNoop"), bool)
                or compression.get("assessment") != "measured-profile-admission-ready"
                or compression.get("pacmanCheckSpaceBypassAuthorized")
                is not compression.get("admissionAuthorized")
                or compression.get("pacmanCheckSpacePolicy") != (
                    "temporary-config-disable-after-live-revalidation"
                    if compression.get("admissionAuthorized") else "preserve"
                )
                or not isinstance(compression.get("compressionRatio"), str)
                or re.fullmatch(r"[0-9]+\.[0-9]{6}", compression["compressionRatio"])
                is None
                or not measured_storage <= storage.keys()
                or any(not isinstance(storage[field], int)
                       or isinstance(storage[field], bool)
                       or not 0 <= storage[field] <= 2**63 - 1
                       for field in measured_storage)
                or measurement.get("status") != "measured"
                or measurement.get("schemaVersion") != 1
                or measurement.get("profile") != "btrfs-zstd3"
                or measurement.get("writePolicy") != "compress-force=zstd:3"
                or measurement.get("measurementMethod")
                != "scratch-btrfs-filesystem-usage-used-delta"
                or set(measurement) != measurement_keys
                or not isinstance(package_measurements, list)
                or len(package_measurements) != len(expected_package_filenames)
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"filename", "allocatedBytes"}
                    or item.get("filename") != expected_filename
                    or not isinstance(item.get("allocatedBytes"), int)
                    or isinstance(item.get("allocatedBytes"), bool)
                    or item["allocatedBytes"] < 0
                    for item, expected_filename in zip(
                        package_measurements, expected_package_filenames
                    )
                )
                or not isinstance(measurement.get("moduleAllocatedBytes"), int)
                or isinstance(measurement.get("moduleAllocatedBytes"), bool)
                or measurement.get("moduleAllocatedBytes", -1) < 0
                or sum(item["allocatedBytes"] for item in package_measurements)
                + measurement.get("moduleAllocatedBytes", 0)
                > measurement.get("payloadAllocatedBytes", 0)
                or not measurement_numbers <= measurement.keys()
                or any(not isinstance(measurement[field], int)
                       or isinstance(measurement[field], bool) or measurement[field] < 0
                       for field in measurement_numbers)
                or measurement["dataAllocatedBytes"] <= 0
                or measurement["dataAllocatedBytes"]
                > measurement["payloadAllocatedBytes"]
                or measurement["filesystemOverheadBytes"] != (
                    measurement["payloadAllocatedBytes"]
                    - measurement["dataAllocatedBytes"]
                )
                or measurement["declaredPayloadBytes"] != (
                    storage["packageInstalledBytes"]
                    + storage["moduleInstalledBytes"]
                )
                or storage["rootLogicalRequiredBytes"] != (
                    measurement["declaredPayloadBytes"]
                    + storage["compressionReserveBytes"]
                )
                or storage["rootConservativeRequiredBytes"] != (
                    max(
                        0,
                        storage["packageInstalledBytes"]
                        - storage["packageReplacedBytes"],
                    )
                    + max(
                        0,
                        storage["moduleInstalledBytes"]
                        - storage["moduleReplacedBytes"],
                    )
                    + storage["compressionReserveBytes"]
                )
                or storage["replacementCandidateLogicalBytes"] != (
                    storage["packageReplacedBytes"]
                    + storage["moduleReplacedBytes"]
                )
                or storage["rootRequiredBytes"] != storage["rootMeasuredRequiredBytes"]
                or storage["measuredPayloadAllocatedBytes"]
                != measurement.get("payloadAllocatedBytes")
                or storage["compressionPayloadAllocatedBytes"]
                != measurement.get("payloadAllocatedBytes")
                or storage["replacementCreditBytes"]
                > storage["measuredPayloadAllocatedBytes"]
                or storage["replacementCreditBytes"] != (
                    storage["packageNoopCreditBytes"]
                    + storage["moduleNoopCreditBytes"]
                )
                or storage["packageNoopCreditBytes"] > sum(
                    item["allocatedBytes"] for item in package_measurements
                )
                or storage["moduleNoopCreditBytes"] != (
                    measurement.get("moduleAllocatedBytes", 0)
                    if compression["modulePayloadNoop"] else 0
                )
                or storage["compressionReserveBytes"] != (
                    storage["initramfsReserveBytes"]
                    + storage["compressionSafetyReserveBytes"]
                )
                or storage["compressionSafetyReserveBytes"]
                != ROOT_METADATA_RESERVE_BYTES
                or storage["rootMeasuredRequiredBytes"] != (
                    storage["measuredPayloadAllocatedBytes"]
                    - storage["replacementCreditBytes"]
                    + storage["compressionReserveBytes"]
                )
                or storage.get("rootFinalMarginBytes") != (
                    storage["rootAvailableBytes"] - storage["rootRequiredBytes"]
                )
                or not isinstance(storage.get("rootFinalMarginBytes"), int)
                or isinstance(storage.get("rootFinalMarginBytes"), bool)
                or storage["rootShortfallBytes"] != max(
                    0, -storage["rootFinalMarginBytes"]
                )
                or compression["compressionRatio"] != (
                    f"{ratio_millionths // 1_000_000}."
                    f"{ratio_millionths % 1_000_000:06d}"
                )
                or compression["admissionAuthorized"]
                != all(storage[f"{name}RequiredBytes"] <= storage[f"{name}AvailableBytes"]
                       for name in ("root", "var", "efi"))):
            raise SystemExit("Verified Btrfs measurement metadata is inconsistent.")
    elif (compression.get("admissionBasis") != "logical-uncompressed-conservative"
          or compression.get("compressionSavingsCreditedBytes") != 0
          or compression.get("declaredSizesLikelyConservative") is not (
              compression["enabled"]
              and compression["packageArchiveBytes"]
              < compression["declaredPackageBytes"]
          )
          or storage["rootRequiredBytes"] != (
              max(0, storage["packageInstalledBytes"] - storage["packageReplacedBytes"])
              + max(0, storage["moduleInstalledBytes"] - storage["moduleReplacedBytes"])
              + storage["initramfsReserveBytes"]
              + ROOT_METADATA_RESERVE_BYTES
          )
          or compression.get("pacmanCheckSpaceBypassAuthorized") is not False
          or compression.get("pacmanCheckSpacePolicy") != "preserve"):
        raise SystemExit("Verified conservative compression metadata is inconsistent.")


def validate_measurement_failure(detail):
    if (not isinstance(detail, dict)
            or set(detail) != {"phase", "command", "exitStatus", "stderr"}
            or detail.get("phase") not in MEASUREMENT_PHASES
            or detail.get("command") not in MEASUREMENT_COMMANDS
            or (detail.get("exitStatus") is not None
                and (not isinstance(detail["exitStatus"], int)
                     or isinstance(detail["exitStatus"], bool)
                     or not -255 <= detail["exitStatus"] <= 255))
            or (detail.get("stderr") is not None
                and (not isinstance(detail["stderr"], str)
                     or len(detail["stderr"]) > 512
                     or any(not 32 <= ord(character) < 127
                            for character in detail["stderr"])))):
        raise SystemExit("Measurement failure diagnostics are malformed or excessive.")


def validate_module_verification_binding(validated_modules, module_verification):
    """Require verification expectations to come from authenticated validation."""
    if validated_modules is None:
        return
    expected_hashes = {
        module["name"]: module["payloadSha256"]
        for module in validated_modules
    }
    records = (
        module_verification.get("modules")
        or module_verification.get("moduleMismatches") or []
    )
    for record in records:
        name = record["moduleName"]
        if (name in EXPECTED_MODULES
                and record["expectedPayloadSha256"] != expected_hashes.get(name)):
            raise SystemExit(
                "Module verification does not match validated module payloads."
            )


def main():
    args = parse_args()
    artifact_names = (
        args.archive,
        args.provenance,
        args.nvidia_utils,
        args.lib32_nvidia_utils,
    )
    if not all(plain_name(value) for value in artifact_names):
        raise SystemExit("Installation results may contain filenames, never host paths.")
    if not TOKEN.fullmatch(args.reason) or not TOKEN.fullmatch(args.phase):
        raise SystemExit("Installation result reason and phase must be stable tokens.")
    if not bounded_message(args.message):
        raise SystemExit("Installation result message is empty, excessive, or contains control data.")
    if args.root != "/target-root":
        raise SystemExit("Installation results must use the logical /target-root identity.")
    if args.status == "success" or args.status == "validated":
        if args.kernel == "unknown" or not KERNEL.fullmatch(args.kernel):
            raise SystemExit("Installation result kernel identity is invalid.")
        if (args.steamos == "unknown" or args.nvidia == "unknown"
                or not VERSION.fullmatch(args.steamos)
                or not VERSION.fullmatch(args.nvidia)):
            raise SystemExit("Installation result version identity is invalid.")
    else:
        args.kernel = args.kernel if KERNEL.fullmatch(args.kernel) else "invalid"
        args.steamos = args.steamos if VERSION.fullmatch(args.steamos) else "unknown"
        args.nvidia = args.nvidia if VERSION.fullmatch(args.nvidia) else "unknown"
    if args.trust not in TRUST_VALUES:
        raise SystemExit("Installation result trust classification is invalid.")
    if args.status in {"success", "validated"} and (
            args.trust == "pending-validation"
            or any(not value for value in artifact_names)):
        raise SystemExit(
            "Successful validation and installation results require exact trusted inputs."
        )
    if (not 0 <= args.runtime_mounts_expected <= 4
            or not 0 <= args.runtime_mounts_released <= args.runtime_mounts_expected):
        raise SystemExit("Installation result runtime mount counts are invalid.")
    if args.status == "success" and (
        args.mounts_released != "true"
        or args.compression_policy_restored != "true"
        or args.runtime_mounts_expected != 4
        or args.runtime_mounts_released != 4
    ):
        raise SystemExit("A successful installation result requires complete cleanup.")
    expected_terminal = {
        "success": ("install_complete", "complete"),
        "validated": ("validation_complete", "validated"),
        "cancelled": ("cancelled", None),
    }
    if args.status in expected_terminal:
        reason, phase = expected_terminal[args.status]
        if args.reason != reason or (phase is not None and args.phase != phase):
            raise SystemExit("Installation result terminal status is internally inconsistent.")

    document = {
        "schemaVersion": 1,
        "status": args.status,
        "reason": args.reason,
        "message": args.message,
        "phase": args.phase,
        "trust": args.trust,
        "target": {
            "root": args.root,
            "steamosVersion": args.steamos,
            "kernelVersion": args.kernel,
            "nvidiaVersion": args.nvidia,
            "architecture": "x86_64",
        },
        "inputs": {
            "archive": args.archive or None,
            "provenance": args.provenance or None,
            "nvidiaUtils": args.nvidia_utils or None,
            "lib32NvidiaUtils": args.lib32_nvidia_utils or None,
        },
        "cleanup": {
            "mountsReleased": args.mounts_released == "true",
            "runtimeMountsExpected": args.runtime_mounts_expected,
            "runtimeMountsReleased": args.runtime_mounts_released,
            "compressionPolicyRestored": (
                args.compression_policy_restored == "true"
            ),
        },
    }
    if args.validation:
        validation = load_validation(args.validation)
        if validation.get("status") == "verified":
            validate_verified_metadata(validation)
            document["validation"] = {
                "inputSource": validation["inputSource"],
                "archiveSha256": validation["archiveSha256"],
                "provenanceSha256": validation["provenanceSha256"],
                "userspaceLock": validation["userspaceLock"],
                "pacmanDatabase": validation["pacmanDatabase"],
                "boot": validation["boot"],
                "keyring": validation["keyring"],
                "packages": validation["packages"],
                "modules": validation["modules"],
                "storage": validation["storage"],
                "packageDependencyClosure": validation["packageDependencyClosure"],
                "compression": validation["compression"],
                "gamingPayload": validation["gamingPayload"],
            }
        elif args.status == "failed" and validation.get("status") == "failed":
            if "measurementFailure" in validation:
                validate_measurement_failure(validation["measurementFailure"])
            failure_validation = {
                key: validation[key]
                for key in (
                    "storage", "packageDependencyClosure", "compression",
                    "missingDependencies", "dependencyRequestedBy",
                    "packageName", "signerFingerprint",
                    "missingPackages", "unexpectedPackages",
                    "duplicatePackages", "packageMismatches",
                    "packageRecord", "invalidFields",
                    "measurementFailure",
                )
                if key in validation
            }
            if failure_validation:
                document["validation"] = failure_validation
        else:
            raise SystemExit("Result validation metadata does not match result status.")
    if args.module_verification:
        module_verification = load_module_verification(args.module_verification)
        if (module_verification["status"] == "failed"
                and (args.status != "failed" or args.phase != "module_install")):
            raise SystemExit(
                "Failed module verification metadata does not match result phase."
            )
        validate_module_verification_binding(
            document.get("validation", {}).get("modules"), module_verification
        )
        document["moduleVerification"] = module_verification
    elif args.status == "success":
        raise SystemExit(
            "A successful installation requires exact five-module verification metadata."
        )
    if args.userspace_verification:
        userspace_verification = load_userspace_verification(
            args.userspace_verification
        )
        expected_packages = {
            package["name"]: (package["fullVersion"], package["sha256"])
            for package in document.get("validation", {}).get("packages", [])
        }
        actual_packages = {
            package["packageName"]: (
                package["version"], package["packageSha256"]
            )
            for package in userspace_verification["packages"]
        }
        if (actual_packages != expected_packages
                or userspace_verification["pacmanDatabase"]["path"]
                != document.get("validation", {}).get("pacmanDatabase", {}).get("path")
                or userspace_verification["gspFirmware"]["version"]
                != args.nvidia):
            raise SystemExit(
                "Userspace verification does not match validated installation metadata."
            )
        document["userspaceVerification"] = userspace_verification
    elif args.status == "success":
        raise SystemExit(
            "A successful installation requires exact userspace verification metadata."
        )
    if args.initramfs_workspace:
        workspace = load_initramfs_workspace(args.initramfs_workspace)
        if (args.status == "success"
                and (workspace["status"] != "verified"
                     or workspace["phase"] != "mounted_workspace")):
            raise SystemExit(
                "A successful installation requires a verified mounted initramfs workspace."
            )
        if (workspace["status"] == "failed"
                and (args.status != "failed"
                     or not (
                         args.reason == "initramfs_workspace_unavailable"
                         or (args.reason == "mutation_cleanup_failed"
                             and args.phase == "cleanup")
                     ))):
            raise SystemExit("Failed workspace metadata does not match result status.")
        if (workspace["status"] == "preparation-required"
                and args.status != "validated"):
            raise SystemExit(
                "Workspace preparation metadata is valid only for validation results."
            )
        document["initramfsWorkspace"] = workspace
    elif args.status == "success":
        raise SystemExit("A successful installation requires workspace verification metadata.")
    if args.initramfs_verification:
        initramfs_verification = load_initramfs_verification(args.initramfs_verification)
        if (args.status != "success"
                or initramfs_verification["kernelVersion"] != args.kernel):
            raise SystemExit("Initramfs verification metadata does not match the result.")
        document["initramfsVerification"] = initramfs_verification
    elif args.status == "success":
        raise SystemExit("A successful installation requires exact initramfs verification metadata.")
    if args.payload_receipt:
        payload_receipt = load_payload_receipt(args.payload_receipt)
        receipt_target = payload_receipt["target"]
        if (args.status != "success"
                or receipt_target.get("steamosVersion") != args.steamos
                or receipt_target.get("kernelVersion") != args.kernel
                or receipt_target.get("nvidiaVersion") != args.nvidia
                or receipt_target.get("architecture") != "x86_64"):
            raise SystemExit("Payload receipt verification does not match the result.")
        document["payloadReceipt"] = payload_receipt
    elif args.status == "success":
        raise SystemExit(
            "A successful installation requires a verified rootfs payload receipt."
        )
    if args.target_execution_failure:
        if (args.status != "failed" or args.reason != "target_execution_trust"
                or args.phase != "target_execution_trust"):
            raise SystemExit(
                "Target execution failure metadata does not match the result."
            )
        document["targetExecutionFailure"] = load_target_execution_failure(
            args.target_execution_failure
        )
    atomic_write_bytes(
        args.output,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


if __name__ == "__main__":
    main()
