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
            },
            "cleanup": {"mountsReleased": True, "runtimeMountsExpected": 4,
                        "runtimeMountsReleased": 4, "compressionPolicyRestored": True},
            "validation": {"status": "verified"},
            "moduleVerification": {"status": "verified"},
            "userspaceVerification": {"status": "verified"},
            "initramfsWorkspace": {"status": "verified", "phase": "mounted_workspace"},
            "initramfsVerification": {
                "status": "verified",
                "requiredModules": [
                    "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
                ],
                "rootfsOnlyModules": ["nvidia-peermem.ko"],
                "images": [{"modules": {
                    "nvidia.ko": "usr/lib/modules/kernel/nvidia.ko.zst",
                    "nvidia-modeset.ko": "usr/lib/modules/kernel/nvidia-modeset.ko.zst",
                    "nvidia-uvm.ko": "usr/lib/modules/kernel/nvidia-uvm.ko.zst",
                    "nvidia-drm.ko": "usr/lib/modules/kernel/nvidia-drm.ko.zst",
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

        for mutate in (
            lambda value: value["cleanup"].update(runtimeMountsReleased=3),
            lambda value: value.pop("moduleVerification"),
            lambda value: value["initramfsVerification"]["requiredModules"].append(
                "nvidia-peermem.ko"
            ),
            lambda value: value["initramfsVerification"].update(
                rootfsOnlyModules=[]
            ),
            lambda value: value["initramfsVerification"]["images"][0][
                "modules"
            ].pop("nvidia-drm.ko"),
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
