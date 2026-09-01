#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:?repository root is required}"
transaction_status=not-run
namespace_status=not-run
btrfs_status=not-run
flock_status=not-run
recovery_ab_status=not-run
chroot_hooks_status=not-run
mount_lifecycle_status=not-run
consumer_contract_status=not-run
target_execution_trust_status=not-run

cleanup()
{
    local rc=$?
    printf '{"schemaVersion":1,"status":"%s","transaction":"%s","flock":"%s","mountNamespace":"%s","btrfs":"%s","recoveryAB":"%s","chrootHooks":"%s","mountLifecycle":"%s","consumerContract":"%s","targetExecutionTrust":"%s"}\n' \
        "$([[ "$rc" == 0 ]] && printf passed || printf failed)" \
        "$transaction_status" "$flock_status" "$namespace_status" "$btrfs_status" \
        "$recovery_ab_status" "$chroot_hooks_status" "$mount_lifecycle_status" \
        "$consumer_contract_status" "$target_execution_trust_status"
    return "$rc"
}
trap cleanup EXIT

sudo -u fedora env HOME=/home/fedora \
    bash "$REPOSITORY_ROOT/tests/transaction.sh"
transaction_status=passed

exec 9>"$REPOSITORY_ROOT/tests/vm-lifecycle.lock"
flock -n 9
! flock -n "$REPOSITORY_ROOT/tests/vm-lifecycle.lock" -c true
flock_status=passed

namespace_probe="$(mktemp -d)"
trap 'umount "$namespace_probe/mount" 2>/dev/null || true; rm -rf "$namespace_probe"' RETURN
mkdir "$namespace_probe/source" "$namespace_probe/mount"
printf fixture > "$namespace_probe/source/marker"
mount --bind "$namespace_probe/source" "$namespace_probe/mount"
unshare --mount bash -c \
    'mount --make-private "$1"; umount "$1"; ! mountpoint -q "$1"' \
    bash "$namespace_probe/mount"
mountpoint -q "$namespace_probe/mount"
umount "$namespace_probe/mount"
namespace_status=passed

btrfs_image="$namespace_probe/btrfs.img"
btrfs_mount="$namespace_probe/btrfs"
truncate -s 256M "$btrfs_image"
mkfs.btrfs -q "$btrfs_image"
mkdir "$btrfs_mount"
mount -o loop,compress=zstd:3 "$btrfs_image" "$btrfs_mount"
printf fixture > "$btrfs_mount/payload"
sync
btrfs filesystem usage --raw "$btrfs_mount" >/dev/null
umount "$btrfs_mount"
btrfs_status=passed

bash "$REPOSITORY_ROOT/tests/vm/recovery-ab.sh"
recovery_ab_status=passed

bash "$REPOSITORY_ROOT/tests/vm/chroot-hooks.sh"
chroot_hooks_status=passed

bash "$REPOSITORY_ROOT/tests/vm/mount-lifecycle.sh" "$REPOSITORY_ROOT"
mount_lifecycle_status=passed

python3 "$REPOSITORY_ROOT/tests/install_contract.py"
consumer_contract_status=passed

python3 "$REPOSITORY_ROOT/tests/target_execution_trust.py"
target_execution_trust_status=passed
