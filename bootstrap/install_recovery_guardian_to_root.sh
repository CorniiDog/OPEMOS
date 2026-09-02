#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT=""
REVISION=""
NVIDIA=""

usage() { printf 'Usage: %s --root PATH --support-revision COMMIT --nvidia VERSION\n' "$0"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --support-revision) REVISION="$2"; shift 2 ;;
        --nvidia) NVIDIA="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ -d "$ROOT" && ! -L "$ROOT" ]] || { echo "Target root is unsafe." >&2; exit 1; }
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "Support revision is malformed." >&2; exit 1; }
[[ "$NVIDIA" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { echo "NVIDIA version is malformed." >&2; exit 1; }

DEST="$ROOT/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"
OWNERSHIP=(-o 0 -g 0)
[[ "${PROJECT_TEST_MODE:-0}" != 1 ]] || OWNERSHIP=(-o "$(id -u)" -g "$(id -g)")
install -d "${OWNERSHIP[@]}" -m 0755 "$DEST/bootstrap" "$DEST/lib" \
    "$ROOT/etc/systemd/system/multi-user.target.wants" \
    "$ROOT/etc/systemd/system/timers.target.wants" \
    "$ROOT/etc/NetworkManager/dispatcher.d" "$ROOT/etc/atomic-update.conf.d"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" "$DEST/bootstrap/recoveryctl.sh"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/online_install.sh" "$DEST/bootstrap/online_install.sh"
install "${OWNERSHIP[@]}" -m 0644 "$SUPPORT_ROOT/lib/common.sh" "$DEST/lib/common.sh"
for helper in recovery_status.py recovery_transaction.py recovery_release_plan.py update_recovery_grub_args.py; do
    install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/lib/$helper" "$DEST/lib/$helper"
done
printf '%s\n' "$REVISION" > "$DEST/support-revision"
printf '%s\n' "$NVIDIA" > "$DEST/nvidia-version"
chmod 0644 "$DEST/support-revision" "$DEST/nvidia-version"

sed "s|@DEST@|/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery|g" \
    "$SUPPORT_ROOT/support/recovery/opemos-nvidia-guardian.service.in" \
    > "$ROOT/etc/systemd/system/opemos-nvidia-guardian.service"
sed "s|@DEST@|/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery|g" \
    "$SUPPORT_ROOT/support/recovery/opemos-nvidia-repair.service.in" \
    > "$ROOT/etc/systemd/system/opemos-nvidia-repair.service"
install "${OWNERSHIP[@]}" -m 0644 "$SUPPORT_ROOT/support/recovery/opemos-nvidia-repair.timer" \
    "$ROOT/etc/systemd/system/opemos-nvidia-repair.timer"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/support/recovery/90-opemos-nvidia-repair" \
    "$ROOT/etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair"
install "${OWNERSHIP[@]}" -m 0644 "$SUPPORT_ROOT/support/recovery/90-opemos-nvidia-guardian.conf" \
    "$ROOT/etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf"
ln -sfn ../opemos-nvidia-guardian.service \
    "$ROOT/etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service"
ln -sfn ../opemos-nvidia-repair.timer \
    "$ROOT/etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer"
