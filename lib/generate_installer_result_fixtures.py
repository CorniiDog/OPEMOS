#!/usr/bin/env python3
"""Emit the canonical bounded installer-result schema-1 compatibility matrix."""

import copy
import json
import sys


MAX_OUTPUT_BYTES = 512 * 1024
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfe145653a794"
NVIDIA = "575.64.05"
MODULES = (
    "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko",
    "nvidia-uvm.ko", "nvidia.ko",
)
PACKAGE_SPECS = (
    ("egl-gbm", "1.1.3", "1", "x86_64", "A" * 40, ["eglexternalplatform", "glibc", "mesa", "libdrm"], ["libnvidia-egl-gbm.so=1"]),
    ("egl-wayland", "4:1.1.19", "1", "x86_64", "B" * 40, ["glibc", "libegl", "wayland"], ["libnvidia-egl-wayland.so=1"]),
    ("egl-x11", "1.0.3", "1", "x86_64", "C" * 40, ["eglexternalplatform", "libx11"], ["libnvidia-egl-x11.so=1"]),
    ("eglexternalplatform", "1.2.1", "1", "any", "83BC8889351B5DEBBB68416EB8AC08600F108CDF", [], ["eglexternalplatform"]),
    ("lib32-nvidia-utils", NVIDIA, "1", "x86_64", "D2E95FEC015CF1F911AAAB0C3D4C5008BB5C8D29", ["lib32-glibc", f"nvidia-utils={NVIDIA}"], []),
    ("nvidia-utils", NVIDIA, "2", "x86_64", "05C7775A9E8B977407FE08E69D4C5AA15426DA0A", ["egl-gbm", "egl-wayland", "egl-x11"], ["vulkan-driver", "opengl-driver"]),
)


def package_records():
    records = []
    for index, (name, pkgver, pkgrel, architecture, signer, dependencies, provides) in enumerate(PACKAGE_SPECS, 1):
        full_version = f"{pkgver}-{pkgrel}"
        archive_version = f"{pkgver.split(':', 1)[-1]}-{pkgrel}"
        filename = f"{name}-{archive_version}-{architecture}.pkg.tar.zst"
        records.append({
            "name": name,
            "role": "nvidia-userspace" if name in {"nvidia-utils", "lib32-nvidia-utils"} else "dependency",
            "filename": filename,
            "signatureFilename": f"{filename}.sig",
            "fullVersion": full_version,
            "pkgver": pkgver,
            "pkgrel": pkgrel,
            "architecture": architecture,
            "signer": signer,
            "sha256": f"{index:x}" * 64,
            "signatureSha256": f"{index + 6:x}" * 64,
            "installedSize": 1_000_000 * index,
            "dependencies": dependencies,
            "provides": provides,
        })
    return records


def envelope(status, reason, phase):
    return {
        "schemaVersion": 1,
        "status": status,
        "reason": reason,
        "message": "Human-readable fixture wording is intentionally not frozen.",
        "phase": phase,
        "trust": "locally-built-verified",
        "target": {
            "root": "/target-root", "steamosVersion": "3.8.14",
            "kernelVersion": KERNEL, "nvidiaVersion": NVIDIA,
            "architecture": "x86_64",
        },
        "inputs": {
            "archive": "modules.tar.gz", "provenance": "provenance.json",
            "nvidiaUtils": "nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst",
            "lib32NvidiaUtils": "lib32-nvidia-utils-575.64.05-1-x86_64.pkg.tar.zst",
        },
        "cleanup": {
            "mountsReleased": True, "runtimeMountsExpected": 4,
            "runtimeMountsReleased": 4, "compressionPolicyRestored": True,
        },
    }


def validation_proof():
    packages = package_records()
    package_installed = sum(package["installedSize"] for package in packages)
    module_installed = 25_000_000
    initramfs_reserve = 160_000_000
    return {
        "archiveSha256": "a" * 64,
        "provenanceSha256": "b" * 64,
        "userspaceLock": {"name": "userspace-lock-v1.json", "sha256": "c" * 64},
        "pacmanDatabase": {"path": "/usr/lib/holo/pacmandb", "packageCount": 1158},
        "boot": {
            "rootfsBootPath": "/boot",
            "efiMountPath": "/efi",
            "grubConfiguration": "/efi/EFI/steamos/grub.cfg",
            "requiredKernelArguments": ["nvidia-drm.modeset=1", "nvidia-drm.fbdev=1"],
        },
        "keyring": {"name": "approved-package-signers.gpg", "sha256": "d" * 64},
        "packages": packages,
        "packageDependencyClosure": [
            {"name": package["name"], "version": package["fullVersion"], "source": "incoming"}
            for package in packages
        ],
        "gamingPayload": {
            "schemaVersion": 1, "status": "not-requested",
            "profileId": "gaming-no-cuda-v1",
        },
        "compression": {
            "filesystem": "btrfs", "enabled": False, "options": [],
            "invalidOptions": [], "writeIncompatibleOptions": [],
            "admissionBasis": "logical-uncompressed-conservative",
            "compressionSavingsCreditedBytes": 0,
            "declaredPackageBytes": package_installed,
            "packageArchiveBytes": 8_000_000,
            "packageArchiveSavingsBytes": package_installed - 8_000_000,
            "declaredSizesLikelyConservative": True,
            "assessment": "logical-conservative-admission",
            "pacmanCheckSpaceBypassAuthorized": False,
            "pacmanCheckSpacePolicy": "preserve",
        },
        "storage": {
            "rootAvailableBytes": 1_000_000_000,
            "rootRequiredBytes": package_installed + module_installed + initramfs_reserve,
            "varAvailableBytes": 200_000_000, "varRequiredBytes": 16_000_000,
            "efiAvailableBytes": 100_000_000, "efiRequiredBytes": 2_000_000,
            "packageInstalledBytes": package_installed,
            "packageCompressedBytes": 8_000_000, "packageReplacedBytes": 0,
            "moduleInstalledBytes": module_installed, "moduleReplacedBytes": 0,
            "initramfsReserveBytes": initramfs_reserve,
        },
        "modules": [
            {"name": name, "payloadSha256": str(index) * 64}
            for index, name in enumerate(MODULES, 1)
        ],
    }


def module_verification():
    return {
        "schemaVersion": 1, "status": "verified",
        "reason": "installed_modules_verified",
        "modules": [{
            "moduleName": name, "representation": ".ko.zst",
            "targetRelativePath": (
                f"usr/lib/modules/{KERNEL}/updates/"
                f"open-gpu-kernel-modules-steamos/{name}.zst"
            ),
            "expectedPayloadSha256": str(index) * 64,
            "actualPayloadSha256": str(index) * 64,
            "expectedMode": "0644", "actualMode": "0644",
            "expectedUid": 0, "actualUid": 0,
            "expectedGid": 0, "actualGid": 0,
            "compressedSizeBytes": 1, "invalidFields": [],
            "decompressionStatus": "verified",
        } for index, name in enumerate(MODULES, 1)],
    }


def userspace_verification():
    packages = []
    for package in package_records():
        packages.append({
            "packageName": package["name"], "version": package["fullVersion"],
            "packageSha256": package["sha256"],
            "packageQueryVerified": True, "pacmanIntegrityVerified": True,
            "payloadVerified": True, "directories": 1, "regularFiles": 1,
            "symlinks": 0, "hardlinks": 0, "sharedLibraries": 1,
        })
    return {
        "schemaVersion": 1, "status": "verified",
        "reason": "installed_userspace_verified", "packages": packages,
        "pacmanDatabase": {
            "path": "/usr/lib/holo/pacmandb", "status": "verified",
            "consistencyVerified": True, "verifiedPackageCount": len(packages),
        },
        "gspFirmware": {
            "status": "verified", "version": NVIDIA,
            "targetRelativeFiles": [f"usr/lib/firmware/nvidia/{NVIDIA}/gsp.bin"],
        },
    }


def initramfs_verification():
    required = ["nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko"]
    return {
        "schemaVersion": 1, "status": "verified", "kernelVersion": KERNEL,
        "tools": {
            "mkinitcpio": {"path": "/usr/bin/mkinitcpio", "sizeBytes": 1, "sha256": "3" * 64},
            "lsinitcpio": {"path": "/usr/bin/lsinitcpio", "sizeBytes": 1, "sha256": "4" * 64},
        },
        "config": {
            "path": "/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
            "sizeBytes": 1, "sha256": "5" * 64,
        },
        "requiredModules": required, "rootfsOnlyModules": ["nvidia-peermem.ko"],
        "images": [{
            "filename": "initramfs-linux-neptune.img", "sizeBytes": 1,
            "sha256": "6" * 64, "listingSha256": "7" * 64, "entries": 1,
            "configPath": "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
            "modules": {
                name: f"usr/lib/modules/{KERNEL}/{name}.zst" for name in required
            },
        }],
    }


def payload_receipt():
    roles = (
        "buildInfo", "provenance", "validation", "moduleVerification",
        "userspaceVerification", "initramfsVerification",
    )
    return {
        "schemaVersion": 1, "status": "verified",
        "reason": "payload_receipt_verified",
        "target": {
            "steamosVersion": "3.8.14", "kernelVersion": KERNEL,
            "nvidiaVersion": NVIDIA, "architecture": "x86_64",
        },
        "receiptId": "8" * 64,
        "rootfsRelativePath": (
            "usr/lib/open-gpu-kernel-modules-steamos-support/"
            "offline-install/receipt.json"
        ),
        "records": [
            {"role": role, "filename": f"{role}.json", "sizeBytes": 1,
             "sha256": str(index) * 64}
            for index, role in enumerate(roles, 1)
        ],
    }


def success_document():
    document = envelope("success", "install_complete", "complete")
    document.update({
        "validation": validation_proof(),
        "moduleVerification": module_verification(),
        "userspaceVerification": userspace_verification(),
        "initramfsWorkspace": {
            "schemaVersion": 1, "status": "verified",
            "reason": "initramfs_workspace_available", "phase": "mounted_workspace",
            "condition": "available", "requiredBytes": 64_000_000,
            "requiredInodes": 4096, "availableBytes": 200_000_000,
            "availableInodes": 100_000, "inodeCapacityMode": "reported",
            "mode": "1777",
        },
        "initramfsVerification": initramfs_verification(),
        "payloadReceipt": payload_receipt(),
    })
    return document


def case(name, document, accepted):
    return {
        "name": name,
        "expected": {"accepted": accepted, **({"status": document["status"]} if accepted else {})},
        "document": document,
    }


def matrix():
    validated = envelope("validated", "validation_complete", "validated")
    validated["validation"] = validation_proof()
    validated["initramfsWorkspace"] = {
        "schemaVersion": 1, "status": "verified",
        "reason": "initramfs_workspace_available", "phase": "target_workspace",
        "condition": "available", "requiredBytes": 64_000_000,
        "requiredInodes": 4096, "availableBytes": 200_000_000,
        "availableInodes": 100_000, "inodeCapacityMode": "reported",
        "mode": "1777",
    }
    success = success_document()
    cases = [
        case("validated-success", validated, True),
        case("mutation-success", success, True),
    ]
    additive = copy.deepcopy(success)
    additive["futureAdditiveField"] = {"safe": True}
    additive["inputs"]["futureAdditiveInput"] = "accepted"
    cases.append(case("safe-additive-fields", additive, True))
    for name, field in (
        ("missing-module-verification", "moduleVerification"),
        ("missing-userspace-verification", "userspaceVerification"),
        ("missing-workspace-verification", "initramfsWorkspace"),
        ("missing-initramfs-verification", "initramfsVerification"),
        ("missing-payload-receipt", "payloadReceipt"),
    ):
        document = copy.deepcopy(success)
        document.pop(field)
        cases.append(case(name, document, False))
    target_mismatch = copy.deepcopy(success)
    target_mismatch["payloadReceipt"]["target"]["kernelVersion"] = "wrong-kernel"
    cases.append(case("target-proof-mismatch", target_mismatch, False))
    input_mismatch = copy.deepcopy(success)
    input_mismatch["inputs"]["archive"] = "../modules.tar.gz"
    cases.append(case("unsafe-input-identity", input_mismatch, False))
    cleanup_failure = copy.deepcopy(success)
    cleanup_failure["cleanup"]["runtimeMountsReleased"] = 3
    cases.append(case("cleanup-incomplete", cleanup_failure, False))
    cases.extend((
        {"name": "malformed-json", "expected": {"accepted": False},
         "rawDocument": "{\"schemaVersion\":1,"},
        {"name": "duplicate-json-key", "expected": {"accepted": False},
         "rawDocument": "{\"schemaVersion\":1,\"schemaVersion\":1}"},
    ))
    return {
        "schemaVersion": 1,
        "kind": "opemos-installer-result-compatibility-fixtures",
        "resultSchemaVersion": 1,
        "unfrozenFields": ["message"],
        "cases": cases,
    }


def main():
    payload = (json.dumps(matrix(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("installer-result compatibility matrix exceeds its size limit")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
