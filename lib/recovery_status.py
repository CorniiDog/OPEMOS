#!/usr/bin/env python3
"""Bounded, machine-readable installed-system NVIDIA recovery status."""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = 1
MODULES = (
    ("nvidia", "nvidia"),
    ("nvidia_drm", "nvidia-drm"),
    ("nvidia_modeset", "nvidia-modeset"),
    ("nvidia_uvm", "nvidia-uvm"),
    ("nvidia_peermem", "nvidia-peermem"),
)
VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
KERNEL = re.compile(r"^[A-Za-z0-9._+\-]{1,192}$")
MAX_CONFIG_BYTES = 1024 * 1024


def confined(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes target root: {path}")
    return resolved


def regular_text(root: Path, relative: str, optional=True) -> str:
    path = confined(root, root / relative)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return ""
        raise ValueError(f"required file is missing: {relative}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"unsafe status input: {relative}")
    if info.st_size > MAX_CONFIG_BYTES:
        raise ValueError(f"status input is excessive: {relative}")
    return path.read_text(encoding="utf-8", errors="strict")


def module_path(root: Path, kernel: str, name: str) -> Path | None:
    base = confined(root, root / "usr/lib/modules" / kernel)
    if not base.is_dir() or base.is_symlink():
        return None
    matches = []
    for suffix in (".ko", ".ko.zst", ".ko.xz", ".ko.gz"):
        for candidate in base.rglob(name + suffix):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                matches.append(candidate)
                if len(matches) > 32:
                    raise ValueError(f"excessive module candidates for {name}")
    if not matches:
        return None
    # depmod's resolved identity is authoritative when examining the live root.
    if root == Path("/"):
        resolved = subprocess.run(
            ["modinfo", "-k", kernel, "-n", name], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        ).stdout.strip()
        if resolved:
            resolved_path = Path(resolved).resolve(strict=False)
            for candidate in matches:
                if candidate.resolve(strict=False) == resolved_path:
                    return candidate
    return sorted(matches, key=lambda item: str(item))[0]


def modinfo(path: Path, field: str) -> str:
    result = subprocess.run(
        ["modinfo", "-F", field, str(path)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=20,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.splitlines()[0].strip() if result.stdout else ""


def installed_nvidia(root: Path) -> str:
    for relative in (
        "var/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
        "var/lib/open-gpu-kernel-modules-steamos-support/installed-nvidia.txt",
        "var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup/nvidia-version",
        "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
    ):
        value = regular_text(root, relative).strip()
        if value:
            return value if VERSION.fullmatch(value) else ""
    if root == Path("/"):
        result = subprocess.run(
            ["pacman", "-Q", "nvidia-utils"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            timeout=20,
        )
        fields = result.stdout.strip().split()
        if result.returncode == 0 and len(fields) == 2:
            # Arch package release is appended after the NVIDIA version.
            value = fields[1].rsplit("-", 1)[0]
            if VERSION.fullmatch(value):
                return value
    return ""


def inspect(args):
    root = Path(args.root).resolve()
    kernel = args.kernel or os.uname().release
    if not KERNEL.fullmatch(kernel):
        raise ValueError("kernel identity is malformed")
    recovery_state = regular_text(
        root, "var/lib/open-gpu-kernel-modules-steamos-support/recovery/state.json"
    )
    fallback = None
    if recovery_state:
        try:
            state = json.loads(recovery_state)
            if state.get("schemaVersion") == 1 and state.get("active") is True:
                fallback = state.get("profile")
        except json.JSONDecodeError:
            raise ValueError("recovery state is malformed") from None

    expected_version = args.expected_nvidia or installed_nvidia(root)
    if expected_version and not VERSION.fullmatch(expected_version):
        raise ValueError("expected NVIDIA policy is malformed")
    records = []
    reasons = []
    for name, filename in MODULES:
        path = module_path(root, kernel, filename)
        record = {"name": name, "present": path is not None}
        if path is None:
            reasons.append("missing_exact_modules")
        else:
            vermagic = modinfo(path, "vermagic").split(" ", 1)[0]
            version = modinfo(path, "version")
            record.update({
                "path": str(path.relative_to(root)),
                "vermagic": vermagic,
                "version": version,
                "exactKernel": vermagic == kernel,
                "exactUserspace": bool(expected_version) and version == expected_version,
            })
            if vermagic != kernel:
                reasons.append("module_vermagic_mismatch")
            if not expected_version or version != expected_version:
                reasons.append("module_userspace_mismatch")
        records.append(record)

    healthy = not reasons
    if fallback:
        status = "fallback-active"
        reason = "fallback_active"
    elif healthy:
        status = "healthy"
        reason = "exact_nvidia_ready"
    else:
        status = "recovery-required"
        reason = reasons[0]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "target": {"kernelVersion": kernel, "nvidiaVersion": expected_version or None},
        "moduleVerification": {"status": "verified" if healthy else "failed", "records": records},
        "fallback": {
            "active": bool(fallback), "profile": fallback,
            "automaticProfile": "console",
            "profiles": ["console", "igpu-desktop", "nouveau-experimental"],
            "nouveauAutomatic": False,
        },
        "actions": (["disable-fallback"] if healthy and fallback else
                    ([] if healthy else ["enable-console-fallback", "repair-exact-kernel", "coordinate-ab-rollback"])),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/")
    parser.add_argument("--kernel")
    parser.add_argument("--expected-nvidia")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        document = inspect(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        document = {"schemaVersion": SCHEMA_VERSION, "status": "unknown", "reason": "inspection_failed", "error": str(error)[:512]}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > 256 * 1024:
        raise SystemExit("recovery status exceeded its contract bound")
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if document["status"] in ("healthy", "fallback-active", "recovery-required") else 2


if __name__ == "__main__":
    raise SystemExit(main())
