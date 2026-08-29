#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

YES=0

usage()
{
    printf "Usage: %s [-y]\n" "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_steamos
need_cmd sudo
need_cmd realpath
need_cmd modinfo
need_cmd depmod

CURRENT_KERNEL="$(get_kernel_version)"
STATE_ROOT="$(project_system_path "/var/lib/open-gpu-kernel-modules-steamos-support")"
TARGET_DIR="$(project_system_path "/usr/lib/modules/${CURRENT_KERNEL}/updates/open-gpu-kernel-modules-steamos")"

[[ -d "$TARGET_DIR" ]] ||
    die "NVIDIA open kernel module directory is not installed: $TARGET_DIR"

TARGET_REAL="$(realpath -m "$TARGET_DIR")"
RESOLVED_BEFORE="$(modinfo -n nvidia 2>/dev/null || true)"
RESOLVED_BEFORE_REAL="$(realpath -m "$RESOLVED_BEFORE")"

case "$RESOLVED_BEFORE_REAL" in
    "$TARGET_REAL"/*) ;;
    *)
        die "NVIDIA currently resolves outside this installation: ${RESOLVED_BEFORE:-unknown}"
        ;;
esac

printf "\n[%s] Uninstall candidate\n" "$PROJECT_NAME"
printf "[%s]   Kernel:  %s\n" "$PROJECT_NAME" "$CURRENT_KERNEL"
printf "[%s]   Current: %s\n\n" "$PROJECT_NAME" "$RESOLVED_BEFORE"

if [[ "$YES" != "1" ]]; then
    read -r -p "[$PROJECT_NAME] Remove the NVIDIA open kernel modules and restore previous module resolution? [y/N]: " REPLY
    case "$REPLY" in
        y|Y|yes|YES|Yes) ;;
        *) die "Uninstall cancelled." ;;
    esac
fi

log "Requesting administrator privileges..."
sudo -v
acquire_lifecycle_lock

STAMP="$(date +%Y%m%d-%H%M%S)"
CACHE_ROOT="${HOME}/.cache/${PROJECT_NAME}"
BACKUP_ROOT="${CACHE_ROOT}/backups/${CURRENT_KERNEL}"
mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/uninstall-${STAMP}.XXXXXX")"

RO_WAS_ENABLED=0
TARGET_TOUCHED=0
UNINSTALL_COMPLETE=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 ||
            warn "Failed to re-enable SteamOS read-only mode."
        RO_WAS_ENABLED=0
    fi
}

cleanup()
{
    local rc=$?
    trap - EXIT INT TERM

    if [[ "$UNINSTALL_COMPLETE" != "1" && "$TARGET_TOUCHED" == "1" ]]; then
        warn "Uninstall failed; restoring NVIDIA open kernel modules."

        if [[ -d "$BACKUP_DIR/modules" ]]; then
            sudo mkdir -p "$(dirname "$TARGET_DIR")" || true
            sudo rm -rf "$TARGET_DIR" || true
            sudo cp -a "$BACKUP_DIR/modules" "$TARGET_DIR" || true
            sudo depmod -a "$CURRENT_KERNEL" || true

            if command -v mkinitcpio >/dev/null 2>&1; then
                sudo mkinitcpio -P >/dev/null 2>&1 || true
            fi
        fi

        if [[ -d "$BACKUP_DIR/state" ]]; then
            sudo mkdir -p "$STATE_ROOT" || true
            sudo cp -a "$BACKUP_DIR/state/." "$STATE_ROOT/" || true
        fi
    fi

    restore_readonly
    exit "$rc"
}

trap cleanup EXIT
trap "exit 130" INT
trap "exit 143" TERM

if command -v steamos-readonly >/dev/null 2>&1 &&
   steamos-readonly status 2>/dev/null | grep -qi enabled; then
    log "Temporarily disabling SteamOS read-only mode..."
    RO_WAS_ENABLED=1
    sudo steamos-readonly disable
fi

mkdir -p "$BACKUP_DIR/state"
sudo cp -a "$TARGET_DIR" "$BACKUP_DIR/modules"

for state_file in \
    installed-build-info.txt \
    installed-archive.txt \
    installed-kernel.txt \
    installed-nvidia.txt
do
    if [[ -f "${STATE_ROOT}/${state_file}" ]]; then
        sudo cp -a "${STATE_ROOT}/${state_file}" "$BACKUP_DIR/state/"
    fi
done

sudo chown -R "$USER":"$(id -gn)" "$BACKUP_DIR"

TARGET_TOUCHED=1
sudo rm -rf "$TARGET_DIR"

log "Refreshing module dependency database..."
sudo depmod -a "$CURRENT_KERNEL"

RESOLVED_AFTER="$(modinfo -n nvidia 2>/dev/null || true)"

[[ -n "$RESOLVED_AFTER" ]] ||
    die "No fallback NVIDIA module was found after removing the open modules."

RESOLVED_AFTER_REAL="$(realpath -m "$RESOLVED_AFTER")"

case "$RESOLVED_AFTER_REAL" in
    "$TARGET_REAL"/*)
        die "depmod still resolves NVIDIA to the removed module directory: $RESOLVED_AFTER"
        ;;
esac

log "Fallback NVIDIA module resolved to:"
log "$RESOLVED_AFTER"

if command -v mkinitcpio >/dev/null 2>&1; then
    log "Rebuilding initramfs..."
    sudo mkinitcpio -P
fi

sudo rm -f     "${STATE_ROOT}/installed-build-info.txt"     "${STATE_ROOT}/installed-archive.txt"     "${STATE_ROOT}/installed-kernel.txt"     "${STATE_ROOT}/installed-nvidia.txt"

UNINSTALL_COMPLETE=1
restore_readonly
trap - EXIT INT TERM

ok "NVIDIA open kernel modules removed successfully."
log "Fallback NVIDIA module: $RESOLVED_AFTER"
log "Reboot is required before the fallback NVIDIA modules will be used."
