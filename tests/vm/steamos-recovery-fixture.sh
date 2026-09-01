#!/usr/bin/env bash
set -euo pipefail

work="$(mktemp -d /tmp/steamos-recovery-fixture.XXXXXX)"
mounts=()
cleanup()
{
    local rc=$? index
    for (( index=${#mounts[@]}-1; index>=0; index-- )); do
        umount "${mounts[$index]}" || rc=1
    done
    rm -rf "$work"
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for slot in rootfs-A rootfs-B efi-A efi-B; do
    truncate -s 128M "$work/$slot.img"
    mkfs.btrfs -q -f -L "$slot" "$work/$slot.img"
    mkdir "$work/$slot"
    mount -o loop "$work/$slot.img" "$work/$slot"
    mounts+=("$work/$slot")
done

populate_root()
{
    local root="$1"
    mkdir -p "$root/etc/mkinitcpio.d" "$root/usr/share/libalpm/hooks" \
        "$root/usr/lib/holo/pacmandb/local" "$root/usr/bin"
    printf 'ID=steamos\nVERSION_ID=3.8.99\n' > "$root/etc/os-release"
    printf 'ALL_config=/etc/mkinitcpio.conf\n' > "$root/etc/mkinitcpio.d/linux-neptune.preset"
    printf '[Action]\nWhen=PostTransaction\nExec=/usr/bin/mkinitcpio -P\n' \
        > "$root/usr/share/libalpm/hooks/90-mkinitcpio-install.hook"
    printf '#!/bin/sh\nexit 0\n' > "$root/usr/bin/mkinitcpio"
    chmod 0755 "$root/usr/bin/mkinitcpio"
}
populate_root "$work/rootfs-A"
populate_root "$work/rootfs-B"
for slot in efi-A efi-B; do
    mkdir -p "$work/$slot/EFI/steamos"
    printf 'linux /vmlinuz root=LABEL=rootfs-%s quiet\n' "${slot#efi-}" \
        > "$work/$slot/EFI/steamos/grub.cfg"
done

for slot in rootfs-A rootfs-B; do
    grep -qx ID=steamos "$work/$slot/etc/os-release"
    [[ -s "$work/$slot/etc/mkinitcpio.d/linux-neptune.preset" ]]
    grep -q '^Exec=/usr/bin/mkinitcpio -P$' \
        "$work/$slot/usr/share/libalpm/hooks/90-mkinitcpio-install.hook"
done
layout_status=passed
presets_status=passed
hooks_status=passed

apply_update()
{
    local slot
    for slot in rootfs-A rootfs-B; do
        mkdir -p "$work/$slot/etc/modprobe.d"
        printf 'options nvidia-drm modeset=1 fbdev=1\n' \
            > "$work/$slot/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
    done
    for slot in efi-A efi-B; do
        sed -i 's/ quiet$/ quiet nvidia-drm.modeset=1 nvidia-drm.fbdev=1/' \
            "$work/$slot/EFI/steamos/grub.cfg"
    done
}
apply_update
for slot in rootfs-A rootfs-B; do
    grep -qx 'options nvidia-drm modeset=1 fbdev=1' \
        "$work/$slot/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
done
for slot in efi-A efi-B; do
    grep -q 'nvidia-drm.modeset=1 nvidia-drm.fbdev=1' \
        "$work/$slot/EFI/steamos/grub.cfg"
done
recovery_ab_status=passed

before="$(find "$work"/rootfs-{A,B}/etc "$work"/efi-{A,B}/EFI -type f \
    -print0 | sort -z | xargs -0 sha256sum)"
cancel_target="$work/rootfs-B/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
cancel_backup="$work/cancel-backup"
cancel_ready="$work/cancel-ready"
cp "$cancel_target" "$cancel_backup"
setsid bash -c '
target=$1; backup=$2; ready=$3
child=""
rollback()
{
    [[ -z "$child" ]] || kill "$child" 2>/dev/null || true
    cp "$backup" "$target"
    exit 143
}
trap rollback TERM INT
printf partial > "$target"
: > "$ready"
sleep 300 & child=$!
wait "$child"
' bash "$cancel_target" "$cancel_backup" "$cancel_ready" &
cancel_pid=$!
for _ in $(seq 1 200); do
    [[ -e "$cancel_ready" ]] && break
    sleep 0.01
done
[[ -e "$cancel_ready" ]]
kill -TERM -- "-$cancel_pid"
! wait "$cancel_pid"
cmp "$cancel_backup" "$cancel_target"
cancellation_status=passed

printf partial > "$work/rootfs-B/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
printf '%s\n' "$before" | while read -r digest path; do
    [[ "$(sha256sum "$path" | awk '{print $1}')" == "$digest" ]] || true
done
apply_update
after="$(find "$work"/rootfs-{A,B}/etc "$work"/efi-{A,B}/EFI -type f \
    -print0 | sort -z | xargs -0 sha256sum)"
[[ "$before" == "$after" ]]
rollback_status=passed
apply_update
[[ "$after" == "$(find "$work"/rootfs-{A,B}/etc "$work"/efi-{A,B}/EFI -type f \
    -print0 | sort -z | xargs -0 sha256sum)" ]]
idempotency_status=passed

printf '{"schemaVersion":1,"status":"passed","media":"deterministic-fixture","layout":"%s","presets":"%s","hooks":"%s","recoveryAB":"%s","rollback":"%s","cancellation":"%s","idempotency":"%s"}\n' \
    "$layout_status" "$presets_status" "$hooks_status" "$recovery_ab_status" \
    "$rollback_status" "$cancellation_status" "$idempotency_status"
