#!/usr/bin/env python3
"""Select early display modules from the live PCI topology."""

import argparse
import os
import re
import sys
from pathlib import Path


NVIDIA_MODULES = ("nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm")
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


def i915_availability(root: Path, kernel: str) -> str:
    module_root = root / "usr/lib/modules" / kernel
    for suffix in ("", ".xz", ".gz", ".zst"):
        if (module_root / f"kernel/drivers/gpu/drm/i915/i915.ko{suffix}").is_file():
            return "module"
    builtin = module_root / "modules.builtin"
    try:
        records = builtin.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        records = ()
    if any(record.endswith("/i915.ko") for record in records):
        return "builtin"
    return "unavailable"


def render(root: Path, kernel: str, sysfs: Path) -> tuple[str, str]:
    vendors = display_vendors(sysfs)
    hybrid = 0x8086 in vendors and 0x10DE in vendors
    availability = i915_availability(root, kernel) if hybrid else "not-required"
    modules = list(NVIDIA_MODULES)
    if hybrid and availability == "module":
        modules.insert(0, "i915")
        decision = "hybrid-intel-nvidia-i915-early"
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
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", args.kernel):
        parser.error("kernel version is invalid")
    content, decision = render(args.root, args.kernel, args.sysfs)
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
