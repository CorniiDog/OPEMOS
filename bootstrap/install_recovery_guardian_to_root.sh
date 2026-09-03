#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT=""
REVISION=""
NVIDIA=""
INTERSTITIAL_BINARY=""
INTERSTITIAL_SHA256=""

usage()
{
    printf 'Usage: %s --root PATH --support-revision COMMIT --nvidia VERSION [--interstitial-binary FILE --interstitial-sha256 HASH]\n' "$0"
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; ROOT="$2"; shift 2 ;;
        --support-revision) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; REVISION="$2"; shift 2 ;;
        --nvidia) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; NVIDIA="$2"; shift 2 ;;
        --interstitial-binary) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_BINARY="$2"; shift 2 ;;
        --interstitial-sha256) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_SHA256="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ -d "$ROOT" && ! -L "$ROOT" ]] || { echo "Target root is unsafe." >&2; exit 1; }
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "Support revision is malformed." >&2; exit 1; }
[[ "$NVIDIA" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { echo "NVIDIA version is malformed." >&2; exit 1; }
if ! { [[ -z "$INTERSTITIAL_BINARY" && -z "$INTERSTITIAL_SHA256" ]] ||
       [[ -n "$INTERSTITIAL_BINARY" && -n "$INTERSTITIAL_SHA256" ]]; }; then
    echo "Interstitial binary and SHA-256 must be supplied together." >&2
    exit 2
fi

STAGING=""
cleanup_staging()
{
    [[ -z "$STAGING" ]] || rm -rf "$STAGING"
}
trap cleanup_staging EXIT
trap 'cleanup_staging; exit 130' INT
trap 'cleanup_staging; exit 143' TERM
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/opemos-interstitial.XXXXXX")"
    python3 "$SUPPORT_ROOT/lib/snapshot_install_input.py" \
        --source "$INTERSTITIAL_BINARY" --destination "$STAGING/opemos-interstitial" \
        --max-bytes 33554432
    python3 "$SUPPORT_ROOT/lib/validate_interstitial_binary.py" \
        --binary "$STAGING/opemos-interstitial" --sha256 "$INTERSTITIAL_SHA256" >/dev/null
fi

DEST="$ROOT/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"
PATH_CHECK_ARGS=(--root "$ROOT")
[[ "${PROJECT_TEST_MODE:-0}" != 1 ]] || PATH_CHECK_ARGS+=(--test-owner)
python3 "$SUPPORT_ROOT/lib/validate_recovery_install_path.py" "${PATH_CHECK_ARGS[@]}" \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/support-revision \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/interstitial.sha256 \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/recoveryctl.sh \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/launch_desktop_companion.sh \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/run_guardian_with_interstitial.sh \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/launch_interstitial.sh \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/recovery_status.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/recovery_fallback_state.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/desktop_update_generations.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/open_opemos_contract.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/interstitial_progress.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/lib/validate_interstitial_binary.py \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bin/opemos-interstitial \
    --path home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/trust/desktop-update-signers.json \
    --path etc/systemd/system/opemos-nvidia-guardian.service \
    --path etc/systemd/system/opemos-interstitial.service \
    --path etc/systemd/system/opemos-nvidia-repair.service \
    --path etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair \
    --expected-symlink etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service=../opemos-nvidia-guardian.service \
    --expected-symlink etc/systemd/system/multi-user.target.wants/opemos-interstitial.service=../opemos-interstitial.service \
    --expected-symlink etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer=../opemos-nvidia-repair.timer
OWNERSHIP=(-o 0 -g 0)
[[ "${PROJECT_TEST_MODE:-0}" != 1 ]] || OWNERSHIP=(-o "$(id -u)" -g "$(id -g)")
install -d "${OWNERSHIP[@]}" -m 0755 "$DEST/bin" "$DEST/bootstrap" "$DEST/lib" "$DEST/trust" \
    "$ROOT/etc/systemd/system/multi-user.target.wants" \
    "$ROOT/etc/systemd/system/timers.target.wants" \
    "$ROOT/etc/NetworkManager/dispatcher.d" "$ROOT/etc/atomic-update.conf.d"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" "$DEST/bootstrap/recoveryctl.sh"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/launch_desktop_companion.sh" "$DEST/bootstrap/launch_desktop_companion.sh"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/run_guardian_with_interstitial.sh" "$DEST/bootstrap/run_guardian_with_interstitial.sh"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/launch_interstitial.sh" "$DEST/bootstrap/launch_interstitial.sh"
install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/bootstrap/online_install.sh" "$DEST/bootstrap/online_install.sh"
install "${OWNERSHIP[@]}" -m 0644 "$SUPPORT_ROOT/lib/common.sh" "$DEST/lib/common.sh"
for helper in recovery_status.py recovery_fallback_state.py recovery_transaction.py recovery_release_plan.py update_recovery_grub_args.py open_opemos_contract.py validate_recovery_install_path.py desktop_update_generations.py interstitial_progress.py validate_interstitial_binary.py; do
    install "${OWNERSHIP[@]}" -m 0755 "$SUPPORT_ROOT/lib/$helper" "$DEST/lib/$helper"
done
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    install "${OWNERSHIP[@]}" -m 0755 "$STAGING/opemos-interstitial" "$DEST/bin/opemos-interstitial"
    printf '%s\n' "$INTERSTITIAL_SHA256" > "$DEST/interstitial.sha256"
    chmod 0644 "$DEST/interstitial.sha256"
fi
install "${OWNERSHIP[@]}" -m 0644 "$SUPPORT_ROOT/trust/desktop-update-signers.json" "$DEST/trust/desktop-update-signers.json"
printf '%s\n' "$REVISION" > "$DEST/support-revision"
printf '%s\n' "$NVIDIA" > "$DEST/nvidia-version"
chmod 0644 "$DEST/support-revision" "$DEST/nvidia-version"

sed "s|@DEST@|/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery|g" \
    "$SUPPORT_ROOT/support/recovery/opemos-nvidia-guardian.service.in" \
    > "$ROOT/etc/systemd/system/opemos-nvidia-guardian.service"
sed "s|@DEST@|/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery|g" \
    "$SUPPORT_ROOT/support/recovery/opemos-interstitial.service.in" \
    > "$ROOT/etc/systemd/system/opemos-interstitial.service"
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
ln -sfn ../opemos-interstitial.service \
    "$ROOT/etc/systemd/system/multi-user.target.wants/opemos-interstitial.service"
ln -sfn ../opemos-nvidia-repair.timer \
    "$ROOT/etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer"
