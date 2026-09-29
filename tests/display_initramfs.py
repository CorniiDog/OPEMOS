#!/usr/bin/env python3
"""User-visible boot configuration regressions for hybrid and discrete systems."""

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/configure_display_initramfs.py"
KERNEL = "6.16.12-valve-fixture"


def pci_device(sysfs: Path, address: str, vendor: str, device_class: str) -> None:
    device = sysfs / "bus/pci/devices" / address
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor + "\n")
    (device / "class").write_text(device_class + "\n")


def execute(root: Path, sysfs: Path) -> tuple[str, str]:
    output = root / "etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf"
    completed = subprocess.run(
        [str(TOOL), "--root", str(root), "--kernel", KERNEL,
         "--sysfs", str(sysfs), "--output", str(output)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr
    return output.read_text(), completed.stderr


def fixture(temporary: str) -> tuple[Path, Path]:
    base = Path(temporary)
    root = base / "root"
    sysfs = base / "sys"
    (root / "usr/lib/modules" / KERNEL).mkdir(parents=True)
    (sysfs / "bus/pci/devices").mkdir(parents=True)
    return root, sysfs


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="display-initramfs-hybrid-") as temporary:
        root, sysfs = fixture(temporary)
        i915 = root / "usr/lib/modules" / KERNEL / "kernel/drivers/gpu/drm/i915/i915.ko.zst"
        i915.parent.mkdir(parents=True)
        i915.write_bytes(b"fixture")
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030200")
        content, diagnostic = execute(root, sysfs)
        assert "MODULES=(i915 nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "hybrid-intel-nvidia-i915-early" in content
        assert "hybrid-intel-nvidia-i915-early" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-discrete-") as temporary:
        root, sysfs = fixture(temporary)
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030000")
        content, _ = execute(root, sysfs)
        assert "MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "nvidia-discrete-or-unclassified" in content
        assert "i915 nvidia" not in content

    with tempfile.TemporaryDirectory(prefix="display-initramfs-fallback-") as temporary:
        root, sysfs = fixture(temporary)
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030000")
        content, diagnostic = execute(root, sysfs)
        assert "MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "hybrid-intel-nvidia-autodetect-fallback" in content
        assert "unavailable" in content
        assert "autodetect-fallback" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-builtin-") as temporary:
        root, sysfs = fixture(temporary)
        modules = root / "usr/lib/modules" / KERNEL / "modules.builtin"
        modules.write_text("kernel/drivers/gpu/drm/i915/i915.ko\n")
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030000")
        content, _ = execute(root, sysfs)
        assert "MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "hybrid-intel-nvidia-i915-builtin" in content


if __name__ == "__main__":
    main()
