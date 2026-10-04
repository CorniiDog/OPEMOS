#!/usr/bin/env python3
"""Select early display modules from the live PCI topology."""

import argparse
import os
import re
import sys
from pathlib import Path


NVIDIA_MODULES = ("nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm")
NVIDIA_PAYLOADS = ("nvidia", "nvidia-modeset", "nvidia-uvm", "nvidia-drm")
MODULE_SUFFIXES = ("", ".xz", ".gz", ".zst")
DISPLAY_CLASS = 0x030000
DISPLAY_CLASS_MASK = 0xFF0000


def read_hex(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not re.fullmatch(r"0x[0-9a-fA-F]{4,8}", value):
        return None
    return int(value, 16)


def display_vendors(sysfs: Path) -> set[int]:
    vendors = set()
    devices = sysfs / "bus/pci/devices"
    try:
        entries = tuple(devices.iterdir())
    except OSError:
        return vendors
    for device in entries:
        vendor = read_hex(device / "vendor")
        device_class = read_hex(device / "class")
        if vendor is not None and device_class is not None and (
            device_class & DISPLAY_CLASS_MASK
        ) == DISPLAY_CLASS:
            vendors.add(vendor)
    return vendors


def intel_internal_panel_connected(sysfs: Path) -> bool:
    drm = sysfs / "class/drm"
    try:
        connectors = tuple(drm.iterdir())
    except OSError:
        return False
    for connector in connectors:
        name = connector.name.lower()
        if not any(marker in name for marker in ("-edp-", "-lvds-", "-dsi-")):
            continue
        try:
            status = (connector / "status").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if status == "connected" and read_hex(connector / "device/vendor") == 0x8086:
            return True
    return False


def module_availability(root: Path, kernel: str, module: str, relative: str) -> str:
    module_root = root / "usr/lib/modules" / kernel
    for suffix in MODULE_SUFFIXES:
        if (module_root / f"{relative}/{module}.ko{suffix}").is_file():
            return "module"
    builtin = module_root / "modules.builtin"
    try:
        records = builtin.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        records = ()
    if any(record.endswith(f"/{module}.ko") for record in records):
        return "builtin"
    return "unavailable"


def portable_nvidia_payloads(root: Path, kernel: str) -> tuple[str, ...]:
    relative_directory = Path(
        "usr/lib/modules"
    ) / kernel / "updates/open-gpu-kernel-modules-steamos"
    directory = root / relative_directory
    payloads = []
    for module in NVIDIA_PAYLOADS:
        matches = [
            directory / f"{module}.ko{suffix}"
            for suffix in MODULE_SUFFIXES
            if (directory / f"{module}.ko{suffix}").is_file()
            and not (directory / f"{module}.ko{suffix}").is_symlink()
        ]
        if len(matches) != 1:
            raise ValueError(
                "portable image requires exactly one regular "
                f"{module}.ko payload; found {len(matches)}"
            )
        payloads.append("/" + matches[0].relative_to(root).as_posix())
    return tuple(payloads)


def i915_availability(root: Path, kernel: str) -> str:
    return module_availability(
        root, kernel, "i915", "kernel/drivers/gpu/drm/i915"
    )


def portable_render(root: Path, kernel: str) -> tuple[str, str]:
    candidates = (
        ("i915", "kernel/drivers/gpu/drm/i915"),
        ("amdgpu", "kernel/drivers/gpu/drm/amd/amdgpu"),
    )
    modules = [
        module
        for module, relative in candidates
        if module_availability(root, kernel, module, relative) == "module"
    ]
    nvidia_payloads = portable_nvidia_payloads(root, kernel)
    # Image construction runs inside a managed appliance whose PCI topology is
    # unrelated to the eventual device. Include the target kernel's available
    # integrated-display drivers and defer NVIDIA DRM until the real root so a
    # muxless internal panel cannot lose the early KMS handoff to the builder
    # VM's virtual GPU decision.
    content = (
        "# Managed by OPEMOS\n"
        "# Display boot decision: portable-integrated-early-nvidia-rootfs\n"
        "# Hardware selection deferred to target boot\n"
        f"MODULES=({' '.join(modules)})\n"
        # Preserve the verified NVIDIA recovery payload in each generated
        # initramfs without asking mkinitcpio to load NVIDIA before the real
        # target chooses its display owner.
        f"FILES=({' '.join(nvidia_payloads)})\n"
    )
    return content, "portable-integrated-early-nvidia-rootfs"


def render(root: Path, kernel: str, sysfs: Path) -> tuple[str, str]:
    vendors = display_vendors(sysfs)
    hybrid = 0x8086 in vendors and 0x10DE in vendors
    availability = i915_availability(root, kernel) if hybrid else "not-required"
    modules = list(NVIDIA_MODULES)
    if hybrid and availability == "module" and intel_internal_panel_connected(sysfs):
        # A connected Intel internal panel is direct evidence that i915 owns
        # scanout. Keep NVIDIA DRM out of this pre-root handoff so it binds
        # normally after the real root is available instead of competing for
        # early fbdev/KMS.
        modules = ["i915"]
        decision = "hybrid-intel-panel-i915-early-nvidia-rootfs"
    elif hybrid and availability == "module":
        modules.insert(0, "i915")
        decision = "hybrid-topology-unclassified-i915-nvidia-early"
    elif hybrid and availability == "builtin":
        decision = "hybrid-intel-nvidia-i915-builtin"
    elif hybrid:
        decision = "hybrid-intel-nvidia-autodetect-fallback"
    else:
        decision = "nvidia-discrete-or-unclassified"
    content = (
        "# Managed by OPEMOS\n"
        f"# Display boot decision: {decision}\n"
        f"# Intel i915 availability: {availability}\n"
        f"MODULES=({' '.join(modules)})\n"
    )
    return content, decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--sysfs", default="/sys", type=Path)
    parser.add_argument("--portable-image", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel):
        parser.error("kernel version is invalid")
    try:
        if args.portable_image:
            content, decision = portable_render(args.root, args.kernel)
        else:
            content, decision = render(args.root, args.kernel, args.sysfs)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, args.output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    print(f"Display initramfs decision: {decision}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
