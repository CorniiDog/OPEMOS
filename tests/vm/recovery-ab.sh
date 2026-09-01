#!/usr/bin/env bash
set -euo pipefail

WORK_ROOT="$(mktemp -d /tmp/open-gpu-recovery-ab.XXXXXX)"
IMAGE="$WORK_ROOT/recovery-ab.img"
MOUNT="$WORK_ROOT/mount"
READY="$WORK_ROOT/mutation.ready"
IMAGE_BYTES=$((512 * 1024 * 1024))

cleanup()
{
    local rc=$?
    if mountpoint -q "$MOUNT" && ! umount "$MOUNT"; then
        printf 'synthetic recovery filesystem could not be unmounted\n' >&2
        rc=1
    fi
    if ! mountpoint -q "$MOUNT"; then
        rm -rf "$WORK_ROOT"
    fi
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

truncate -s "$IMAGE_BYTES" "$IMAGE"
mkfs.btrfs -q "$IMAGE"
mkdir "$MOUNT"
mount -o loop,compress=zstd:3 "$IMAGE" "$MOUNT"
btrfs subvolume create "$MOUNT/root-A" >/dev/null
btrfs subvolume create "$MOUNT/root-B" >/dev/null
btrfs subvolume create "$MOUNT/recovery" >/dev/null
printf 'active-amd\n' > "$MOUNT/root-A/driver"
printf 'inactive-amd\n' > "$MOUNT/root-B/driver"
printf 'A\n' > "$MOUNT/boot-slot"
active_hash="$(sha256sum "$MOUNT/root-A/driver" | awk '{print $1}')"
inactive_hash="$(sha256sum "$MOUNT/root-B/driver" | awk '{print $1}')"

assert_original_slots()
{
    [[ "$(sha256sum "$MOUNT/root-A/driver" | awk '{print $1}')" == "$active_hash" ]]
    [[ "$(sha256sum "$MOUNT/root-B/driver" | awk '{print $1}')" == "$inactive_hash" ]]
    [[ "$(<"$MOUNT/boot-slot")" == A ]]
    [[ ! -e "$MOUNT/recovery/root-B.rollback" ]]
}

admit_mutation()
{
    local required_bytes="$1"
    local available_bytes
    available_bytes="$(( $(stat -f -c '%a * %S' "$MOUNT") ))"
    (( required_bytes <= available_bytes ))
}

mutate_inactive()
{
    local mode="$1"
    local rollback="$MOUNT/recovery/root-B.rollback"
    local committed=0
    local cancel_child=""
    rollback_mutation()
    {
        local rc=$?
        if (( committed == 0 )); then
            btrfs subvolume delete "$MOUNT/root-B" >/dev/null 2>&1 || true
            btrfs subvolume snapshot "$rollback" "$MOUNT/root-B" >/dev/null 2>&1 || true
        fi
        [[ ! -e "$rollback" ]] ||
            btrfs subvolume delete "$rollback" >/dev/null 2>&1 || true
        return "$rc"
    }
    trap rollback_mutation EXIT
    trap '[[ -z "$cancel_child" ]] || kill "$cancel_child" 2>/dev/null || true; exit 143' INT TERM
    btrfs subvolume snapshot -r "$MOUNT/root-B" "$rollback" >/dev/null
    printf 'nvidia-verified\n' > "$MOUNT/root-B/driver"
    if [[ "$mode" == cancel ]]; then
        : > "$READY"
        sleep 300 &
        cancel_child=$!
        wait "$cancel_child"
    fi
    printf 'B\n' > "$MOUNT/boot-slot"
    committed=1
    btrfs subvolume delete "$rollback" >/dev/null
    trap - EXIT INT TERM
}

# Resource refusal happens before a rollback snapshot or any slot mutation.
! admit_mutation $((IMAGE_BYTES * 4))
assert_original_slots

# Cancellation after the inactive slot changes restores it and preserves A/boot.
(mutate_inactive cancel) &
mutation_pid=$!
for _ in $(seq 1 100); do
    [[ -e "$READY" ]] && break
    kill -0 "$mutation_pid" 2>/dev/null || break
    sleep 0.05
done
[[ -e "$READY" ]]
kill -TERM "$mutation_pid"
! wait "$mutation_pid"
assert_original_slots

# Successful activation mutates only B, and repeating it is idempotent.
mutate_inactive success
[[ "$(<"$MOUNT/root-A/driver")" == active-amd ]]
[[ "$(<"$MOUNT/root-B/driver")" == nvidia-verified ]]
[[ "$(<"$MOUNT/boot-slot")" == B ]]
success_hash="$(sha256sum "$MOUNT/root-B/driver" | awk '{print $1}')"
mutate_inactive success
[[ "$(sha256sum "$MOUNT/root-B/driver" | awk '{print $1}')" == "$success_hash" ]]
[[ "$(<"$MOUNT/boot-slot")" == B ]]
[[ ! -e "$MOUNT/recovery/root-B.rollback" ]]

printf '{"schemaVersion":1,"status":"passed","resourceRefusal":"passed","signalRollback":"passed","activeSlotPreserved":"passed","repeatExecution":"passed"}\n'
