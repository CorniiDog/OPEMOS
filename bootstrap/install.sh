#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

ARCHIVE=""
CHECKSUM=""
FUZZY=0
YES=0

usage()
{
    printf 'Usage: %s --archive FILE [--checksum FILE] [--fuzzy] [-y]\n' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --archive) [[ $# -ge 2 ]] || die "--archive requires a file."; ARCHIVE="$2"; shift 2 ;;
        --checksum) [[ $# -ge 2 ]] || die "--checksum requires a file."; CHECKSUM="$2"; shift 2 ;;
        --fuzzy) FUZZY=1; shift ;;
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "$ARCHIVE" ]] || die "--archive is required."
ARCHIVE="$(realpath "$ARCHIVE")"
[[ -f "$ARCHIVE" ]] || die "Archive not found: $ARCHIVE"

if [[ -z "$CHECKSUM" && -f "${ARCHIVE}.sha256" ]]; then
    CHECKSUM="${ARCHIVE}.sha256"
fi
if [[ -n "$CHECKSUM" ]]; then
    CHECKSUM="$(realpath "$CHECKSUM")"
    [[ -f "$CHECKSUM" ]] || die "Checksum not found: $CHECKSUM"
fi

require_steamos
need_cmd sudo
need_cmd tar
need_cmd realpath
need_cmd sha256sum
need_cmd modinfo
need_cmd depmod
need_cmd find
need_cmd install

CURRENT_STEAMOS="$(get_steamos_version)"
CURRENT_KERNEL="$(get_kernel_version)"
CURRENT_NVIDIA="$(get_nvidia_version)"

if [[ -n "$CHECKSUM" ]]; then
    EXPECTED_SHA="$(awk '{print $1}' "$CHECKSUM" | head -n1)"
    [[ "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid archive checksum."
    ACTUAL_SHA="$(sha256_file "$ARCHIVE")"
    [[ "${EXPECTED_SHA,,}" == "${ACTUAL_SHA,,}" ]] || die "Archive checksum verification failed."
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

while IFS= read -r entry; do
    [[ "$entry" != /* ]] || die "Archive contains absolute path: $entry"
    [[ "$entry" != ".." && "$entry" != ../* && "$entry" != */../* && "$entry" != */.. ]] ||
        die "Archive contains path traversal: $entry"
done < <(tar -tzf "$ARCHIVE")

tar -xzf "$ARCHIVE" -C "$TMP"

INFO="$TMP/BUILD-INFO.txt"
MODULE_DIR="$TMP/modules"
[[ -f "$INFO" ]] || die "Archive does not contain BUILD-INFO.txt."
[[ -d "$MODULE_DIR" ]] || die "Archive does not contain modules/."

metadata()
{
    sed -n "s/^${1}=//p" "$INFO" | head -n1
}

BUILD_STEAMOS="$(metadata steamos_version)"
BUILD_KERNEL="$(metadata kernel_version)"
BUILD_NVIDIA="$(metadata nvidia_version)"

[[ -n "$BUILD_STEAMOS" && -n "$BUILD_KERNEL" && -n "$BUILD_NVIDIA" ]] ||
    die "Release metadata is incomplete."

[[ "$BUILD_KERNEL" == "$CURRENT_KERNEL" ]] ||
    die "Kernel mismatch: release is ${BUILD_KERNEL}; running kernel is ${CURRENT_KERNEL}. Refusing to install."

[[ "$BUILD_NVIDIA" == "$CURRENT_NVIDIA" ]] ||
    die "NVIDIA mismatch: release is ${BUILD_NVIDIA}; installed userspace is ${CURRENT_NVIDIA}. Refusing to install."

if [[ "$FUZZY" == "0" ]]; then
    [[ "$BUILD_STEAMOS" == "$CURRENT_STEAMOS" ]] ||
        die "SteamOS mismatch: release is ${BUILD_STEAMOS}; system is ${CURRENT_STEAMOS}. Use --fuzzy for a nearby published SteamOS build."
fi

mapfile -t MODULES < <(find "$MODULE_DIR" -maxdepth 1 -type f -name '*.ko' | sort)
(( ${#MODULES[@]} > 0 )) || die "Release contains no kernel modules."

for module in "${MODULES[@]}"; do
    VM="$(modinfo -F vermagic "$module")"
    VM_KERNEL="${VM%% *}"
    [[ "$VM_KERNEL" == "$CURRENT_KERNEL" ]] ||
        die "$(basename "$module") vermagic is ${VM_KERNEL}; expected ${CURRENT_KERNEL}."

    MODVER="$(modinfo -F version "$module" 2>/dev/null || true)"
    if [[ -n "$MODVER" ]]; then
        [[ "$MODVER" == "$BUILD_NVIDIA" ]] ||
            die "$(basename "$module") reports NVIDIA ${MODVER}; metadata says ${BUILD_NVIDIA}."
    fi
done

printf '\n[%s] Install candidate\n' "$PROJECT_NAME"
printf '[%s]   SteamOS build: %s (system %s)\n' "$PROJECT_NAME" "$BUILD_STEAMOS" "$CURRENT_STEAMOS"
printf '[%s]   Kernel:        %s\n' "$PROJECT_NAME" "$BUILD_KERNEL"
printf '[%s]   NVIDIA:        %s\n' "$PROJECT_NAME" "$BUILD_NVIDIA"
printf '[%s]   Archive:       %s\n\n' "$PROJECT_NAME" "$ARCHIVE"

if [[ "$YES" != "1" ]]; then
    read -r -p "[$PROJECT_NAME] Install these kernel modules and rebuild module dependencies? [y/N]: " REPLY
    case "$REPLY" in y|Y|yes|YES|Yes) ;; *) die "Install cancelled." ;; esac
fi

log "Requesting administrator privileges..."
sudo -v

TARGET_DIR="/usr/lib/modules/${CURRENT_KERNEL}/updates/open-gpu-kernel-modules-steamos"
STATE_ROOT="/var/lib/open-gpu-kernel-modules-steamos-support"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${STATE_ROOT}/backups/${CURRENT_KERNEL}/${STAMP}"
RO_WAS_ENABLED=0
INSTALLED=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 || warn "Failed to re-enable SteamOS read-only mode."
        RO_WAS_ENABLED=0
    fi
}

rollback()
{
    local rc=$?
    if [[ "$INSTALLED" == "1" ]]; then
        warn "Install failed; restoring previous updates directory."
        sudo rm -rf "$TARGET_DIR" || true
        if [[ -d "$BACKUP_DIR/modules" ]]; then
            sudo mkdir -p "$(dirname "$TARGET_DIR")" || true
            sudo cp -a "$BACKUP_DIR/modules" "$TARGET_DIR" || true
        fi
        sudo depmod -a "$CURRENT_KERNEL" || true
        if command -v mkinitcpio >/dev/null 2>&1; then
            sudo mkinitcpio -P >/dev/null 2>&1 || true
        fi
    fi
    restore_readonly
    exit "$rc"
}
trap rollback ERR INT TERM

if command -v steamos-readonly >/dev/null 2>&1 &&
   steamos-readonly status 2>/dev/null | grep -qi enabled; then
    log "Temporarily disabling SteamOS read-only mode..."
    sudo steamos-readonly disable
    RO_WAS_ENABLED=1
fi

sudo mkdir -p "$BACKUP_DIR"
if [[ -d "$TARGET_DIR" ]]; then
    sudo cp -a "$TARGET_DIR" "$BACKUP_DIR/modules"
fi

STAGE="${TARGET_DIR}.new.$$"
sudo rm -rf "$STAGE"
sudo mkdir -p "$STAGE"

for module in "${MODULES[@]}"; do
    sudo install -o root -g root -m 0644 "$module" "$STAGE/$(basename "$module")"
done

sudo rm -rf "$TARGET_DIR"
sudo mv "$STAGE" "$TARGET_DIR"
INSTALLED=1

log "Refreshing module dependency database..."
sudo depmod -a "$CURRENT_KERNEL"

RESOLVED="$(modinfo -n nvidia 2>/dev/null || true)"
case "$RESOLVED" in
    "$TARGET_DIR"/*) ;;
    *) die "depmod did not select the installed NVIDIA module. Resolved path: ${RESOLVED:-unknown}" ;;
esac

if command -v mkinitcpio >/dev/null 2>&1; then
    log "Rebuilding initramfs..."
    sudo mkinitcpio -P
fi

sudo mkdir -p "$STATE_ROOT"
sudo cp "$INFO" "${STATE_ROOT}/installed-build-info.txt"
printf '%s\n' "$ARCHIVE" | sudo tee "${STATE_ROOT}/installed-archive.txt" >/dev/null
printf '%s\n' "$CURRENT_KERNEL" | sudo tee "${STATE_ROOT}/installed-kernel.txt" >/dev/null
printf '%s\n' "$BUILD_NVIDIA" | sudo tee "${STATE_ROOT}/installed-nvidia.txt" >/dev/null

INSTALLED=0
restore_readonly
trap - ERR INT TERM

ok "NVIDIA open kernel modules installed successfully."
log "Reboot is required before the new modules will be used."
