#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "$SUPPORT_ROOT/lib/common.sh"

INTERSTITIAL_BINARY=""
INTERSTITIAL_SHA256=""
usage()
{
    printf 'Usage: %s [--interstitial-binary FILE --interstitial-sha256 HASH]\n' "$0"
    printf 'Install or refresh the persistent OPEMOS NVIDIA recovery guardian.\n'
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --interstitial-binary) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_BINARY="$2"; shift 2 ;;
        --interstitial-sha256) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_SHA256="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done
if ! { [[ -z "$INTERSTITIAL_BINARY" && -z "$INTERSTITIAL_SHA256" ]] ||
       [[ -n "$INTERSTITIAL_BINARY" && -n "$INTERSTITIAL_SHA256" ]]; }; then
    die "Interstitial binary and SHA-256 must be supplied together."
fi

require_steamos
need_cmd sudo
sudo -v
acquire_lifecycle_lock

STAGING=""
RO_WAS_ENABLED=0
cleanup()
{
    if [[ "$RO_WAS_ENABLED" == 1 ]]; then sudo steamos-readonly enable >/dev/null 2>&1 || true; fi
    [[ -z "$STAGING" ]] || rm -rf "$STAGING"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/opemos-interstitial.XXXXXX")"
    python3 "$SUPPORT_ROOT/lib/snapshot_install_input.py" \
        --source "$INTERSTITIAL_BINARY" --destination "$STAGING/opemos-interstitial" \
        --max-bytes 33554432
    python3 "$SUPPORT_ROOT/lib/validate_interstitial_binary.py" \
        --binary "$STAGING/opemos-interstitial" --sha256 "$INTERSTITIAL_SHA256" >/dev/null
fi

REVISION="$(git -C "$SUPPORT_ROOT" rev-parse HEAD)"
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die "Could not bind guardian to an exact support revision."
# The inactive rootfs is replaced by SteamOS atomic updates. Keep the bounded,
# root-owned executable snapshot on the shared home filesystem and migrate only
# its systemd/configuration entry points through Valve's supported /etc keep
# list. A service stored solely in the current /usr would disappear precisely
# when the guardian is needed.
DEST=/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery
python3 "$SUPPORT_ROOT/lib/validate_recovery_install_path.py" --root / \
    --path "${DEST#/}/support-revision" --path "${DEST#/}/nvidia-version" \
    --path "${DEST#/}/interstitial.sha256" \
    --path "${DEST#/}/bootstrap/recoveryctl.sh" \
    --path "${DEST#/}/bootstrap/launch_desktop_companion.sh" \
    --path "${DEST#/}/bootstrap/launch_interstitial.sh" \
    --path "${DEST#/}/bootstrap/run_guardian_with_interstitial.sh" \
    --path "${DEST#/}/bin/opemos-interstitial" \
    --path "${DEST#/}/lib/recovery_status.py" \
    --path "${DEST#/}/lib/desktop_update_generations.py" \
    --path "${DEST#/}/lib/open_opemos_contract.py" \
    --path "${DEST#/}/lib/interstitial_progress.py" \
    --path "${DEST#/}/lib/validate_interstitial_binary.py" \
    --path "${DEST#/}/trust/desktop-update-signers.json" \
    --path var/lib/open-gpu-kernel-modules-steamos-support/recovery/state.json \
    --path etc/systemd/system/opemos-nvidia-guardian.service \
    --path etc/systemd/system/opemos-interstitial.service \
    --path etc/systemd/system/opemos-nvidia-repair.service \
    --path etc/systemd/system/opemos-nvidia-repair.timer \
    --path etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair \
    --path etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf \
    --expected-symlink etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service=../opemos-nvidia-guardian.service \
    --expected-symlink etc/systemd/system/multi-user.target.wants/opemos-interstitial.service=../opemos-interstitial.service \
    --expected-symlink etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer=../opemos-nvidia-repair.timer
if command -v steamos-readonly >/dev/null 2>&1 && steamos-readonly status 2>/dev/null | grep -qi enabled; then
    sudo steamos-readonly disable
    RO_WAS_ENABLED=1
fi
sudo install -d -o root -g root -m 0755 "$DEST/bin" "$DEST/bootstrap" "$DEST/lib" "$DEST/trust" \
    /etc/systemd/system /etc/atomic-update.conf.d \
    /var/lib/open-gpu-kernel-modules-steamos-support/recovery
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" "$DEST/bootstrap/recoveryctl.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/launch_desktop_companion.sh" "$DEST/bootstrap/launch_desktop_companion.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/launch_interstitial.sh" "$DEST/bootstrap/launch_interstitial.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/run_guardian_with_interstitial.sh" "$DEST/bootstrap/run_guardian_with_interstitial.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/online_install.sh" "$DEST/bootstrap/online_install.sh"
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/lib/common.sh" "$DEST/lib/common.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_status.py" "$DEST/lib/recovery_status.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/desktop_update_generations.py" "$DEST/lib/desktop_update_generations.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/update_recovery_grub_args.py" "$DEST/lib/update_recovery_grub_args.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_transaction.py" "$DEST/lib/recovery_transaction.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_release_plan.py" "$DEST/lib/recovery_release_plan.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/open_opemos_contract.py" "$DEST/lib/open_opemos_contract.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/interstitial_progress.py" "$DEST/lib/interstitial_progress.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/validate_interstitial_binary.py" "$DEST/lib/validate_interstitial_binary.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/validate_recovery_install_path.py" "$DEST/lib/validate_recovery_install_path.py"
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/trust/desktop-update-signers.json" "$DEST/trust/desktop-update-signers.json"
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    sudo install -o root -g root -m 0755 "$STAGING/opemos-interstitial" "$DEST/bin/opemos-interstitial"
    printf '%s\n' "$INTERSTITIAL_SHA256" | sudo tee "$DEST/interstitial.sha256" >/dev/null
    sudo chown root:root "$DEST/interstitial.sha256"
    sudo chmod 0644 "$DEST/interstitial.sha256"
fi
printf '%s\n' "$REVISION" | sudo tee "$DEST/support-revision" >/dev/null
sudo chown root:root "$DEST/support-revision"
sudo chmod 0644 "$DEST/support-revision"
NVIDIA_VERSION="$(get_nvidia_version)"
printf '%s\n' "$NVIDIA_VERSION" | sudo tee "$DEST/nvidia-version" >/dev/null
sudo chown root:root "$DEST/nvidia-version"
sudo chmod 0644 "$DEST/nvidia-version"
sed "s|@DEST@|$DEST|g" "$SUPPORT_ROOT/support/recovery/opemos-nvidia-guardian.service.in" | \
    sudo tee /etc/systemd/system/opemos-nvidia-guardian.service >/dev/null
sed "s|@DEST@|$DEST|g" "$SUPPORT_ROOT/support/recovery/opemos-interstitial.service.in" | \
    sudo tee /etc/systemd/system/opemos-interstitial.service >/dev/null
sed "s|@DEST@|$DEST|g" "$SUPPORT_ROOT/support/recovery/opemos-nvidia-repair.service.in" | \
    sudo tee /etc/systemd/system/opemos-nvidia-repair.service >/dev/null
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/support/recovery/opemos-nvidia-repair.timer" \
    /etc/systemd/system/opemos-nvidia-repair.timer
sudo install -d -o root -g root -m 0755 /etc/NetworkManager/dispatcher.d
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/support/recovery/90-opemos-nvidia-repair" \
    /etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/support/recovery/90-opemos-nvidia-guardian.conf" \
    /etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf
sudo systemctl daemon-reload
sudo systemctl enable opemos-nvidia-guardian.service
sudo systemctl enable opemos-interstitial.service
sudo systemctl enable opemos-nvidia-repair.timer
cleanup
trap - EXIT INT TERM
ok "Installed exact-kernel boot guardian bound to support revision $REVISION."
