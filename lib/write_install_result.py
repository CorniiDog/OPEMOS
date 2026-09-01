#!/usr/bin/env python3
"""Atomically write the offline-root installation result contract."""

import argparse
import json
import os
import re
from pathlib import Path


MAX_VALIDATION_BYTES = 16 * 1024 * 1024
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


def validate_verified_metadata(validation):
    required = {
        "archiveSha256", "provenanceSha256", "userspaceLock",
        "pacmanDatabase", "boot", "keyring", "packages", "modules", "storage",
        "packageDependencyClosure", "compression", "gamingPayload",
    }
    if not required <= validation.keys():
        raise SystemExit("Verified installation metadata is incomplete.")
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
                         "policySha256", "omittedCapabilities",
                         "preservedCapabilities", "packageOwnership"}
          or not HEX_SHA256.fullmatch(gaming.get("sha256", ""))
          or not HEX_SHA256.fullmatch(gaming.get("policySha256", ""))
          or gaming.get("omittedCapabilities") != ["cuda-compute"]
          or set(gaming.get("preservedCapabilities", [])) != {
              "graphics", "vulkan", "glvnd-egl", "nvenc", "nvdec",
              "gsp-firmware", "gaming-32bit", "recovery-rendering",
          }
          or gaming.get("packageOwnership") != "archive-and-pacman-database-exact"):
        raise SystemExit("Verified gaming payload metadata is invalid.")
    storage = validation["storage"]
    compression = validation["compression"]
    base_storage = {
        "rootAvailableBytes", "rootRequiredBytes", "varAvailableBytes",
        "varRequiredBytes", "efiAvailableBytes", "efiRequiredBytes",
        "packageInstalledBytes", "packageCompressedBytes", "packageReplacedBytes",
        "moduleInstalledBytes", "moduleReplacedBytes", "initramfsReserveBytes",
    }
    if (not isinstance(storage, dict) or not base_storage <= storage.keys()
            or any(not isinstance(storage[field], int) or isinstance(storage[field], bool)
                   or storage[field] < 0 for field in base_storage)
            or not isinstance(compression, dict)):
        raise SystemExit("Verified installation storage metadata is invalid.")
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
                or not isinstance(compression.get("compressionRatio"), str)
                or re.fullmatch(r"[0-9]+\.[0-9]{6}", compression["compressionRatio"])
                is None
                or not measured_storage <= storage.keys()
                or any(not isinstance(storage[field], int)
                       or isinstance(storage[field], bool) or storage[field] < 0
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
          or compression.get("compressionSavingsCreditedBytes") != 0):
        raise SystemExit("Verified conservative compression metadata is inconsistent.")


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
        if not KERNEL.fullmatch(args.kernel):
            raise SystemExit("Installation result kernel identity is invalid.")
        if not VERSION.fullmatch(args.steamos) or not VERSION.fullmatch(args.nvidia):
            raise SystemExit("Installation result version identity is invalid.")
    else:
        args.kernel = args.kernel if KERNEL.fullmatch(args.kernel) else "invalid"
        args.steamos = args.steamos if VERSION.fullmatch(args.steamos) else "unknown"
        args.nvidia = args.nvidia if VERSION.fullmatch(args.nvidia) else "unknown"
    if args.trust not in TRUST_VALUES:
        raise SystemExit("Installation result trust classification is invalid.")
    if args.status == "success" and (
        args.mounts_released != "true"
        or args.compression_policy_restored != "true"
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
            failure_validation = {
                key: validation[key]
                for key in (
                    "storage", "packageDependencyClosure", "compression",
                    "missingDependencies", "dependencyRequestedBy",
                    "packageName", "signerFingerprint",
                    "missingPackages", "unexpectedPackages",
                    "duplicatePackages", "packageMismatches",
                    "packageRecord", "invalidFields",
                )
                if key in validation
            }
            if failure_validation:
                document["validation"] = failure_validation
        else:
            raise SystemExit("Result validation metadata does not match result status.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(args.output)


if __name__ == "__main__":
    main()
