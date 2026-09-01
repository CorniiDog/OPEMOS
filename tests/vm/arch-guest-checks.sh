#!/usr/bin/env bash
set -euo pipefail

status=failed
pacman_status=not-run
mkinitcpio_status=not-run
cancellation_status=not-run
idempotency_status=not-run

report()
{
    local rc=$?
    printf '{"schemaVersion":1,"status":"%s","pacman":"%s","mkinitcpio":"%s","cancellation":"%s","idempotency":"%s"}\n' \
        "$([[ "$rc" == 0 && "$status" == passed ]] && printf passed || printf failed)" \
        "$pacman_status" "$mkinitcpio_status" "$cancellation_status" "$idempotency_status"
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

mkinitcpio -P
mapfile -t images < <(find /boot -maxdepth 1 -type f -name 'initramfs-*.img' | sort)
(( ${#images[@]} > 0 ))
before="$(mktemp)"
after="$(mktemp)"
for image in "${images[@]}"; do
    lsinitcpio "$image"
done | sort -u > "$before"
grep -q 'usr/lib/modules/' "$before"
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
status=passed
