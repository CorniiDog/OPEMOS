#!/usr/bin/env python3
"""Bounded, machine-readable installed-system NVIDIA recovery status."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from payload_receipt import verify_receipt_evidence

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
MAX_MODULE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_MODULE_BYTES = 1024 * 1024 * 1024
RECOVERY_STATE_FIELDS = {"schemaVersion", "active", "profile"}
RECOVERY_PROFILES = {"console", "igpu-desktop", "nouveau-experimental"}


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("recovery state contains a duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("recovery state contains a non-finite number")


def recovery_profile(payload):
    try:
        document = json.loads(
            payload, object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError:
        raise ValueError("recovery state is malformed") from None
    if (not isinstance(document, dict)
            or set(document) != RECOVERY_STATE_FIELDS
            or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1
            or document.get("active") is not True
            or not isinstance(document.get("profile"), str)
            or document["profile"] not in RECOVERY_PROFILES):
        raise ValueError("recovery state is malformed")
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n"
    if payload != canonical:
        raise ValueError("recovery state is not canonical JSON")
    return document["profile"]


def confined(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("status path is outside the target root") from None
    if any(component in ("", ".", "..") for component in relative.parts):
        raise ValueError("status path is malformed")
    candidate = resolved_root
    for component in relative.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise ValueError("status path contains a symlink")
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes target root: {path}")
    return candidate


def regular_text(root: Path, relative: str, optional=True) -> str:
    path = confined(root, root / relative)
    components = Path(relative).parts
    directory_descriptors = []
    descriptor = None
    try:
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        parent = os.open(root.resolve(), directory_flags)
        directory_descriptors.append(parent)
        for component in components[:-1]:
            parent = os.open(component, directory_flags, dir_fd=parent)
            directory_descriptors.append(parent)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(components[-1], flags, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return ""
        raise ValueError(f"required file is missing: {relative}")
    try:
        before = os.fstat(descriptor)
        expected_owner = 0 if root == Path("/") else os.geteuid()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != expected_owner
                or stat.S_IMODE(before.st_mode) & 0o022):
            raise ValueError(f"unsafe status input: {relative}")
        if before.st_size > MAX_CONFIG_BYTES:
            raise ValueError(f"status input is excessive: {relative}")
        chunks = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
        identity = lambda info: (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode), info.st_nlink,
        )
        if (len(payload) != before.st_size or identity(before) != identity(after)
                or identity(after) != identity(current)):
            raise ValueError(f"status input changed while read: {relative}")
        return payload.decode("utf-8", errors="strict")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)


def module_path(root: Path, kernel: str, name: str) -> Path | None:
    base = confined(root, root / "usr/lib/modules" / kernel)
    if not base.is_dir() or base.is_symlink():
        return None
    matches = []
    for suffix in (".ko", ".ko.zst", ".ko.xz", ".ko.gz"):
        for candidate in base.rglob(name + suffix):
            try:
                confined(root, candidate)
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            expected_owner = 0 if root == Path("/") else os.geteuid()
            if (stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and info.st_nlink == 1 and info.st_uid == expected_owner
                    and not stat.S_IMODE(info.st_mode) & 0o022
                    and 0 < info.st_size <= MAX_MODULE_BYTES):
                matches.append(candidate)
                if len(matches) > 32:
                    raise ValueError(f"excessive module candidates for {name}")
            else:
                raise ValueError(f"unsafe module candidate for {name}")
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
            selected = [
                candidate for candidate in matches
                if candidate.resolve(strict=False) == resolved_path
            ]
            if len(selected) == 1:
                return selected[0]
        raise ValueError(f"loaded module identity is unresolved for {name}")
    if len(matches) != 1:
        raise ValueError(f"module identity is ambiguous for {name}")
    return matches[0]


def module_identity(root: Path, path: Path):
    confined(root, path)
    info = path.lstat()
    expected_owner = 0 if root == Path("/") else os.geteuid()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != expected_owner
            or stat.S_IMODE(info.st_mode) & 0o022
            or not 0 < info.st_size <= MAX_MODULE_BYTES):
        raise ValueError("installed module identity is unsafe")
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, info.st_gid,
        stat.S_IMODE(info.st_mode), info.st_nlink,
    )


def modinfo(path: Path, field: str) -> str:
    result = subprocess.run(
        ["modinfo", "-F", field, str(path)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=20,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.splitlines()[0].strip() if result.stdout else ""


def module_payload_sha256(root: Path, path: Path) -> str:
    if not path.name.endswith(".ko.zst"):
        raise ValueError("receipt-bound module representation is unsupported")
    process = None
    descriptor = None
    digest = hashlib.sha256()
    expanded = 0
    try:
        before = module_identity(root, path)
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        if module_identity(root, path) != before:
            raise ValueError("receipt-bound module changed before inspection")
        process = subprocess.Popen(
            ["zstd", "-q", "-d", "-c"],
            stdin=descriptor, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        with process.stdout:
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                expanded += len(chunk)
                if expanded > MAX_EXPANDED_MODULE_BYTES:
                    process.kill()
                    raise ValueError("receipt-bound module payload is excessive")
                digest.update(chunk)
        if process.wait(timeout=20) != 0 or expanded == 0:
            raise ValueError("receipt-bound module decompression failed")
        current = module_identity(root, path)
        opened = os.fstat(descriptor)
        opened_identity = (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns,
            opened.st_ctime_ns, opened.st_uid, opened.st_gid,
            stat.S_IMODE(opened.st_mode), opened.st_nlink,
        )
        if before != opened_identity or before != current:
            raise ValueError("receipt-bound module changed during inspection")
        return digest.hexdigest()
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("receipt-bound module decompression failed") from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if descriptor is not None:
            os.close(descriptor)


def verify_receipt_modules(root, kernel, expected_version, installed_paths):
    try:
        receipt, evidence = verify_receipt_evidence(
            root, allow_live_root=root == Path("/"),
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("installed payload receipt is unavailable or invalid") from error
    target = receipt["target"]
    if (target.get("kernelVersion") != kernel
            or target.get("nvidiaVersion") != expected_version
            or target.get("architecture") != "x86_64"):
        raise ValueError("installed payload receipt targets another system")
    verification = evidence.get("moduleVerification")
    records = verification.get("modules") if isinstance(verification, dict) else None
    if (not isinstance(records, list) or len(records) != len(MODULES)
            or verification.get("schemaVersion") != 1
            or verification.get("status") != "verified"
            or verification.get("reason") != "installed_modules_verified"):
        raise ValueError("payload receipt module evidence is incomplete")
    expected_names = {filename + ".ko" for _, filename in MODULES}
    by_name = {
        record.get("moduleName"): record
        for record in records if isinstance(record, dict)
    }
    if set(by_name) != expected_names or len(by_name) != len(records):
        raise ValueError("payload receipt module evidence is ambiguous")
    destination = (
        Path("usr/lib/modules") / kernel
        / "updates/open-gpu-kernel-modules-steamos"
    )
    for _name, filename in MODULES:
        module_name = filename + ".ko"
        record = by_name[module_name]
        path = installed_paths.get(module_name)
        expected_path = destination / f"{module_name}.zst"
        expected_hash = record.get("actualPayloadSha256")
        if (path is None or path.relative_to(root) != expected_path
                or record.get("targetRelativePath") != str(expected_path)
                or record.get("representation") != ".ko.zst"
                or record.get("expectedPayloadSha256") != expected_hash
                or not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
                or record.get("expectedMode") != "0644"
                or record.get("actualMode") != "0644"
                or record.get("expectedUid") != 0
                or record.get("actualUid") != 0
                or record.get("expectedGid") != 0
                or record.get("actualGid") != 0
                or record.get("decompressionStatus") != "verified"
                or record.get("invalidFields") != []):
            raise ValueError("payload receipt module evidence is inconsistent")
        metadata = path.lstat()
        expected_owner = 0 if root == Path("/") else os.geteuid()
        if (stat.S_IMODE(metadata.st_mode) != 0o644
                or metadata.st_uid != expected_owner
                or root == Path("/") and metadata.st_gid != 0
                or record.get("compressedSizeBytes") != metadata.st_size
                or module_payload_sha256(root, path) != expected_hash):
            raise ValueError("installed module differs from its payload receipt")
    return receipt["receiptId"]


def installed_nvidia(root: Path) -> str:
    records = []
    for relative in (
        "var/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
        "var/lib/open-gpu-kernel-modules-steamos-support/installed-nvidia.txt",
        "var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup/nvidia-version",
        "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install/nvidia-version",
    ):
        value = regular_text(root, relative).strip()
        if value:
            if not VERSION.fullmatch(value):
                raise ValueError("installed NVIDIA identity is malformed")
            records.append(value)
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
            if not VERSION.fullmatch(value):
                raise ValueError("installed NVIDIA package identity is malformed")
            records.append(value)
        elif result.returncode == 0:
            raise ValueError("installed NVIDIA package identity is malformed")
    if len(set(records)) > 1:
        raise ValueError("installed NVIDIA identities are ambiguous")
    return records[0] if records else ""


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
        fallback = recovery_profile(recovery_state)

    installed_version = installed_nvidia(root)
    expected_file_version = ""
    if args.expected_nvidia_file:
        expected_file_version = regular_text(
            root, args.expected_nvidia_file, optional=False,
        ).strip()
        if VERSION.fullmatch(expected_file_version) is None:
            raise ValueError("expected NVIDIA policy is malformed")
    if args.expected_nvidia and expected_file_version:
        raise ValueError("expected NVIDIA policy is ambiguous")
    expected_version = (
        args.expected_nvidia or expected_file_version or installed_version
    )
    if expected_version and not VERSION.fullmatch(expected_version):
        raise ValueError("expected NVIDIA policy is malformed")
    records = []
    reasons = []
    installed_paths = {}
    if ((args.expected_nvidia or expected_file_version)
            and installed_version != expected_version):
        reasons.append("module_userspace_mismatch")
    for name, filename in MODULES:
        path = module_path(root, kernel, filename)
        record = {"name": name, "present": path is not None}
        if path is None:
            reasons.append("missing_exact_modules")
        else:
            installed_paths[filename + ".ko"] = path
            identity = module_identity(root, path)
            vermagic = modinfo(path, "vermagic").split(" ", 1)[0]
            version = modinfo(path, "version")
            if module_identity(root, path) != identity:
                raise ValueError(f"installed module changed during inspection: {name}")
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

    if args.require_payload_receipt:
        try:
            verify_receipt_modules(root, kernel, expected_version, installed_paths)
        except ValueError as error:
            reason = (
                "module_payload_mismatch"
                if str(error) == "installed module differs from its payload receipt"
                else "payload_receipt_invalid"
            )
            reasons.append(reason)

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
    parser.add_argument("--expected-nvidia-file")
    parser.add_argument("--require-payload-receipt", action="store_true")
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
