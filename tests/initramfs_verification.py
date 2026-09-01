#!/usr/bin/env python3
"""Host contract regressions for exact initramfs verification metadata."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from write_install_result import load_initramfs_verification
VERIFIER = ROOT / "lib/verify_initramfs.py"
KERNEL = "6.16.12-valve-fixture"
MODULES = ("nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
           "nvidia-peermem.ko", "nvidia-uvm.ko")
CONFIG = "etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root, expected=None):
    image = root / "initramfs-linux.img"
    command = [str(VERIFIER), "--kernel", KERNEL,
               "--execution-manifest", str(root / "execution.json"),
               "--config", str(root / CONFIG), "--image", str(image),
               "--listing", str(root / "listing"), "--image-sha256", expected or sha(image),
               "--output", str(root / "result.json")]
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def fixture(root):
    image = root / "initramfs-linux.img"
    image.write_bytes(b"deterministic initramfs fixture\n")
    listing = [f"usr/lib/modules/{KERNEL}/{module}.zst" for module in MODULES]
    listing.extend((CONFIG, "usr/bin/busybox"))
    (root / "listing").write_text("\n".join(listing) + "\n")
    config = root / CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("options nvidia-drm modeset=1\n")
    files = []
    for name, payload, mode in (
        ("usr/bin/mkinitcpio", b"mkinitcpio\n", 0o755),
        ("usr/bin/lsinitcpio", b"lsinitcpio\n", 0o755),
        (CONFIG, config.read_bytes(), 0o644),
    ):
        files.append({"path": name, "kind": "file", "mode": mode,
                      "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    (root / "execution.json").write_text(json.dumps(
        {"schemaVersion": 1, "status": "verified", "files": files}))


def rejected(mutator, expected=None):
    with tempfile.TemporaryDirectory(prefix="initramfs-verification-hostile-") as temporary:
        root = Path(temporary)
        fixture(root)
        mutator(root)
        assert run(root, expected).returncode != 0


def main():
    with tempfile.TemporaryDirectory(prefix="initramfs-verification-") as temporary:
        root = Path(temporary)
        fixture(root)
        completed = run(root)
        assert completed.returncode == 0, completed.stderr
        result = json.loads((root / "result.json").read_text())
        assert result["status"] == "verified" and len(result["images"]) == 1
        assert set(result["images"][0]["modules"]) == set(MODULES)
        assert load_initramfs_verification(root / "result.json") == result

    rejected(lambda root: (root / "listing").write_text(
        (root / "listing").read_text() + CONFIG + "\n"))
    rejected(lambda root: (root / "listing").write_text("../escape\n"))
    rejected(lambda root: (root / "listing").write_text(
        (root / "listing").read_text().replace(f"usr/lib/modules/{KERNEL}/nvidia.ko.zst\n", "")))
    rejected(lambda root: (root / CONFIG).write_text("options nvidia NVreg=hostile\n"))

    def duplicate_manifest(root):
        document = json.loads((root / "execution.json").read_text())
        document["files"].append(document["files"][0])
        (root / "execution.json").write_text(json.dumps(document))
    rejected(duplicate_manifest)

    def duplicate_json_key(root):
        (root / "execution.json").write_text(
            '{"schemaVersion":1,"status":"verified","status":"verified","files":[]}')
    rejected(duplicate_json_key)

    with tempfile.TemporaryDirectory(prefix="initramfs-verification-drift-") as temporary:
        root = Path(temporary)
        fixture(root)
        before = sha(root / "initramfs-linux.img")
        (root / "initramfs-linux.img").write_bytes(b"replacement\n")
        assert run(root, before).returncode != 0

    def excessive_listing(root):
        with (root / "listing").open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
    rejected(excessive_listing)

    with tempfile.TemporaryDirectory(prefix="initramfs-result-hostile-") as temporary:
        root = Path(temporary)
        fixture(root)
        assert run(root).returncode == 0
        result_path = root / "result.json"
        document = json.loads(result_path.read_text())
        document["images"][0]["modules"]["nvidia.ko"] = "../escape/nvidia.ko"
        result_path.write_text(json.dumps(document))
        try:
            load_initramfs_verification(result_path)
        except SystemExit:
            pass
        else:
            raise AssertionError("result loader accepted an escaped module path")


if __name__ == "__main__":
    main()
