#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

ROOT=""
ARCHIVE=""
CHECKSUM=""
PROVENANCE=""
KERNEL=""
NVIDIA_UTILS=""
NVIDIA_UTILS_SIGNATURE=""
LIB32_NVIDIA_UTILS=""
LIB32_NVIDIA_UTILS_SIGNATURE=""
PACKAGE_KEYRING=""
RESULT_JSON=""
VALIDATE_ONLY=0

usage()
{
    cat <<EOF
Usage: install_to_root.sh [options]

Required:
  --root PATH
  --archive FILE
  --checksum FILE
  --provenance FILE
  --kernel VERSION
  --nvidia-utils FILE
  --nvidia-utils-signature FILE
  --lib32-nvidia-utils FILE
  --lib32-nvidia-utils-signature FILE
  --package-keyring FILE
  --result-json FILE

Options:
  --validate-only
  -h, --help

Runs only in the managed x86_64 appliance. It never examines the appliance
kernel or calls steamos-readonly. A failed mutation invalidates the caller's
disposable image overlay.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --root) ROOT="${2:-}"; shift 2 ;;
        --archive) ARCHIVE="${2:-}"; shift 2 ;;
        --checksum) CHECKSUM="${2:-}"; shift 2 ;;
        --provenance) PROVENANCE="${2:-}"; shift 2 ;;
        --kernel) KERNEL="${2:-}"; shift 2 ;;
        --nvidia-utils) NVIDIA_UTILS="${2:-}"; shift 2 ;;
        --nvidia-utils-signature) NVIDIA_UTILS_SIGNATURE="${2:-}"; shift 2 ;;
        --lib32-nvidia-utils) LIB32_NVIDIA_UTILS="${2:-}"; shift 2 ;;
        --lib32-nvidia-utils-signature) LIB32_NVIDIA_UTILS_SIGNATURE="${2:-}"; shift 2 ;;
        --package-keyring) PACKAGE_KEYRING="${2:-}"; shift 2 ;;
        --result-json) RESULT_JSON="${2:-}"; shift 2 ;;
        --validate-only) VALIDATE_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

for value in ROOT ARCHIVE CHECKSUM PROVENANCE KERNEL NVIDIA_UTILS \
    NVIDIA_UTILS_SIGNATURE LIB32_NVIDIA_UTILS LIB32_NVIDIA_UTILS_SIGNATURE \
    PACKAGE_KEYRING RESULT_JSON
do
    [[ -n "${!value}" ]] || die "Required offline-root argument is missing: $value"
done

VALIDATION_JSON="$(mktemp /tmp/offline-root-validation.XXXXXX)"
trap 'rm -f "$VALIDATION_JSON"' EXIT INT TERM

VALIDATOR_ARGS=(
    --root "$ROOT"
    --archive "$ARCHIVE"
    --checksum "$CHECKSUM"
    --provenance "$PROVENANCE"
    --kernel "$KERNEL"
    --nvidia-utils "$NVIDIA_UTILS"
    --nvidia-utils-signature "$NVIDIA_UTILS_SIGNATURE"
    --lib32-nvidia-utils "$LIB32_NVIDIA_UTILS"
    --lib32-nvidia-utils-signature "$LIB32_NVIDIA_UTILS_SIGNATURE"
    --package-keyring "$PACKAGE_KEYRING"
    --output "$VALIDATION_JSON"
)

if ! python3 "${SUPPORT_ROOT}/lib/validate_install_inputs.py" "${VALIDATOR_ARGS[@]}"; then
    python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status failed --reason input_validation_failed \
        --message "One or more offline-root inputs failed validation." \
        --phase validation --root /target-root --kernel "$KERNEL" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")"
    exit 1
fi

json_value()
{
    python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); print(value[sys.argv[2]][sys.argv[3]])' \
        "$VALIDATION_JSON" "$1" "$2"
}

STEAMOS_VERSION="$(json_value target steamosVersion)"
NVIDIA_VERSION="$(json_value target nvidiaVersion)"
TRUST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["trust"])' "$VALIDATION_JSON")"

write_install_result()
{
    python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status "$1" --reason "$2" --message "$3" \
        --phase "$4" --root /target-root --kernel "$KERNEL" \
        --steamos "$STEAMOS_VERSION" --nvidia "$NVIDIA_VERSION" --trust "$TRUST" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")" \
        --mounts-released "${5:-true}"
}

if (( VALIDATE_ONLY )); then
    python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status validated --reason validation_complete \
        --message "All offline-root inputs passed validation without mutation." \
        --phase validated --root /target-root --kernel "$KERNEL" \
        --steamos "$STEAMOS_VERSION" --nvidia "$NVIDIA_VERSION" --trust "$TRUST" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")"
    ok "Offline-root NVIDIA inputs validated without mutation."
    exit 0
fi

(( EUID == 0 || ${PROJECT_TEST_MODE:-0} == 1 )) ||
    die "Offline-root mutation must run as root in the managed appliance."
if [[ "${PROJECT_TEST_MODE:-0}" != 1 ]]; then
    [[ "$(uname -m)" == x86_64 ]] || die "Offline-root mutation requires x86_64."
    mountpoint -q "$ROOT" || die "Target root must be an explicit mountpoint."
    mountpoint -q "$ROOT/boot" ||
        die "The image builder must mount the target boot/EFI partition at /boot before mutation."
fi

for command_name in bsdtar chroot depmod install mount mountpoint pacman umount zstd; do
    need_cmd "$command_name"
done

MUTATION_WORK="$(mktemp -d /tmp/offline-root-mutation.XXXXXX)"
MOUNTS=()
PHASE=mutation_preflight
COMPLETED=0

cleanup_mutation()
{
    local rc=$? index mounts_released=true
    trap - EXIT INT TERM
    for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
        if mountpoint -q "${MOUNTS[$index]}"; then
            umount "${MOUNTS[$index]}" || mounts_released=false
        fi
    done
    rm -rf "$MUTATION_WORK" "$VALIDATION_JSON" >/dev/null 2>&1 || true
    if [[ "$COMPLETED" != 1 ]]; then
        if [[ "$mounts_released" == true ]]; then
            if [[ "$PHASE" == cancelled ]]; then
                write_install_result cancelled cancelled \
                    "Offline-root mutation was cancelled; discard the disposable overlay." \
                    cancelled true || true
            else
                write_install_result failed "$PHASE" \
                    "Offline-root mutation failed; discard the disposable overlay." \
                    "$PHASE" true || true
            fi
        else
            write_install_result failed mount_cleanup_failed \
                "Offline-root mutation failed and one or more mounts remain active." \
                cleanup false || true
        fi
    fi
    exit "$rc"
}

cancel_mutation()
{
    PHASE=cancelled
    exit 130
}

trap cleanup_mutation EXIT
trap cancel_mutation INT TERM

PHASE=userspace_install
SYSTEMD_OFFLINE=1 pacman --root "$ROOT" --dbpath "$ROOT/var/lib/pacman" \
    --noconfirm --needed -U "$NVIDIA_UTILS" "$LIB32_NVIDIA_UTILS"

installed_package_version()
{
    local package="$1" version
    version="$(pacman --root "$ROOT" --dbpath "$ROOT/var/lib/pacman" -Q "$package" | awk '{print $2}')"
    printf '%s\n' "${version%-*}"
}

[[ "$(installed_package_version nvidia-utils)" == "$NVIDIA_VERSION" ]] ||
    die "Installed nvidia-utils version does not match the artifact."
[[ "$(installed_package_version lib32-nvidia-utils)" == "$NVIDIA_VERSION" ]] ||
    die "Installed lib32-nvidia-utils version does not match the artifact."
find "$ROOT/usr/lib/firmware/nvidia" -type f -name 'gsp*.bin' -print -quit | grep -q . ||
    die "Matching GSP firmware was not installed into the target root."

PHASE=module_install
bsdtar -xzf "$ARCHIVE" -C "$MUTATION_WORK"
TARGET_MODULES="$ROOT/usr/lib/modules/$KERNEL/updates/open-gpu-kernel-modules-steamos"
rm -rf "$TARGET_MODULES"
install -d -m 0755 "$TARGET_MODULES"
for module in "$MUTATION_WORK"/modules/*.ko*; do
    module_name="$(basename "$module")"
    case "$module_name" in
        *.ko.zst) install -m 0644 "$module" "$TARGET_MODULES/$module_name" ;;
        *.ko) zstd -q -f -T0 "$module" -o "$TARGET_MODULES/${module_name}.zst" ;;
        *) die "Unexpected module filename after validated extraction." ;;
    esac
done

install -d -m 0755 "$ROOT/etc/modprobe.d" "$ROOT/etc/mkinitcpio.conf.d"
printf '%s\n' \
    "# Managed by ${PROJECT_NAME}" \
    'blacklist nouveau' \
    'options nouveau modeset=0' \
    'options nvidia-drm modeset=1 fbdev=1' \
    'options nvidia NVreg_PreserveVideoMemoryAllocations=1' \
    > "$ROOT/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
printf '%s\n' \
    "# Managed by ${PROJECT_NAME}" \
    'MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)' \
    > "$ROOT/etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf"

PHASE=depmod
depmod -b "$ROOT" -a "$KERNEL"

PHASE=initramfs
for bind_path in dev proc sys; do
    install -d -m 0755 "$ROOT/$bind_path"
    mount --rbind "/$bind_path" "$ROOT/$bind_path"
    mount --make-rslave "$ROOT/$bind_path"
    MOUNTS+=("$ROOT/$bind_path")
done
SYSTEMD_OFFLINE=1 chroot "$ROOT" /usr/bin/mkinitcpio -P

PHASE=state_write
STATE_ROOT="$ROOT/var/lib/$PROJECT_NAME/offline-install"
install -d -m 0755 "$STATE_ROOT"
install -m 0644 "$MUTATION_WORK/BUILD-INFO.txt" "$STATE_ROOT/BUILD-INFO.txt"
install -m 0644 "$PROVENANCE" "$STATE_ROOT/PROVENANCE.json"
printf '%s\n' "$KERNEL" > "$STATE_ROOT/kernel-version"
printf '%s\n' "$NVIDIA_VERSION" > "$STATE_ROOT/nvidia-version"

PHASE=cleanup
for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
    if mountpoint -q "${MOUNTS[$index]}"; then
        umount "${MOUNTS[$index]}"
    fi
done
MOUNTS=()

PHASE=complete
write_install_result success install_complete \
    "NVIDIA modules, authenticated userspace, GSP firmware, and initramfs were installed." \
    complete true
COMPLETED=1
trap - EXIT INT TERM
rm -rf "$MUTATION_WORK" "$VALIDATION_JSON"
ok "Offline-root NVIDIA installation completed with all mounts released."
