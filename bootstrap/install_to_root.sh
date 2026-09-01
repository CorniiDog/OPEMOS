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
USERSPACE_LOCK=""
DEPENDENCY_PACKAGES=()
DEPENDENCY_SIGNATURES=()
RESULT_JSON=""
PROGRESS_ATTEMPT=""
VALIDATE_ONLY=0
ORIGINAL_ARGS=("$@")

# Locate the result path before normal parsing so malformed CLI input can still
# return the same machine-readable contract consumed by the image builder.
for (( argument_index=0; argument_index<${#ORIGINAL_ARGS[@]}; argument_index++ )); do
    if [[ "${ORIGINAL_ARGS[$argument_index]}" == "--result-json" &&
          $((argument_index + 1)) -lt ${#ORIGINAL_ARGS[@]} &&
          "${ORIGINAL_ARGS[$((argument_index + 1))]}" != --* ]]; then
        RESULT_JSON="${ORIGINAL_ARGS[$((argument_index + 1))]}"
        break
    fi
done

fail_argument()
{
    local message="$1"
    if [[ -n "$RESULT_JSON" ]]; then
        python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
            --output "$RESULT_JSON" --status failed --reason invalid_arguments \
            --message "$message" --phase argument_validation --root /target-root \
            --kernel "${KERNEL:-unknown}" || true
    fi
    die "$message"
}

require_option_value()
{
    [[ $# -ge 2 && "$2" != --* ]] || fail_argument "$1 requires a value."
}

SEEN_SINGLE_OPTIONS="|"
mark_single_option()
{
    case "$SEEN_SINGLE_OPTIONS" in
        *"|$1|"*) fail_argument "Option may be specified only once: $1" ;;
    esac
    SEEN_SINGLE_OPTIONS="${SEEN_SINGLE_OPTIONS}$1|"
}

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
  --userspace-lock FILE
  --result-json FILE

Options:
  --dependency-package FILE       Repeat with its paired signature.
  --dependency-signature FILE     Repeat in the same order as packages.
  --progress-attempt NUMBER       Correlates progress records (0-1000000).
  --validate-only
  -h, --help

Runs only in the managed x86_64 appliance. It never examines the appliance
kernel or calls steamos-readonly. A failed mutation invalidates the caller's
disposable image overlay.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --root|--archive|--checksum|--provenance|--kernel|--nvidia-utils|\
        --nvidia-utils-signature|--lib32-nvidia-utils|--lib32-nvidia-utils-signature|\
        --package-keyring|--userspace-lock|--result-json|--progress-attempt)
            require_option_value "$@"
            mark_single_option "$1"
            case "$1" in
                --root) ROOT="$2" ;;
                --archive) ARCHIVE="$2" ;;
                --checksum) CHECKSUM="$2" ;;
                --provenance) PROVENANCE="$2" ;;
                --kernel) KERNEL="$2" ;;
                --nvidia-utils) NVIDIA_UTILS="$2" ;;
                --nvidia-utils-signature) NVIDIA_UTILS_SIGNATURE="$2" ;;
                --lib32-nvidia-utils) LIB32_NVIDIA_UTILS="$2" ;;
                --lib32-nvidia-utils-signature) LIB32_NVIDIA_UTILS_SIGNATURE="$2" ;;
                --package-keyring) PACKAGE_KEYRING="$2" ;;
                --userspace-lock) USERSPACE_LOCK="$2" ;;
                --result-json) RESULT_JSON="$2" ;;
                --progress-attempt) PROGRESS_ATTEMPT="$2" ;;
            esac
            shift 2
            ;;
        --dependency-package|--dependency-signature)
            require_option_value "$@"
            if [[ "$1" == "--dependency-package" ]]; then
                DEPENDENCY_PACKAGES+=("$2")
            else
                DEPENDENCY_SIGNATURES+=("$2")
            fi
            shift 2
            ;;
        --validate-only)
            mark_single_option "$1"
            VALIDATE_ONLY=1
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) fail_argument "An unknown offline-root installer argument was supplied." ;;
    esac
done
(( ${#DEPENDENCY_PACKAGES[@]} == ${#DEPENDENCY_SIGNATURES[@]} )) ||
    fail_argument "Each dependency package requires one positionally paired signature."
if [[ -n "$PROGRESS_ATTEMPT" ]]; then
    [[ "$PROGRESS_ATTEMPT" =~ ^[0-9]+$ ]] || fail_argument "Progress attempt must be an integer."
    (( ${#PROGRESS_ATTEMPT} <= 7 )) || fail_argument "Progress attempt exceeds 1000000."
    (( 10#$PROGRESS_ATTEMPT <= 1000000 )) || fail_argument "Progress attempt exceeds 1000000."
fi

for value in ROOT ARCHIVE CHECKSUM PROVENANCE KERNEL NVIDIA_UTILS \
    NVIDIA_UTILS_SIGNATURE LIB32_NVIDIA_UTILS LIB32_NVIDIA_UTILS_SIGNATURE \
    PACKAGE_KEYRING USERSPACE_LOCK RESULT_JSON
do
    [[ -n "${!value}" ]] || fail_argument "Required offline-root argument is missing: $value"
done

VALIDATION_JSON="$(mktemp /tmp/offline-root-validation.XXXXXX)"
VALIDATOR_PID=""

terminate_process_group()
{
    local process_group="$1" attempt
    [[ -n "$process_group" ]] || return 0
    kill -TERM -- "-$process_group" >/dev/null 2>&1 ||
        kill -TERM "$process_group" >/dev/null 2>&1 || true
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        if ! kill -0 -- "-$process_group" >/dev/null 2>&1 &&
           ! kill -0 "$process_group" >/dev/null 2>&1; then
            break
        fi
        sleep 0.1
    done
    kill -KILL -- "-$process_group" >/dev/null 2>&1 || true
    kill -KILL "$process_group" >/dev/null 2>&1 || true
    wait "$process_group" >/dev/null 2>&1 || true
}

write_prevalidation_result()
{
    set -- python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status "$1" --reason "$2" --message "$3" \
        --phase validation --root /target-root --kernel "$KERNEL" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")"
    [[ ! -s "$VALIDATION_JSON" ]] || set -- "$@" --validation "$VALIDATION_JSON"
    "$@"
}

cancel_validation()
{
    trap - INT TERM
    if [[ -n "$VALIDATOR_PID" ]]; then
        terminate_process_group "$VALIDATOR_PID"
    fi
    write_prevalidation_result cancelled cancelled \
        "Offline-root validation was cancelled."
    rm -f "$VALIDATION_JSON"
    exit 143
}

trap 'rm -f "$VALIDATION_JSON"' EXIT
trap cancel_validation INT TERM

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
    --userspace-lock "$USERSPACE_LOCK"
    --output "$VALIDATION_JSON"
)
[[ -z "$PROGRESS_ATTEMPT" ]] || VALIDATOR_ARGS+=(--progress-attempt "$PROGRESS_ATTEMPT")
for (( dependency_index=0; dependency_index<${#DEPENDENCY_PACKAGES[@]}; dependency_index++ )); do
    VALIDATOR_ARGS+=(
        --dependency-package "${DEPENDENCY_PACKAGES[$dependency_index]}"
        --dependency-signature "${DEPENDENCY_SIGNATURES[$dependency_index]}"
    )
done

python3 "${SUPPORT_ROOT}/lib/run_in_process_group.py" \
    python3 "${SUPPORT_ROOT}/lib/validate_install_inputs.py" \
    "${VALIDATOR_ARGS[@]}" &
VALIDATOR_PID=$!
set +e
wait "$VALIDATOR_PID"
VALIDATOR_RC=$?
set -e
VALIDATOR_PID=""
if (( VALIDATOR_RC != 0 )); then
    VALIDATION_REASON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reason", "validation_failed"))' "$VALIDATION_JSON" 2>/dev/null || printf validation_failed)"
    VALIDATION_MESSAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("message", "Offline-root input validation failed."))' "$VALIDATION_JSON" 2>/dev/null || printf 'Offline-root input validation failed.')"
    write_prevalidation_result failed "$VALIDATION_REASON" "$VALIDATION_MESSAGE"
    exit 1
fi
trap - INT TERM

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
        --mounts-released "${5:-true}" --validation "$VALIDATION_JSON"
}

fail_pre_mutation()
{
    local reason="$1" message="$2"
    write_install_result failed "$reason" "$message" mutation_preflight true || true
    die "$message"
}

if (( VALIDATE_ONLY )); then
    python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status validated --reason validation_complete \
        --message "All offline-root inputs passed validation without mutation." \
        --phase validated --root /target-root --kernel "$KERNEL" \
        --steamos "$STEAMOS_VERSION" --nvidia "$NVIDIA_VERSION" --trust "$TRUST" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")" \
        --validation "$VALIDATION_JSON"
    ok "Offline-root NVIDIA inputs validated without mutation."
    exit 0
fi

(( EUID == 0 || ${PROJECT_TEST_MODE:-0} == 1 )) ||
    fail_pre_mutation privilege_required \
        "Offline-root mutation must run as root in the managed appliance."
if [[ "${PROJECT_TEST_MODE:-0}" != 1 ]]; then
    [[ "$(uname -m)" == x86_64 ]] ||
        fail_pre_mutation unsupported_appliance_architecture \
            "Offline-root mutation requires an x86_64 appliance."
    mountpoint -q "$ROOT" ||
        fail_pre_mutation target_mount_invalid \
            "Target root must be an explicit mountpoint."
    ! mountpoint -q "$ROOT/boot" ||
        fail_pre_mutation target_boot_invalid \
            "Target rootfs /boot must remain visible and must not be covered by EFI."
    mountpoint -q "$ROOT/efi" ||
        fail_pre_mutation target_efi_invalid \
            "The image builder must mount target efi-A at /efi before mutation."
    ROOT_SOURCE="$(findmnt -rn -M "$ROOT" -o SOURCE)" ||
        fail_pre_mutation target_mount_invalid \
            "Target root mount identity could not be determined."
    EFI_SOURCE="$(findmnt -rn -M "$ROOT/efi" -o SOURCE)" ||
        fail_pre_mutation target_efi_invalid \
            "Target EFI mount identity could not be determined."
    EFI_FSTYPE="$(findmnt -rn -M "$ROOT/efi" -o FSTYPE)" ||
        fail_pre_mutation target_efi_invalid \
            "Target EFI filesystem type could not be determined."
    [[ -n "$ROOT_SOURCE" && -n "$EFI_SOURCE" && "$ROOT_SOURCE" != "$EFI_SOURCE" ]] ||
        fail_pre_mutation target_efi_invalid \
            "Target /efi must be a distinct filesystem from the rootfs."
    case "$EFI_FSTYPE" in
        vfat|fat|msdos) ;;
        *) fail_pre_mutation target_efi_invalid \
            "Target /efi must be a FAT filesystem." ;;
    esac
fi

for command_name in bsdtar chroot depmod findmnt install mount mountpoint pacman umount vercmp zstd; do
    command -v "$command_name" >/dev/null 2>&1 ||
        fail_pre_mutation appliance_dependency_missing \
            "The managed appliance lacks a required installer command: $command_name"
done

MUTATION_WORK="$(mktemp -d /tmp/offline-root-mutation.XXXXXX)"
MOUNTS=()
PHASE=mutation_preflight
COMPLETED=0
ACTIVE_CHILD=""
CANCELLED=0

run_mutation_command()
{
    python3 "${SUPPORT_ROOT}/lib/run_in_process_group.py" "$@" &
    ACTIVE_CHILD=$!
    set +e
    wait "$ACTIVE_CHILD"
    local rc=$?
    set -e
    ACTIVE_CHILD=""
    return "$rc"
}

unmount_tree()
{
    local target="$1"
    if findmnt -rn -R "$target" >/dev/null 2>&1; then
        umount -R "$target" || return 1
    fi
    ! findmnt -rn -R "$target" >/dev/null 2>&1
}

cleanup_mutation()
{
    local rc=$? index mounts_released=true
    trap - EXIT INT TERM
    for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
        unmount_tree "${MOUNTS[$index]}" || mounts_released=false
    done
    if [[ "$COMPLETED" != 1 ]]; then
        if [[ "$mounts_released" == true ]]; then
            if [[ "$CANCELLED" == 1 ]]; then
                write_install_result cancelled cancelled \
                    "Offline-root mutation was cancelled; discard the disposable overlay." \
                    "$PHASE" true || true
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
    rm -rf "$MUTATION_WORK" "$VALIDATION_JSON" >/dev/null 2>&1 || true
    exit "$rc"
}

cancel_mutation()
{
    CANCELLED=1
    if [[ -n "$ACTIVE_CHILD" ]]; then
        terminate_process_group "$ACTIVE_CHILD"
        ACTIVE_CHILD=""
    fi
    exit 130
}

trap cleanup_mutation EXIT
trap cancel_mutation INT TERM

PHASE=userspace_install
TARGET_PACMAN_DATABASE="$ROOT/usr/lib/holo/pacmandb"
PACMAN_PACKAGES=("$NVIDIA_UTILS" "$LIB32_NVIDIA_UTILS")
for (( dependency_index=0; dependency_index<${#DEPENDENCY_PACKAGES[@]}; dependency_index++ )); do
    PACMAN_PACKAGES+=("${DEPENDENCY_PACKAGES[$dependency_index]}")
done
run_mutation_command env SYSTEMD_OFFLINE=1 pacman \
    --root "$ROOT" --dbpath "$TARGET_PACMAN_DATABASE" \
    --noconfirm --needed -U "${PACMAN_PACKAGES[@]}"

installed_package_version()
{
    local package="$1" version
    version="$(pacman --root "$ROOT" --dbpath "$TARGET_PACMAN_DATABASE" -Q "$package" | awk '{print $2}')"
    printf '%s\n' "${version%-*}"
}

[[ "$(installed_package_version nvidia-utils)" == "$NVIDIA_VERSION" ]] ||
    die "Installed nvidia-utils version does not match the artifact."
[[ "$(installed_package_version lib32-nvidia-utils)" == "$NVIDIA_VERSION" ]] ||
    die "Installed lib32-nvidia-utils version does not match the artifact."
find "$ROOT/usr/lib/firmware/nvidia/$NVIDIA_VERSION" \
    -type f -name 'gsp*.bin' -print -quit 2>/dev/null | grep -q . ||
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

PHASE=bootloader_config
run_mutation_command python3 "$SUPPORT_ROOT/lib/update_grub_nvidia_args.py" \
    --grub-config "$ROOT/efi/EFI/steamos/grub.cfg"

PHASE=depmod
run_mutation_command depmod -b "$ROOT" -a "$KERNEL"

PHASE=initramfs
for bind_path in dev proc sys; do
    install -d -m 0755 "$ROOT/$bind_path"
    MOUNTS+=("$ROOT/$bind_path")
    run_mutation_command mount --rbind "/$bind_path" "$ROOT/$bind_path"
    run_mutation_command mount --make-rslave "$ROOT/$bind_path"
done
run_mutation_command env SYSTEMD_OFFLINE=1 chroot "$ROOT" /usr/bin/mkinitcpio -P

PHASE=state_write
STATE_ROOT="$ROOT/var/lib/$PROJECT_NAME/offline-install"
install -d -m 0755 "$STATE_ROOT"
install -m 0644 "$MUTATION_WORK/BUILD-INFO.txt" "$STATE_ROOT/BUILD-INFO.txt"
install -m 0644 "$PROVENANCE" "$STATE_ROOT/PROVENANCE.json"
printf '%s\n' "$KERNEL" > "$STATE_ROOT/kernel-version"
printf '%s\n' "$NVIDIA_VERSION" > "$STATE_ROOT/nvidia-version"

PHASE=cleanup
for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
    unmount_tree "${MOUNTS[$index]}"
done
MOUNTS=()
if findmnt -rn -R "$ROOT/dev" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/proc" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/sys" >/dev/null 2>&1; then
    die "One or more target bind mounts remain after recursive cleanup."
fi

PHASE=complete
write_install_result success install_complete \
    "NVIDIA modules, authenticated userspace, GSP firmware, and initramfs were installed." \
    complete true
COMPLETED=1
trap - EXIT INT TERM
rm -rf "$MUTATION_WORK" "$VALIDATION_JSON"
ok "Offline-root NVIDIA installation completed with all mounts released."
