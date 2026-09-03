#!/usr/bin/env python3
"""Compatibility and hostile-input tests for the consumer contract validator."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_install_contract.py"
sys.path.insert(0, str(ROOT / "lib"))
from validate_install_contract import read_bounded_regular  # noqa: E402
from write_install_result import validate_module_verification_binding  # noqa: E402


def run(result, progress):
    return subprocess.run([str(VALIDATOR), "--result", str(result), "--progress", str(progress)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    with tempfile.TemporaryDirectory(prefix="install-contract-") as temporary:
        root = Path(temporary)
        result = root / "result.json"
        progress = root / "progress.log"
        document = {
            "schemaVersion": 1, "status": "success", "reason": "install_complete",
            "message": "complete", "phase": "complete", "trust": "locally-built-verified",
            "target": {
                "root": "/target-root", "steamosVersion": "3.8.14",
                "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gabc",
                "nvidiaVersion": "575.64.05", "architecture": "x86_64",
            },
            "inputs": {
                "archive": "modules.tar.gz", "provenance": "provenance.json",
                "nvidiaUtils": "nvidia-utils.pkg.tar.zst",
                "lib32NvidiaUtils": "lib32-nvidia-utils.pkg.tar.zst",
                "futureAdditiveInput": {"accepted": True},
            },
            "cleanup": {"mountsReleased": True, "runtimeMountsExpected": 4,
                        "runtimeMountsReleased": 4, "compressionPolicyRestored": True},
            "validation": {"status": "verified", "provenanceSha256": "b" * 64,
                           "userspaceLock": {"sha256": "c" * 64}, "packages": [
                {"name": "nvidia-utils", "filename": "nvidia-utils.pkg.tar.zst",
                 "fullVersion": "575.64.05-2", "sha256": "1" * 64,
                 "dependencies": ["glibc"], "provides": ["vulkan-driver"]},
                {"name": "lib32-nvidia-utils", "filename": "lib32-nvidia-utils.pkg.tar.zst",
                 "fullVersion": "575.64.05-1", "sha256": "2" * 64,
                 "dependencies": ["lib32-glibc"], "provides": []},
            ], "modules": [
                {"name": name, "payloadSha256": str(index) * 64}
                for index, name in enumerate(sorted({
                    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
                    "nvidia-peermem.ko", "nvidia-uvm.ko",
                }), 1)
            ]},
            "moduleVerification": {
                "schemaVersion": 1, "status": "verified",
                "reason": "installed_modules_verified",
                "modules": [{
                    "moduleName": name, "representation": ".ko.zst",
                    "targetRelativePath": (
                        "usr/lib/modules/6.16.12-valve24.4-1-neptune-616-gabc/"
                        f"updates/open-gpu-kernel-modules-steamos/{name}.zst"
                    ),
                    "expectedPayloadSha256": str(index) * 64,
                    "actualPayloadSha256": str(index) * 64,
                    "expectedMode": "0644", "actualMode": "0644",
                    "expectedUid": 0, "actualUid": 0,
                    "expectedGid": 0, "actualGid": 0,
                    "compressedSizeBytes": 1,
                    "invalidFields": [], "decompressionStatus": "verified",
                } for index, name in enumerate(sorted({
                    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
                    "nvidia-peermem.ko", "nvidia-uvm.ko",
                }), 1)],
            },
            "userspaceVerification": {
                "schemaVersion": 1, "status": "verified",
                "reason": "installed_userspace_verified",
                "validationBinding": {"userspaceLockSha256": "c" * 64,
                                      "provenanceSha256": "b" * 64},
                "packages": [{
                    "packageName": "nvidia-utils",
                    "packageFilename": "nvidia-utils.pkg.tar.zst",
                    "version": "575.64.05-2", "dependencies": ["glibc"],
                    "provides": ["vulkan-driver"],
                    "packageSha256": "1" * 64, "packageQueryVerified": True,
                    "pacmanIntegrityVerified": True, "payloadVerified": True,
                    "payloadPathsConfined": True, "payloadHashesVerified": True,
                    "payloadModesVerified": True, "payloadOwnershipVerified": True,
                    "payloadLinksVerified": True,
                    "directories": 1, "regularFiles": 1, "symlinks": 0,
                    "hardlinks": 0, "sharedLibraries": 1,
                }, {
                    "packageName": "lib32-nvidia-utils",
                    "packageFilename": "lib32-nvidia-utils.pkg.tar.zst",
                    "version": "575.64.05-1", "dependencies": ["lib32-glibc"],
                    "provides": [],
                    "packageSha256": "2" * 64, "packageQueryVerified": True,
                    "pacmanIntegrityVerified": True, "payloadVerified": True,
                    "payloadPathsConfined": True, "payloadHashesVerified": True,
                    "payloadModesVerified": True, "payloadOwnershipVerified": True,
                    "payloadLinksVerified": True,
                    "directories": 1, "regularFiles": 1, "symlinks": 0,
                    "hardlinks": 0, "sharedLibraries": 1,
                }],
                "pacmanDatabase": {"path": "/usr/lib/holo/pacmandb",
                                   "status": "verified", "consistencyVerified": True,
                                   "verifiedPackageCount": 2},
                "gspFirmware": {"status": "verified", "version": "575.64.05",
                                "targetRelativeFiles": ["usr/lib/firmware/nvidia/575.64.05/gsp.bin"]},
            },
            "initramfsWorkspace": {
                "schemaVersion": 1, "status": "verified",
                "reason": "initramfs_workspace_available",
                "phase": "mounted_workspace", "condition": "available", "mode": "1777",
            },
            "payloadReceipt": {
                "schemaVersion": 1, "status": "verified",
                "reason": "payload_receipt_verified", "target": {
                    "steamosVersion": "3.8.14",
                    "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gabc",
                    "nvidiaVersion": "575.64.05", "architecture": "x86_64",
                },
                "receiptId": "8" * 64,
                "rootfsRelativePath": (
                    "usr/lib/open-gpu-kernel-modules-steamos-support/"
                    "offline-install/receipt.json"
                ),
                "records": [{
                    "role": role, "filename": f"{role}.json",
                    "sizeBytes": 1, "sha256": str(index) * 64,
                } for index, role in enumerate((
                    "buildInfo", "provenance", "validation", "moduleVerification",
                    "userspaceVerification", "initramfsVerification",
                ), 1)],
            },
            "initramfsVerification": {
                "schemaVersion": 1, "status": "verified",
                "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gabc",
                "tools": {
                    "mkinitcpio": {"path": "/usr/bin/mkinitcpio", "sizeBytes": 1,
                                   "sha256": "3" * 64},
                    "lsinitcpio": {"path": "/usr/bin/lsinitcpio", "sizeBytes": 1,
                                   "sha256": "4" * 64},
                },
                "config": {
                    "path": "/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
                    "sizeBytes": 1, "sha256": "5" * 64,
                },
                "requiredModules": [
                    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
                ],
                "rootfsOnlyModules": ["nvidia-peermem.ko"],
                "images": [{
                    "filename": "initramfs-linux-neptune.img", "sizeBytes": 1,
                    "sha256": "6" * 64, "listingSha256": "7" * 64, "entries": 1,
                    "configPath": "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf",
                    "modules": {
                    "nvidia.ko": "usr/lib/modules/6.16.12-valve24.4-1-neptune-616-gabc/nvidia.ko.zst",
                    "nvidia-modeset.ko": "usr/lib/modules/6.16.12-valve24.4-1-neptune-616-gabc/nvidia-modeset.ko.zst",
                    "nvidia-uvm.ko": "usr/lib/modules/6.16.12-valve24.4-1-neptune-616-gabc/nvidia-uvm.ko.zst",
                    "nvidia-drm.ko": "usr/lib/modules/6.16.12-valve24.4-1-neptune-616-gabc/nvidia-drm.ko.zst",
                }}],
            },
            "futureAdditiveField": {"accepted": True},
        }
        result.write_text(json.dumps(document))
        progress.write_text(
            'noise\nSTEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":7,"phase":"modules","indeterminate":false,"unit":"items","completed":0,"total":5,"future":true}\n'
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":7,"phase":"modules","indeterminate":false,"unit":"items","completed":5,"total":5}\n'
        )
        completed = run(result, progress)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["progressRecords"] == 2

        validate_module_verification_binding(
            document["validation"]["modules"], document["moduleVerification"]
        )
        unbound_modules = json.loads(json.dumps(document["moduleVerification"]))
        unbound_modules["modules"][0]["expectedPayloadSha256"] = "f" * 64
        unbound_modules["modules"][0]["actualPayloadSha256"] = "f" * 64
        try:
            validate_module_verification_binding(
                document["validation"]["modules"], unbound_modules
            )
        except SystemExit as error:
            assert "validated module payloads" in str(error)
        else:
            raise AssertionError("unbound module verification hashes were accepted")

        for mutate in (
            lambda value: value["cleanup"].update(runtimeMountsReleased=3),
            lambda value: value.pop("moduleVerification"),
            lambda value: value.pop("payloadReceipt"),
            lambda value: value["payloadReceipt"].update(receiptId="invalid"),
            lambda value: value["payloadReceipt"]["target"].update(
                kernelVersion="wrong-kernel"
            ),
            lambda value: value["moduleVerification"].update(modules=[]),
            lambda value: value["userspaceVerification"].update(packages=[]),
            lambda value: value["userspaceVerification"]["pacmanDatabase"].update(
                consistencyVerified=False
            ),
            lambda value: value["moduleVerification"]["modules"][0].update(
                moduleName=[]
            ),
            lambda value: value["moduleVerification"]["modules"][0].update(
                actualPayloadSha256="f" * 64
            ),
            lambda value: value["validation"]["modules"][0].update(
                payloadSha256="f" * 64
            ),
            lambda value: value["moduleVerification"]["modules"][0].update(
                targetRelativePath="../nvidia.ko.zst"
            ),
            lambda value: value["userspaceVerification"]["packages"][0].update(
                packageName=[]
            ),
            lambda value: value["userspaceVerification"]["packages"][0].update(
                version="575.64.05-99"
            ),
            lambda value: value["initramfsVerification"]["requiredModules"].append(
                "nvidia-peermem.ko"
            ),
            lambda value: value["initramfsVerification"].update(
                rootfsOnlyModules=[]
            ),
            lambda value: value["initramfsVerification"]["images"][0][
                "modules"
            ].pop("nvidia-drm.ko"),
            lambda value: value["initramfsVerification"].update(
                kernelVersion="wrong-kernel"
            ),
            lambda value: value["initramfsVerification"]["tools"].pop(
                "mkinitcpio"
            ),
            lambda value: value["initramfsVerification"]["images"][0].update(
                sha256="not-a-hash"
            ),
            lambda value: value.update(schemaVersion=2),
        ):
            broken = json.loads(json.dumps(document))
            mutate(broken)
            result.write_text(json.dumps(broken))
            assert run(result, progress).returncode != 0
        result.write_text(json.dumps(document))

        hostile_streams = (
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"schemaVersion":1,"attempt":0,"phase":"x","indeterminate":true}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":0,"phase":"x","indeterminate":false,"unit":"items","completed":2,"total":1}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":0,"phase":"x","indeterminate":false,"unit":"items","completed":1,"total":2}\nSTEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":0,"phase":"x","indeterminate":false,"unit":"items","completed":0,"total":2}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":1,"phase":"x","indeterminate":true,"completed":0,"total":1,"unit":"items"}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":1,"phase":"x","indeterminate":false,"unit":"items","completed":0,"total":0}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":2,"phase":"x","indeterminate":true}\nSTEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":1,"phase":"y","indeterminate":true}\n',
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":0,"phase":"x","indeterminate":true,"future":NaN}\n',
        )
        for stream in hostile_streams:
            progress.write_text(stream)
            assert run(result, progress).returncode != 0

        progress.write_text(
            'STEAMOS_NVIDIA_PROGRESS {"schemaVersion":1,"attempt":7,"phase":"modules","indeterminate":true}\n'
        )
        for mutate in (
            lambda value: value.update(trust="unknown"),
            lambda value: value["target"].update(root="/host/path"),
            lambda value: value["inputs"].update(archive="../archive.tar.gz"),
            lambda value: value.update(reason="wrong_success_reason"),
            lambda value: value.update(message="unsafe\x00message"),
            lambda value: value.update(trust="pending-validation"),
            lambda value: value["target"].update(kernelVersion="unknown"),
            lambda value: value["inputs"].update(provenance=None),
        ):
            broken = json.loads(json.dumps(document))
            mutate(broken)
            result.write_text(json.dumps(broken))
            assert run(result, progress).returncode != 0

        result.write_text(json.dumps(document).replace('"schemaVersion": 1', '"schemaVersion": NaN', 1))
        assert run(result, progress).returncode != 0

        raced = root / "raced-result.json"
        raced.write_text(json.dumps(document))
        replacement = b'{"foreign":"replacement"}\n'
        real_read = os.read
        replaced = False

        def replace_path_after_read(descriptor, length):
            nonlocal replaced
            chunk = real_read(descriptor, length)
            if chunk and not replaced:
                replaced = True
                raced.unlink()
                raced.write_bytes(replacement)
            return chunk

        with mock.patch("validate_install_contract.os.read", replace_path_after_read):
            try:
                read_bounded_regular(raced, 32 * 1024 * 1024)
            except ValueError as error:
                assert "changed while it was being read" in str(error)
            else:
                raise AssertionError("contract reader accepted a replaced result")
        assert raced.read_bytes() == replacement


if __name__ == "__main__":
    main()
