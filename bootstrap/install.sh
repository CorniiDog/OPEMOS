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
need_cmd zstd

CURRENT_STEAMOS="$(get_steamos_version)"
CURRENT_KERNEL="$(get_kernel_version)"
CURRENT_NVIDIA="$(get_nvidia_version)"

if [[ -n "$CHECKSUM" ]]; then
    EXPECTED_SHA="$(awk '{print $1}' "$CHECKSUM" | head -n1)"
    [[ "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid archive checksum."
    ACTUAL_SHA="$(sha256_file "$ARCHIVE")"
    strings_equal_case_insensitive "$EXPECTED_SHA" "$ACTUAL_SHA" ||
        die "Archive checksum verification failed."
fi

mkdir -p "${HOME}/.cache/${PROJECT_ID}"
TMP="$(project_mktemp_dir install-extract)"
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

mapfile -t MODULES < <(
    find "$MODULE_DIR" -maxdepth 1 -type f \
        \( -name '*.ko' -o -name '*.ko.zst' \) -print |
        sort
)

validate_nvidia_module_set "${MODULES[@]}" ||
    die "Release module set does not match the five expected NVIDIA modules."

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
acquire_lifecycle_lock

TARGET_DIR="$(project_system_path "/usr/lib/modules/${CURRENT_KERNEL}/updates/open-gpu-kernel-modules-steamos")"
STATE_ROOT="$(project_system_path "/var/lib/open-gpu-kernel-modules-steamos-support")"
CACHE_ROOT="${HOME}/.cache/${PROJECT_ID}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="${CACHE_ROOT}/backups/${CURRENT_KERNEL}"
mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/${STAMP}.XXXXXX")"
RO_WAS_ENABLED=0
TARGET_TOUCHED=0
STATE_TOUCHED=0
INSTALL_COMPLETE=0
STAGE=""

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 || warn "Failed to re-enable SteamOS read-only mode."
        RO_WAS_ENABLED=0
    fi
}

cleanup()
{
    local rc=$?
    trap - EXIT INT TERM

    if [[ "$INSTALL_COMPLETE" != "1" && "$TARGET_TOUCHED" == "1" ]]; then
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

    if [[ "$INSTALL_COMPLETE" != "1" && "$STATE_TOUCHED" == "1" ]]; then
        warn "Restoring previous install state metadata."

        sudo rm -f \
            "${STATE_ROOT}/installed-build-info.txt" \
            "${STATE_ROOT}/installed-archive.txt" \
            "${STATE_ROOT}/installed-kernel.txt" \
            "${STATE_ROOT}/installed-nvidia.txt" || true

        if [[ -d "$BACKUP_DIR/state" ]]; then
            sudo cp -a "$BACKUP_DIR/state/." "$STATE_ROOT/" || true
        fi
    fi

    if [[ -n "$STAGE" ]]; then
        rm -rf "$STAGE" >/dev/null 2>&1 || true
    fi

    rm -rf "$TMP" || true
    restore_readonly
    exit "$rc"
}

interrupt()
{
    exit 130
}

terminate()
{
    exit 143
}

trap cleanup EXIT
trap interrupt INT
trap terminate TERM

if command -v steamos-readonly >/dev/null 2>&1 &&
   steamos-readonly status 2>/dev/null | grep -qi enabled; then
    log "Temporarily disabling SteamOS read-only mode..."
    RO_WAS_ENABLED=1
    sudo steamos-readonly disable
fi

if [[ -d "$TARGET_DIR" ]]; then
    sudo cp -a "$TARGET_DIR" "$BACKUP_DIR/modules"
    sudo chown -R "$USER":"$(id -gn)" "$BACKUP_DIR"
fi

mkdir -p "${HOME}/.cache/${PROJECT_ID}"
STAGE="$(project_mktemp_dir install-stage)"

for module in "${MODULES[@]}"; do
    module_name="$(basename "$module")"

    case "$module_name" in
        *.ko.zst)
            zstd -q -t -- "$module" ||
                die "Compressed module is invalid: $module_name"
            install -m 0644 "$module" "$STAGE/$module_name"
            ;;
        *.ko)
            zstd -q -f -T0 "$module" -o "$STAGE/${module_name}.zst"
            ;;
        *)
            die "Unsupported module file: $module_name"
            ;;
    esac
done

NEW_BYTES="$(du -s -B1 "$STAGE" | awk '{print $1}')"
AVAILABLE_BYTES="$(df -B1 --output=avail "$(dirname "$TARGET_DIR")" | tail -n1 | tr -d ' ')"
CURRENT_BYTES=0

if [[ -d "$TARGET_DIR" ]]; then
    CURRENT_BYTES="$(du -s -B1 "$TARGET_DIR" | awk '{print $1}')"
fi

SAFETY_BYTES=$((64 * 1024 * 1024))
EFFECTIVE_BYTES=$((AVAILABLE_BYTES + CURRENT_BYTES))
REQUIRED_BYTES=$((NEW_BYTES + SAFETY_BYTES))

printf '\n[%s] Root filesystem preflight\n' "$PROJECT_NAME"
printf '[%s]   New compressed modules: %d MiB\n' "$PROJECT_NAME" "$((NEW_BYTES / 1024 / 1024))"
printf '[%s]   Currently available:    %d MiB\n' "$PROJECT_NAME" "$((AVAILABLE_BYTES / 1024 / 1024))"
printf '[%s]   Reclaimable old target: %d MiB\n' "$PROJECT_NAME" "$((CURRENT_BYTES / 1024 / 1024))"
printf '[%s]   Effective after replace: %d MiB\n' "$PROJECT_NAME" "$((EFFECTIVE_BYTES / 1024 / 1024))"
printf '[%s]   Safety reserve:         %d MiB\n' "$PROJECT_NAME" "$((SAFETY_BYTES / 1024 / 1024))"
printf '[%s]   Required with reserve:  %d MiB\n\n' "$PROJECT_NAME" "$((REQUIRED_BYTES / 1024 / 1024))"

(( EFFECTIVE_BYTES >= REQUIRED_BYTES )) ||
    die "Insufficient SteamOS root space for safe module replacement."

TARGET_TOUCHED=1
sudo rm -rf "$TARGET_DIR"
sudo mkdir -p "$TARGET_DIR"
sudo cp -a "$STAGE/." "$TARGET_DIR/"

for staged_module in "$STAGE"/*.ko.zst; do
    installed_module="$TARGET_DIR/$(basename "$staged_module")"

    [[ -f "$installed_module" ]] ||
        die "Installed module is missing after copy: $(basename "$staged_module")"

    [[ "$(sha256_file "$staged_module")" == "$(sha256_file "$installed_module")" ]] ||
        die "Installed module checksum verification failed: $(basename "$staged_module")"
done

log "Refreshing module dependency database..."
sudo depmod -a "$CURRENT_KERNEL"

RESOLVED="$(modinfo -n nvidia 2>/dev/null || true)"
RESOLVED_REAL="$(canonicalize_path "$RESOLVED")"
TARGET_REAL="$(canonicalize_path "$TARGET_DIR")"

case "$RESOLVED_REAL" in
    "$TARGET_REAL"/*) ;;
    *) die "depmod did not select the installed NVIDIA module. Resolved path: ${RESOLVED:-unknown}" ;;
esac

if command -v mkinitcpio >/dev/null 2>&1; then
    log "Rebuilding initramfs..."
    sudo mkinitcpio -P
fi

sudo mkdir -p "$STATE_ROOT"
mkdir -p "$BACKUP_DIR/state"

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

STATE_TOUCHED=1
sudo cp "$INFO" "${STATE_ROOT}/installed-build-info.txt"
printf '%s\n' "$ARCHIVE" | sudo tee "${STATE_ROOT}/installed-archive.txt" >/dev/null
printf '%s\n' "$CURRENT_KERNEL" | sudo tee "${STATE_ROOT}/installed-kernel.txt" >/dev/null
printf '%s\n' "$BUILD_NVIDIA" | sudo tee "${STATE_ROOT}/installed-nvidia.txt" >/dev/null

INSTALL_COMPLETE=1
restore_readonly
rm -rf "$TMP"
trap - EXIT INT TERM
python3 "$SUPPORT_ROOT/lib/prune_backup_generations.py" \
    --root "$BACKUP_ROOT" --protect "$(basename "$BACKUP_DIR")" \
    --keep 10 --max-age-days 90 ||
    warn "Backup retention could not be applied; preserved all generations."

ok "NVIDIA open kernel modules installed successfully."
log "Reboot is required before the new modules will be used."
