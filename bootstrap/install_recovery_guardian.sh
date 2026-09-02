#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "$SUPPORT_ROOT/lib/common.sh"

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
    printf 'Usage: %s\n' "$0"
    printf 'Install or refresh the persistent OPEMOS NVIDIA recovery guardian.\n'
    exit 0
fi
[[ $# -eq 0 ]] || die "Unknown argument: $1"

require_steamos
need_cmd sudo
sudo -v
acquire_lifecycle_lock

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
    --path "${DEST#/}/bootstrap/recoveryctl.sh" \
    --path "${DEST#/}/bootstrap/launch_desktop_companion.sh" \
    --path "${DEST#/}/lib/recovery_status.py" \
    --path "${DEST#/}/lib/desktop_update_generations.py" \
    --path "${DEST#/}/lib/open_opemos_contract.py" \
    --path "${DEST#/}/trust/desktop-update-signers.json" \
    --path var/lib/open-gpu-kernel-modules-steamos-support/recovery/state.json \
    --path etc/systemd/system/opemos-nvidia-guardian.service \
    --path etc/systemd/system/opemos-nvidia-repair.service \
    --path etc/systemd/system/opemos-nvidia-repair.timer \
    --path etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair \
    --path etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf \
    --expected-symlink etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service=../opemos-nvidia-guardian.service \
    --expected-symlink etc/systemd/system/timers.target.wants/opemos-nvidia-repair.timer=../opemos-nvidia-repair.timer
RO_WAS_ENABLED=0
cleanup()
{
    if [[ "$RO_WAS_ENABLED" == 1 ]]; then sudo steamos-readonly enable >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM
if command -v steamos-readonly >/dev/null 2>&1 && steamos-readonly status 2>/dev/null | grep -qi enabled; then
    sudo steamos-readonly disable
    RO_WAS_ENABLED=1
fi
sudo install -d -o root -g root -m 0755 "$DEST/bootstrap" "$DEST/lib" "$DEST/trust" \
    /etc/systemd/system /etc/atomic-update.conf.d \
    /var/lib/open-gpu-kernel-modules-steamos-support/recovery
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" "$DEST/bootstrap/recoveryctl.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/launch_desktop_companion.sh" "$DEST/bootstrap/launch_desktop_companion.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/bootstrap/online_install.sh" "$DEST/bootstrap/online_install.sh"
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/lib/common.sh" "$DEST/lib/common.sh"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_status.py" "$DEST/lib/recovery_status.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/desktop_update_generations.py" "$DEST/lib/desktop_update_generations.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/update_recovery_grub_args.py" "$DEST/lib/update_recovery_grub_args.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_transaction.py" "$DEST/lib/recovery_transaction.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/recovery_release_plan.py" "$DEST/lib/recovery_release_plan.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/open_opemos_contract.py" "$DEST/lib/open_opemos_contract.py"
sudo install -o root -g root -m 0755 "$SUPPORT_ROOT/lib/validate_recovery_install_path.py" "$DEST/lib/validate_recovery_install_path.py"
sudo install -o root -g root -m 0644 "$SUPPORT_ROOT/trust/desktop-update-signers.json" "$DEST/trust/desktop-update-signers.json"
printf '%s\n' "$REVISION" | sudo tee "$DEST/support-revision" >/dev/null
sudo chown root:root "$DEST/support-revision"
sudo chmod 0644 "$DEST/support-revision"
NVIDIA_VERSION="$(get_nvidia_version)"
printf '%s\n' "$NVIDIA_VERSION" | sudo tee "$DEST/nvidia-version" >/dev/null
sudo chown root:root "$DEST/nvidia-version"
sudo chmod 0644 "$DEST/nvidia-version"
sed "s|@DEST@|$DEST|g" "$SUPPORT_ROOT/support/recovery/opemos-nvidia-guardian.service.in" | \
    sudo tee /etc/systemd/system/opemos-nvidia-guardian.service >/dev/null
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
sudo systemctl enable opemos-nvidia-repair.timer
cleanup
trap - EXIT INT TERM
ok "Installed exact-kernel boot guardian bound to support revision $REVISION."
