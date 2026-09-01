#!/usr/bin/env python3
"""Compatibility and hostile-input tests for the consumer contract validator."""

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_install_contract.py"


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
            "target": {"architecture": "x86_64"}, "inputs": {},
            "cleanup": {"mountsReleased": True, "runtimeMountsExpected": 4,
                        "runtimeMountsReleased": 4, "compressionPolicyRestored": True},
            "validation": {"status": "verified"},
            "moduleVerification": {"status": "verified"},
            "userspaceVerification": {"status": "verified"},
            "initramfsWorkspace": {"status": "verified", "phase": "mounted_workspace"},
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
        )
        for stream in hostile_streams:
            progress.write_text(stream)
            assert run(result, progress).returncode != 0


if __name__ == "__main__":
    main()
