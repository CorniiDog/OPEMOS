#!/usr/bin/env bash
set -euo pipefail

status=failed
pacman_status=not-run
mkinitcpio_status=not-run
cancellation_status=not-run
idempotency_status=not-run
initramfs_contract_status=not-run

report()
{
    local rc=$?
    printf '{"schemaVersion":1,"status":"%s","pacman":"%s","mkinitcpio":"%s","cancellation":"%s","idempotency":"%s","initramfsContract":"%s"}\n' \
        "$([[ "$rc" == 0 && "$status" == passed ]] && printf passed || printf failed)" \
        "$pacman_status" "$mkinitcpio_status" "$cancellation_status" "$idempotency_status" \
        "$initramfs_contract_status"
    return "$rc"
}
trap report EXIT

(( $(df --output=avail -B1 / | tail -n1) >= 536870912 ))
grep -Eq '^SigLevel[[:space:]]*=.*Required' /etc/pacman.conf
pacman -Sy --noconfirm
pacman -S --noconfirm --needed mkinitcpio
pacman -Qkk mkinitcpio >/dev/null
! pacman -S --noconfirm open-gpu-intentionally-missing-package
[[ ! -e /var/lib/pacman/db.lck ]]
pacman_status=passed

install -D -m 0644 /dev/stdin \
    /etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf <<'EOF'
options nvidia-drm modeset=1 fbdev=1
EOF
cat > /etc/mkinitcpio.conf.d/99-open-gpu-contract.conf <<'EOF'
FILES=(/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf)
EOF
mkinitcpio -P
mapfile -t images < <(find /boot -maxdepth 1 -type f -name 'initramfs-*.img' | sort)
(( ${#images[@]} > 0 ))
before="$(mktemp)"
after="$(mktemp)"
for image in "${images[@]}"; do
    lsinitcpio "$image"
done | sort -u > "$before"
grep -q 'usr/lib/modules/' "$before"
grep -qx 'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf' "$before"

kernel="$(uname -r)"
image="${images[0]}"
listing=/run/initramfs-contract.listing
lsinitcpio -l "$image" > "$listing"
mapfile -t module_names < <(
    grep -E "^usr/lib/modules/$kernel/.+\\.ko(\\.(gz|xz|zst|lz4|lzo))?$" "$listing" |
        sed -E 's|.*/||; s/\.(gz|xz|zst|lz4|lzo)$//' | sort -u | head -n5
)
(( ${#module_names[@]} == 5 ))
python3 /opt/open-gpu/lib/snapshot_target_execution.py --root / \
    --output /run/initramfs-execution.json
image_sha256="$(sha256sum "$image" | awk '{print $1}')"
verification_args=(
    --kernel "$kernel" --execution-manifest /run/initramfs-execution.json
    --config /etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf
    --image "$image" --listing "$listing" --image-sha256 "$image_sha256"
    --output /run/initramfs-verification.json
)
for module in "${module_names[@]}"; do verification_args+=(--module "$module"); done
python3 /opt/open-gpu/lib/verify_initramfs.py "${verification_args[@]}"
python3 -c 'import json; value=json.load(open("/run/initramfs-verification.json")); assert value["status"] == "verified" and len(value["images"]) == 1'
initramfs_contract_status=passed
mkinitcpio_status=passed

setsid bash -c 'while :; do mkinitcpio -P; done' \
    > /run/mkinitcpio-cancel.log 2>&1 &
generator_pid=$!
sleep 0.25
kill -TERM -- "-$generator_pid"
! wait "$generator_pid"
rm -f /run/mkinitcpio-cancel.log
[[ ! -e /var/lib/pacman/db.lck ]]
! find /boot -maxdepth 1 -type f \( -name '*.tmp' -o -name '*.partial' \) | grep -q .
cancellation_status=passed

mkinitcpio -P
for image in "${images[@]}"; do
    lsinitcpio "$image"
done | sort -u > "$after"
cmp "$before" "$after"
idempotency_status=passed
rm -f /run/initramfs-contract.listing /run/initramfs-execution.json \
    /run/initramfs-verification.json
status=passed
