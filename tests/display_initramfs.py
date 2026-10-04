#!/usr/bin/env python3
"""User-visible boot configuration regressions for hybrid and discrete systems."""

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/configure_display_initramfs.py"
INSTALLER = ROOT / "bootstrap/install_to_root.sh"
KERNEL = "6.16.12-valve-fixture"
NVIDIA_PAYLOADS = ("nvidia", "nvidia-modeset", "nvidia-uvm", "nvidia-drm")
NVIDIA_GSP_FIRMWARE = ("gsp_tu10x.bin", "gsp_ga10x.bin")


def pci_device(sysfs: Path, address: str, vendor: str, device_class: str) -> None:
    device = sysfs / "bus/pci/devices" / address
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor + "\n")
    (device / "class").write_text(device_class + "\n")


def internal_panel(sysfs: Path, name: str, vendor: str, status: str = "connected") -> None:
    connector = sysfs / "class/drm" / name
    (connector / "device").mkdir(parents=True)
    (connector / "status").write_text(status + "\n")
    (connector / "device/vendor").write_text(vendor + "\n")


def execute(root: Path, sysfs: Path, *, portable: bool = False) -> tuple[str, str]:
    output = root / "etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf"
    arguments = [
        str(TOOL), "--root", str(root), "--kernel", KERNEL,
        "--sysfs", str(sysfs),
    ]
    if portable:
        arguments.append("--portable-image")
    arguments.extend(["--output", str(output)])
    completed = subprocess.run(
        arguments,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr
    return output.read_text(), completed.stderr


def execute_failure(root: Path, sysfs: Path) -> str:
    output = root / "etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf"
    completed = subprocess.run(
        [
            str(TOOL), "--root", str(root), "--kernel", KERNEL,
            "--sysfs", str(sysfs), "--portable-image", "--output", str(output),
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.returncode != 0
    assert not output.exists()
    return completed.stderr


def fixture(temporary: str) -> tuple[Path, Path]:
    base = Path(temporary)
    root = base / "root"
    sysfs = base / "sys"
    (root / "usr/lib/modules" / KERNEL).mkdir(parents=True)
    (sysfs / "bus/pci/devices").mkdir(parents=True)
    return root, sysfs


def add_nvidia_payloads(root: Path, suffix: str = ".zst") -> tuple[str, ...]:
    directory = (
        root / "usr/lib/modules" / KERNEL
        / "updates/open-gpu-kernel-modules-steamos"
    )
    directory.mkdir(parents=True)
    paths = []
    for module in NVIDIA_PAYLOADS:
        payload = directory / f"{module}.ko{suffix}"
        payload.write_bytes(b"fixture")
        paths.append("/" + payload.relative_to(root).as_posix())
    return tuple(paths)


def add_nvidia_firmware(root: Path, version: str = "575.64.05") -> tuple[str, ...]:
    directory = root / "usr/lib/firmware/nvidia" / version
    directory.mkdir(parents=True)
    paths = []
    for name in NVIDIA_GSP_FIRMWARE:
        payload = directory / name
        payload.write_bytes(b"authenticated firmware fixture")
        paths.append("/" + payload.relative_to(root).as_posix())
    return tuple(paths)


def main() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    invocation = installer.split("configure_display_initramfs.py", 1)[1].split(
        "snapshot_target_execution.py", 1
    )[0]
    assert "--portable-image" in invocation

    with tempfile.TemporaryDirectory(prefix="display-initramfs-hybrid-") as temporary:
        root, sysfs = fixture(temporary)
        i915 = root / "usr/lib/modules" / KERNEL / "kernel/drivers/gpu/drm/i915/i915.ko.zst"
        i915.parent.mkdir(parents=True)
        i915.write_bytes(b"fixture")
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030200")
        internal_panel(sysfs, "card0-eDP-1", "0x8086")
        content, diagnostic = execute(root, sysfs)
        assert "MODULES=(i915)" in content
        assert "nvidia" not in content.split("MODULES=(", 1)[1].split(")", 1)[0]
        assert "hybrid-intel-panel-i915-early-nvidia-rootfs" in content
        assert "hybrid-intel-panel-i915-early-nvidia-rootfs" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-ambiguous-hybrid-") as temporary:
        root, sysfs = fixture(temporary)
        i915 = root / "usr/lib/modules" / KERNEL / "kernel/drivers/gpu/drm/i915/i915.ko.zst"
        i915.parent.mkdir(parents=True)
        i915.write_bytes(b"fixture")
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030200")
        internal_panel(sysfs, "card1-eDP-1", "0x10de")
        content, diagnostic = execute(root, sysfs)
        assert "MODULES=(i915 nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "hybrid-topology-unclassified-i915-nvidia-early" in content
        assert "hybrid-topology-unclassified-i915-nvidia-early" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-disconnected-intel-panel-") as temporary:
        root, sysfs = fixture(temporary)
        i915 = root / "usr/lib/modules" / KERNEL / "kernel/drivers/gpu/drm/i915/i915.ko.zst"
        i915.parent.mkdir(parents=True)
        i915.write_bytes(b"fixture")
        pci_device(sysfs, "0000:00:02.0", "0x8086", "0x030000")
        pci_device(sysfs, "0000:01:00.0", "0x10de", "0x030200")
        internal_panel(sysfs, "card0-eDP-1", "0x8086", "disconnected")
        content, _ = execute(root, sysfs)
        assert "MODULES=(i915 nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in content
        assert "hybrid-topology-unclassified-i915-nvidia-early" in content

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

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-") as temporary:
        root, sysfs = fixture(temporary)
        nvidia_payloads = add_nvidia_payloads(root)
        nvidia_firmware = add_nvidia_firmware(root)
        module_root = root / "usr/lib/modules" / KERNEL / "kernel/drivers/gpu/drm"
        i915 = module_root / "i915/i915.ko.zst"
        amdgpu = module_root / "amd/amdgpu/amdgpu.ko.zst"
        i915.parent.mkdir(parents=True)
        amdgpu.parent.mkdir(parents=True)
        i915.write_bytes(b"fixture")
        amdgpu.write_bytes(b"fixture")
        # This is the builder appliance topology, not the eventual target.
        pci_device(sysfs, "0000:00:01.0", "0x1234", "0x030000")
        content, diagnostic = execute(root, sysfs, portable=True)
        assert "MODULES=(i915 amdgpu)" in content
        assert "nvidia" not in content.split("MODULES=(", 1)[1].split(")", 1)[0]
        assert f"FILES=({' '.join(nvidia_payloads + nvidia_firmware)})" in content
        assert "portable-integrated-early-nvidia-rootfs" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-builtin-") as temporary:
        root, sysfs = fixture(temporary)
        nvidia_payloads = add_nvidia_payloads(root, suffix="")
        nvidia_firmware = add_nvidia_firmware(root)
        modules = root / "usr/lib/modules" / KERNEL / "modules.builtin"
        modules.write_text("kernel/drivers/gpu/drm/i915/i915.ko\n")
        content, _ = execute(root, sysfs, portable=True)
        assert "MODULES=()" in content
        assert f"FILES=({' '.join(nvidia_payloads + nvidia_firmware)})" in content

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-missing-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        add_nvidia_firmware(root)
        missing = (
            root / "usr/lib/modules" / KERNEL
            / "updates/open-gpu-kernel-modules-steamos/nvidia-drm.ko.zst"
        )
        missing.unlink()
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular nvidia-drm.ko payload; found 0" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-ambiguous-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        add_nvidia_firmware(root)
        duplicate = (
            root / "usr/lib/modules" / KERNEL
            / "updates/open-gpu-kernel-modules-steamos/nvidia.ko"
        )
        duplicate.write_bytes(b"duplicate")
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular nvidia.ko payload; found 2" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-linked-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        add_nvidia_firmware(root)
        payload = (
            root / "usr/lib/modules" / KERNEL
            / "updates/open-gpu-kernel-modules-steamos/nvidia-uvm.ko.zst"
        )
        payload.unlink()
        payload.symlink_to("nvidia.ko.zst")
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular nvidia-uvm.ko payload; found 0" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-missing-gsp-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        firmware = add_nvidia_firmware(root)
        (root / firmware[0].removeprefix("/")).unlink()
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular NVIDIA gsp_tu10x.bin firmware payload; found 0" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-linked-gsp-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        firmware = add_nvidia_firmware(root)
        target = root / firmware[0].removeprefix("/")
        target.unlink()
        target.symlink_to("gsp_ga10x.bin")
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular NVIDIA gsp_tu10x.bin firmware payload; found 0" in diagnostic

    with tempfile.TemporaryDirectory(prefix="display-initramfs-portable-ambiguous-gsp-") as temporary:
        root, sysfs = fixture(temporary)
        add_nvidia_payloads(root)
        add_nvidia_firmware(root)
        duplicate = root / "usr/lib/firmware/nvidia/580.1/gsp_ga10x.bin"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_bytes(b"duplicate")
        diagnostic = execute_failure(root, sysfs)
        assert "requires exactly one regular NVIDIA gsp_ga10x.bin firmware payload; found 2" in diagnostic


if __name__ == "__main__":
    main()
