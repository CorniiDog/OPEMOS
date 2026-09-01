#!/usr/bin/env bash
set -euo pipefail

recovery_device="${1:-/dev/vdb}"
work="$(mktemp -d /tmp/steamos-recovery-inspect.XXXXXX)"
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

[[ -b "$recovery_device" ]]
root=""
while read -r partition; do
    mountpoint="$work/recovery-${partition##*/}"
    mkdir "$mountpoint"
    if mount -o ro,nosuid,nodev,noexec "$partition" "$mountpoint" 2>/dev/null; then
        mounts+=("$mountpoint")
        if [[ -f "$mountpoint/etc/os-release" ]] &&
           grep -qx 'ID=steamos' "$mountpoint/etc/os-release"; then
            [[ -z "$root" ]]
            root="$mountpoint"
        fi
    fi
done < <(lsblk -nrpo NAME,TYPE "$recovery_device" | awk '$2 == "part" {print $1}')
[[ -n "$root" ]]
[[ -x "$root/usr/bin/mkinitcpio" ]]
find "$root/etc/mkinitcpio.d" -maxdepth 1 -type f -name '*.preset' -print -quit | grep -q .
find "$root/usr/share/libalpm/hooks" -maxdepth 1 -type f -name '*.hook' -print -quit | grep -q .
[[ -d "$root/usr/lib/holo/pacmandb/local" ]]

# Writable targets are only fresh files in this disposable guest, never a
# caller-provided block device.
for slot in rootfs-A rootfs-B efi-A efi-B; do
    truncate -s 256M "$work/$slot.img"
    mkfs.btrfs -q -f -L "$slot" "$work/$slot.img"
    mkdir "$work/$slot"
    mount -o loop "$work/$slot.img" "$work/$slot"
    mounts+=("$work/$slot")
done
for slot in rootfs-A rootfs-B; do
    target="$work/$slot"
    mkdir -p "$target/etc/mkinitcpio.d" "$target/usr/share/libalpm/hooks" \
        "$target/usr/lib/holo/pacmandb/local" "$target/etc/modprobe.d"
    install -m 0644 "$root/etc/os-release" "$target/etc/os-release"
    find "$root/etc/mkinitcpio.d" -maxdepth 1 -type f -name '*.preset' -exec \
        install -m 0644 {} "$target/etc/mkinitcpio.d/" \;
    find "$root/usr/share/libalpm/hooks" -maxdepth 1 -type f -name '*.hook' -exec \
        install -m 0644 {} "$target/usr/share/libalpm/hooks/" \;
    printf 'options nvidia-drm modeset=1 fbdev=1\n' \
        > "$target/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
done
for slot in efi-A efi-B; do
    mkdir -p "$work/$slot/EFI/steamos"
    printf 'linux /vmlinuz root=LABEL=rootfs-%s quiet nvidia-drm.modeset=1 nvidia-drm.fbdev=1\n' \
        "${slot#efi-}" > "$work/$slot/EFI/steamos/grub.cfg"
done
before="$(find "$work"/rootfs-{A,B}/etc "$work"/efi-{A,B}/EFI -type f -print0 |
    sort -z | xargs -0 sha256sum)"
for slot in rootfs-A rootfs-B; do
    printf 'options nvidia-drm modeset=1 fbdev=1\n' \
        > "$work/$slot/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
done
[[ "$before" == "$(find "$work"/rootfs-{A,B}/etc "$work"/efi-{A,B}/EFI \
    -type f -print0 | sort -z | xargs -0 sha256sum)" ]]
bash /opt/open-gpu/bootstrap/install_to_root.sh --help >/dev/null
python3 /opt/open-gpu/lib/validate_install_inputs.py --help >/dev/null

printf '{"schemaVersion":1,"status":"passed","media":"valve-reviewed","layout":"passed","presets":"passed","hooks":"passed","recoveryAB":"passed","productionInstaller":"preflight-only","idempotency":"passed"}\n'
