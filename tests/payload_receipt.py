#!/usr/bin/env python3
"""Rootfs payload-receipt contract, corruption, and idempotency tests."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import payload_receipt  # noqa: E402

HELPER = ROOT / "lib/payload_receipt.py"
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfixture"
TARGET = {
    "steamosVersion": "3.8.14",
    "kernelVersion": KERNEL,
    "nvidiaVersion": "575.64.05",
    "architecture": "x86_64",
}
MODULES = (
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
)
RECEIPT = Path(
    "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install"
)


def write_json(path, document):
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def invoke(operation, target_root, evidence, output):
    command = [sys.executable, str(HELPER), operation, "--root", str(target_root)]
    if operation == "commit":
        for option, name in (
            ("--build-info", "build-info"),
            ("--provenance", "provenance"),
            ("--validation", "validation"),
            ("--module-verification", "modules"),
            ("--userspace-verification", "userspace"),
            ("--initramfs-verification", "initramfs"),
        ):
            command.extend((option, str(evidence[name])))
    command.extend(("--output", str(output)))
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    with tempfile.TemporaryDirectory(prefix="payload-receipt-") as temporary:
        root = Path(temporary)
        target_root = root / "target"
        (target_root / "usr/lib").mkdir(parents=True)
        evidence_root = root / "evidence"
        evidence_root.mkdir()
        evidence = {name: evidence_root / filename for name, filename in (
            ("build-info", "BUILD-INFO.txt"),
            ("provenance", "PROVENANCE.json"),
            ("validation", "validation.json"),
            ("modules", "modules.json"),
            ("userspace", "userspace.json"),
            ("initramfs", "initramfs.json"),
        )}
        evidence["build-info"].write_text("fixture=1\n", encoding="utf-8")
        write_json(evidence["provenance"], {
            "schemaVersion": 1, "target": TARGET, "trust": "locally-built-verified",
        })
        write_json(evidence["validation"], {
            "schemaVersion": 1, "status": "verified", "target": TARGET,
        })
        write_json(evidence["modules"], {
            "schemaVersion": 1, "status": "verified",
            "modules": [{"moduleName": name} for name in MODULES],
        })
        write_json(evidence["userspace"], {
            "schemaVersion": 1, "status": "verified",
            "packages": [{"packageName": "nvidia-utils"}],
        })
        write_json(evidence["initramfs"], {
            "schemaVersion": 1, "status": "verified", "kernelVersion": KERNEL,
        })

        committed_output = root / "committed.json"
        committed = invoke("commit", target_root, evidence, committed_output)
        assert committed.returncode == 0, committed.stderr
        first = json.loads(committed_output.read_text(encoding="utf-8"))
        assert first["status"] == "verified"
        assert first["target"] == TARGET
        assert len(first["records"]) == 6
        assert first["rootfsRelativePath"] == str(RECEIPT / "receipt.json")

        verified_output = root / "verified.json"
        verified = invoke("verify", target_root, evidence, verified_output)
        assert verified.returncode == 0, verified.stderr
        assert json.loads(verified_output.read_text())["receiptId"] == first["receiptId"]

        repeat_output = root / "repeat.json"
        repeat = invoke("commit", target_root, evidence, repeat_output)
        assert repeat.returncode == 0, repeat.stderr
        assert json.loads(repeat_output.read_text())["receiptId"] == first["receiptId"]

        receipt_root = target_root / RECEIPT
        validation = receipt_root / "validation.json"
        validation.write_text("corrupt\n", encoding="utf-8")
        corrupt = invoke("verify", target_root, evidence, root / "corrupt.json")
        assert corrupt.returncode != 0
        assert "differs from its manifest" in corrupt.stderr
        assert not (root / "corrupt.json").exists()

        assert invoke("commit", target_root, evidence, root / "repaired.json").returncode == 0
        victim = root / "victim"
        victim.write_text("do not read\n", encoding="utf-8")
        validation.unlink()
        validation.symlink_to(victim)
        linked = invoke("verify", target_root, evidence, root / "linked.json")
        assert linked.returncode != 0
        assert not (root / "linked.json").exists()
        assert victim.read_text() == "do not read\n"

        assert invoke("commit", target_root, evidence, root / "repaired-again.json").returncode == 0
        validation.chmod(0o666)
        writable = invoke(
            "verify", target_root, evidence, root / "writable.json"
        )
        assert writable.returncode != 0
        assert not (root / "writable.json").exists()
        validation.chmod(0o644)

        linked_source = root / "linked-validation.json"
        linked_source.write_bytes(validation.read_bytes())
        validation.unlink()
        validation.hardlink_to(linked_source)
        hardlinked = invoke(
            "verify", target_root, evidence, root / "hardlinked.json"
        )
        assert hardlinked.returncode != 0
        assert not (root / "hardlinked.json").exists()

        assert invoke(
            "commit", target_root, evidence, root / "repaired-safe.json"
        ).returncode == 0
        displaced_receipt = receipt_root.with_name("offline-install-displaced")
        original_read_regular_at = payload_receipt.read_regular_at
        replaced_directory = False

        def replace_receipt_directory(descriptor, filename, *args, **kwargs):
            nonlocal replaced_directory
            payload = original_read_regular_at(
                descriptor, filename, *args, **kwargs
            )
            if filename == "receipt.json" and not replaced_directory:
                receipt_root.rename(displaced_receipt)
                receipt_root.mkdir(mode=0o755)
                replaced_directory = True
            return payload

        with mock.patch.object(
                payload_receipt, "read_regular_at", replace_receipt_directory):
            try:
                payload_receipt.verify_receipt(target_root)
            except ValueError as error:
                assert "directory changed" in str(error)
            else:
                raise AssertionError("replaced receipt directory was accepted")
        receipt_root.rmdir()
        displaced_receipt.rename(receipt_root)

        receipt_root.chmod(0o777)
        writable_directory = invoke(
            "verify", target_root, evidence, root / "writable-directory.json"
        )
        assert writable_directory.returncode != 0
        assert not (root / "writable-directory.json").exists()
        receipt_root.chmod(0o755)

        (receipt_root / "receipt.json").unlink()
        partial = invoke("verify", target_root, evidence, root / "partial.json")
        assert partial.returncode != 0
        assert not (root / "partial.json").exists()

        mismatched = json.loads(evidence["initramfs"].read_text())
        mismatched["kernelVersion"] = "different-kernel"
        write_json(evidence["initramfs"], mismatched)
        mismatch = invoke("commit", target_root, evidence, root / "mismatch.json")
        assert mismatch.returncode != 0
        assert "initramfs verification is inconsistent" in mismatch.stderr
        assert not (root / "mismatch.json").exists()
        mismatched["kernelVersion"] = KERNEL
        write_json(evidence["initramfs"], mismatched)

        unsafe_root = root / "unsafe-target"
        unsafe_root.mkdir()
        (unsafe_root / "usr").symlink_to(target_root / "usr", target_is_directory=True)
        unsafe = invoke("commit", unsafe_root, evidence, root / "unsafe.json")
        assert unsafe.returncode != 0
        assert "path is unsafe" in unsafe.stderr


if __name__ == "__main__":
    main()
