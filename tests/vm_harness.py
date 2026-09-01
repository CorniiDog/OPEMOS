#!/usr/bin/env python3
"""Static and argument-contract checks for the disposable headless VM runner."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests/vm/run.sh"
GUEST = ROOT / "tests/vm/guest-checks.sh"
RECOVERY_AB = ROOT / "tests/vm/recovery-ab.sh"
CHROOT_HOOKS = ROOT / "tests/vm/chroot-hooks.sh"
MOUNT_LIFECYCLE = ROOT / "tests/vm/mount-lifecycle.sh"
ARCH_RUNNER = ROOT / "tests/vm/run-arch.sh"
ARCH_GUEST = ROOT / "tests/vm/arch-guest-checks.sh"
OFFLINE_CACHE_MATRIX = ROOT / "tests/vm/run-offline-cache-matrix.sh"
STEAMOS_RUNNER = ROOT / "tests/vm/run-steamos-recovery.sh"
STEAMOS_INSPECTOR = ROOT / "tests/vm/inspect-steamos-recovery.sh"
IGNORE = ROOT / "tests/vm/.gitignore"


def invoke(*arguments):
    return subprocess.run(
        [str(RUNNER), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def main():
    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "--no-image-download" in help_result.stdout

    malformed = invoke("--unknown")
    assert malformed.returncode == 2
    assert "unknown argument" in malformed.stderr

    arch_help = subprocess.run(
        [str(ARCH_RUNNER), "--help"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert arch_help.returncode == 0 and "--no-download" in arch_help.stdout
    steamos_help = subprocess.run(
        [str(STEAMOS_RUNNER), "--help"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert steamos_help.returncode == 0 and "--archive" in steamos_help.stdout

    runner = RUNNER.read_text(encoding="utf-8")
    guest = GUEST.read_text(encoding="utf-8")
    recovery_ab = RECOVERY_AB.read_text(encoding="utf-8")
    chroot_hooks = CHROOT_HOOKS.read_text(encoding="utf-8")
    mount_lifecycle = MOUNT_LIFECYCLE.read_text(encoding="utf-8")
    arch_runner = ARCH_RUNNER.read_text(encoding="utf-8")
    arch_guest = ARCH_GUEST.read_text(encoding="utf-8")
    offline_cache_matrix = OFFLINE_CACHE_MATRIX.read_text(encoding="utf-8")
    steamos_runner = STEAMOS_RUNNER.read_text(encoding="utf-8")
    steamos_inspector = STEAMOS_INSPECTOR.read_text(encoding="utf-8")
    ignored = IGNORE.read_text(encoding="utf-8")
    assert "e401a4db2e5e04d1967b6729774faa96da629bcf3ba90b67d8d9cce9906bec0f" in runner
    assert "sha256sum -c" in runner
    assert "-display none" in runner and "-serial" in runner
    assert "-nic user" in runner and "hostfwd" not in runner
    assert "2700" in runner and "20G" in runner
    assert "expected_result=" in runner and "OPEN_GPU_VM_COMPLETE" in runner
    assert "--offline-cache-only" in runner
    assert "stop_qemu" in runner and 'rm -f "$BASE_IMAGE.partial"' in runner
    assert 'RUNTIME_DIR="$SCRIPT_DIR/.runtime/fedora"' in runner
    assert "tests/transaction.sh" in guest
    assert "unshare --mount" in guest and "mkfs.btrfs" in guest
    assert '"schemaVersion":1' in guest
    assert "btrfs subvolume snapshot -r" in recovery_ab
    assert "kill -TERM" in recovery_ab and "assert_original_slots" in recovery_ab
    assert "IMAGE_BYTES * 4" in recovery_ab and "mutate_inactive success" in recovery_ab
    assert 'chroot "$TARGET"' in chroot_hooks
    assert "cpio --reproducible --null -o -H newc" in chroot_hooks
    assert "INJECT_HOOK_FAILURE" in chroot_hooks and "INJECT_HOOK_SLEEP" in chroot_hooks
    assert "AVAILABLE_BYTES" in chroot_hooks and "initramfs-linux.img" in chroot_hooks
    assert "verify_bind_mount.py" in mount_lifecycle
    assert "same device identity" in mount_lifecycle
    assert "fcntl.flock" in mount_lifecycle and "unshare --mount" in mount_lifecycle
    assert "tests/install_contract.py" in guest
    assert "tests/target_execution_trust.py" in guest
    assert "tests/initramfs_verification.py" in guest
    assert "steamos-recovery-fixture.sh" in guest
    assert "tests/authenticated_cache_bundle.py" in guest
    assert "5d8be8d28cfd290f051b0f67df0a6874596ad23de3f3f18b90c91aeb758eb878" in arch_runner
    assert "656E4C5AC1CC3B86E539D97E343635A6859A9174" in arch_runner
    assert "gpgv --keyring" in arch_runner and "sha256sum -c" in arch_runner
    assert '--homedir "$RUNTIME_DIR/inspect-gnupg" --dearmor' in arch_runner
    assert "expected_result=" in arch_runner and "OPEN_GPU_ARCH_VM_COMPLETE" in arch_runner
    assert "--offline-cache-only" in arch_runner
    assert "stop_qemu" in arch_runner and "cleanup_partial_downloads" in arch_runner
    assert 'RUNTIME_DIR="$SCRIPT_DIR/.runtime/arch"' in arch_runner
    assert "pacman -S" in arch_guest and "mkinitcpio -P" in arch_guest
    assert "kill -TERM" in arch_guest and "lsinitcpio" in arch_guest
    assert "tests/authenticated_cache_bundle.py" in arch_guest
    assert 'run.sh" --no-image-download --offline-cache-only' in offline_cache_matrix
    assert 'run-arch.sh" --no-download --offline-cache-only' in offline_cache_matrix
    assert "concurrent" in offline_cache_matrix and '"disabled"' in offline_cache_matrix
    assert "fedora_pid" in offline_cache_matrix and "arch_pid" in offline_cache_matrix
    assert "validate_steamos_recovery_input.py" in steamos_runner
    assert "decompress_bzip2_image.py" in steamos_runner
    assert "readonly=on" in steamos_runner and "-display none" in steamos_runner
    assert "hostfwd" not in steamos_runner and "2700" in steamos_runner
    assert 'mount -o ro,nosuid,nodev,noexec' in steamos_inspector
    assert 'truncate -s 256M "$work/$slot.img"' in steamos_inspector
    assert "caller-provided block device" in steamos_inspector
    for pattern in (".cache/", ".runtime/", "*.qcow2", "*.img", "*.iso", "*.log", "*.sock"):
        assert pattern in ignored


if __name__ == "__main__":
    main()
