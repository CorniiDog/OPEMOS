#!/usr/bin/env bash
set -euo pipefail

WORK_ROOT="$(mktemp -d /tmp/open-gpu-chroot-hooks.XXXXXX)"
TARGET="$WORK_ROOT/target"
BACKUP="$WORK_ROOT/backup"
KERNEL=6.16.12-valve-fixture
READY="$WORK_ROOT/hook.ready"
MODULES=(nvidia nvidia-drm nvidia-modeset nvidia-peermem nvidia-uvm)
INITRAMFS_MODULES=(nvidia nvidia-modeset nvidia-uvm nvidia-drm)

cleanup()
{
    local rc=$?
    if mountpoint -q "$TARGET/dev" && ! umount -R "$TARGET/dev"; then
        printf 'synthetic chroot /dev mount could not be released\n' >&2
        rc=1
    fi
    mountpoint -q "$TARGET/dev" && return "$rc"
    rm -rf "$WORK_ROOT"
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

copy_binary()
{
    local binary="$1"
    local dependency
    install -D -m 0755 "$binary" "$TARGET$binary"
    while IFS= read -r dependency; do
        [[ -f "$dependency" ]] || continue
        install -D -m 0755 "$dependency" "$TARGET$dependency"
    done < <(ldd "$binary" | grep -oE '/[^[:space:]]+' | sed 's/[()]$//' | sort -u)
}

mkdir -p "$TARGET"/{bin,boot,dev,etc/modprobe.d,payload,run,usr/bin,usr/lib/modules/$KERNEL,usr/share/libalpm/hooks,var/lib}
mount --rbind /dev "$TARGET/dev"
mount --make-rslave "$TARGET/dev"
for binary in /bin/bash /usr/bin/cpio /usr/bin/cp /usr/bin/find /usr/bin/gzip \
    /usr/bin/mkdir /usr/bin/sleep /usr/bin/sort /usr/bin/touch /usr/bin/wc; do
    copy_binary "$binary"
done
for module in "${MODULES[@]}"; do
    printf 'verified fixture %s\n' "$module" > "$TARGET/payload/$module.ko.zst"
done
printf 'options nvidia-drm modeset=1\n' > "$TARGET/payload/nvidia.conf"

cat > "$TARGET/usr/bin/mkinitcpio-fixture" <<'EOF'
#!/bin/bash
set -euo pipefail
kernel="${1:?kernel is required}"
mkdir -p /run/initramfs/usr/lib/modules/"$kernel" /run/initramfs/etc/modprobe.d
for module in nvidia nvidia-modeset nvidia-uvm nvidia-drm; do
    cp "/usr/lib/modules/$kernel/$module.ko.zst" \
        "/run/initramfs/usr/lib/modules/$kernel/"
done
cp /etc/modprobe.d/nvidia.conf /run/initramfs/etc/modprobe.d/
if [[ "${INJECT_HOOK_FAILURE:-0}" == 1 ]]; then
    printf partial > /boot/initramfs-linux.img
    exit 71
fi
if [[ "${INJECT_HOOK_SLEEP:-0}" == 1 ]]; then
    : > /run/hook.ready
    sleep 300 &
    child=$!
    trap 'kill "$child" 2>/dev/null || true; exit 143' TERM INT
    wait "$child"
fi
(cd /run/initramfs && find . -exec touch -h -d @0 {} + &&
    find . -print0 | sort -z | cpio --reproducible --null -o -H newc 2>/dev/null) |
    gzip -n > /boot/initramfs-linux.img
cp /boot/initramfs-linux.img /boot/initramfs-linux-fallback.img
EOF
chmod 0755 "$TARGET/usr/bin/mkinitcpio-fixture"

cat > "$TARGET/usr/bin/pacman-fixture" <<'EOF'
#!/bin/bash
set -euo pipefail
kernel="${1:?kernel is required}"
required=0
for package in /payload/*.ko.zst /payload/nvidia.conf; do
    size="$(wc -c < "$package")"
    required=$((required + size))
done
(( required <= ${AVAILABLE_BYTES:?capacity is required} )) || exit 70
mkdir -p /usr/lib/modules/"$kernel" /etc/modprobe.d
cp /payload/*.ko.zst /usr/lib/modules/"$kernel"/
cp /payload/nvidia.conf /etc/modprobe.d/
/usr/bin/mkinitcpio-fixture "$kernel"
printf installed > /var/lib/nvidia-transaction
EOF
chmod 0755 "$TARGET/usr/bin/pacman-fixture"

snapshot_state()
{
    rm -rf "$BACKUP"
    mkdir -p "$BACKUP"
    cp -a "$TARGET/boot" "$TARGET/etc/modprobe.d" \
        "$TARGET/usr/lib/modules/$KERNEL" "$TARGET/var/lib" "$BACKUP/"
}

restore_state()
{
    rm -rf "$TARGET/boot" "$TARGET/etc/modprobe.d" \
        "$TARGET/usr/lib/modules/$KERNEL" "$TARGET/var/lib"
    mkdir -p "$TARGET/etc" "$TARGET/usr/lib/modules" "$TARGET/var"
    cp -a "$BACKUP/boot" "$TARGET/boot"
    cp -a "$BACKUP/modprobe.d" "$TARGET/etc/modprobe.d"
    cp -a "$BACKUP/$KERNEL" "$TARGET/usr/lib/modules/$KERNEL"
    cp -a "$BACKUP/lib" "$TARGET/var/lib"
}

run_transaction()
{
    local available="$1"
    local failure="${2:-0}"
    local sleeper="${3:-0}"
    snapshot_state
    if ! chroot "$TARGET" /bin/bash -c \
        "AVAILABLE_BYTES=$available INJECT_HOOK_FAILURE=$failure INJECT_HOOK_SLEEP=$sleeper /usr/bin/pacman-fixture '$KERNEL'"; then
        restore_state
        return 1
    fi
}

state_hash()
{
    find "$TARGET/boot" "$TARGET/etc/modprobe.d" "$TARGET/usr/lib/modules/$KERNEL" \
        "$TARGET/var/lib" -type f -print0 | sort -z | xargs -0 sha256sum
}

baseline="$(state_hash)"
! run_transaction 1
[[ "$(state_hash)" == "$baseline" ]]
! run_transaction 1048576 1
[[ "$(state_hash)" == "$baseline" ]]

snapshot_state
setsid chroot "$TARGET" /bin/bash -c \
    "AVAILABLE_BYTES=1048576 INJECT_HOOK_SLEEP=1 /usr/bin/pacman-fixture '$KERNEL'" &
transaction_pid=$!
for _ in $(seq 1 200); do
    [[ -e "$TARGET/run/hook.ready" ]] && break
    kill -0 "$transaction_pid" 2>/dev/null || break
    sleep 0.05
done
[[ -e "$TARGET/run/hook.ready" ]]
kill -TERM -- "-$transaction_pid"
! wait "$transaction_pid"
restore_state
rm -f "$TARGET/run/hook.ready"
[[ "$(state_hash)" == "$baseline" ]]

run_transaction 1048576
for image in initramfs-linux.img initramfs-linux-fallback.img; do
    archive_listing="$(gzip -dc "$TARGET/boot/$image" | cpio -it 2>/dev/null | sed 's|^\./||')"
    for module in "${INITRAMFS_MODULES[@]}"; do
        grep -qx "usr/lib/modules/$KERNEL/$module.ko.zst" <<<"$archive_listing"
    done
    ! grep -qx "usr/lib/modules/$KERNEL/nvidia-peermem.ko.zst" <<<"$archive_listing"
    grep -qx 'etc/modprobe.d/nvidia.conf' <<<"$archive_listing"
done
[[ -f "$TARGET/usr/lib/modules/$KERNEL/nvidia-peermem.ko.zst" ]]
[[ "$(<"$TARGET/var/lib/nvidia-transaction")" == installed ]]
success_hash="$(state_hash)"
run_transaction 1048576
[[ "$(state_hash)" == "$success_hash" ]]

printf '{"schemaVersion":1,"status":"passed","capacityRefusal":"passed","hookFailureRollback":"passed","signalRollback":"passed","initramfsContents":"passed","repeatExecution":"passed"}\n'
