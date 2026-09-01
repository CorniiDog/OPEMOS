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
GAMING_PAYLOAD_PROFILE=""
DEPENDENCY_PACKAGES=()
DEPENDENCY_SIGNATURES=()
RESULT_JSON=""
PROGRESS_ATTEMPT=""
COMPRESSION_PROFILE=""
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
  --compression-profile PROFILE  Currently: btrfs-zstd3.
  --gaming-payload-profile FILE  Reviewed exact-target CUDA-omission profile.
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
        --package-keyring|--userspace-lock|--gaming-payload-profile|--result-json|--progress-attempt|--compression-profile)
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
                --gaming-payload-profile) GAMING_PAYLOAD_PROFILE="$2" ;;
                --result-json) RESULT_JSON="$2" ;;
                --progress-attempt) PROGRESS_ATTEMPT="$2" ;;
                --compression-profile) COMPRESSION_PROFILE="$2" ;;
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
PROGRESS_ATTEMPT_VALUE=0
[[ -z "$PROGRESS_ATTEMPT" ]] || PROGRESS_ATTEMPT_VALUE=$((10#$PROGRESS_ATTEMPT))
if [[ -n "$COMPRESSION_PROFILE" && "$COMPRESSION_PROFILE" != btrfs-zstd3 ]]; then
    fail_argument "Compression profile is not supported."
fi

for value in ROOT ARCHIVE CHECKSUM PROVENANCE KERNEL NVIDIA_UTILS \
    NVIDIA_UTILS_SIGNATURE LIB32_NVIDIA_UTILS LIB32_NVIDIA_UTILS_SIGNATURE \
    PACKAGE_KEYRING USERSPACE_LOCK RESULT_JSON
do
    [[ -n "${!value}" ]] || fail_argument "Required offline-root argument is missing: $value"
done

# Keep runtime progress records as small and predictable as validator records.
# Phase names are internal constants; no command output or filesystem path is
# ever copied into this machine-readable channel.
emit_progress_indeterminate()
{
    local phase="$1"
    case "$phase" in
        pacman_policy|grub_update|depmod|initramfs|installation_state) ;;
        *) return 1 ;;
    esac
    printf 'STEAMOS_NVIDIA_PROGRESS {"attempt":%d,"indeterminate":true,"phase":"%s","schemaVersion":1}\n' \
        "$PROGRESS_ATTEMPT_VALUE" "$phase" >&2
}

emit_progress_items()
{
    local phase="$1" completed="$2" total="$3"
    case "$phase" in
        pacman_policy|runtime_mounts|userspace_install|userspace_verification|\
        module_install|module_verification|grub_update|depmod|initramfs|\
        installation_state|mount_cleanup) ;;
        *) return 1 ;;
    esac
    [[ "$completed" =~ ^[0-9]+$ && "$total" =~ ^[0-9]+$ ]] || return 1
    (( completed <= total && total <= 1000000 )) || return 1
    printf 'STEAMOS_NVIDIA_PROGRESS {"attempt":%d,"completed":%d,"indeterminate":false,"phase":"%s","schemaVersion":1,"total":%d,"unit":"items"}\n' \
        "$PROGRESS_ATTEMPT_VALUE" "$completed" "$phase" "$total" >&2
}

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
[[ -z "$COMPRESSION_PROFILE" ]] || VALIDATOR_ARGS+=(--compression-profile "$COMPRESSION_PROFILE")
[[ -z "$GAMING_PAYLOAD_PROFILE" ]] || VALIDATOR_ARGS+=(--gaming-payload-profile "$GAMING_PAYLOAD_PROFILE")
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
MODULE_PAYLOAD_NOOP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("compression", {}).get("modulePayloadNoop", False))' "$VALIDATION_JSON")"
VALIDATION_SHA256="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$VALIDATION_JSON")"

write_install_result()
{
    set -- python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status "$1" --reason "$2" --message "$3" \
        --phase "$4" --root /target-root --kernel "$KERNEL" \
        --steamos "$STEAMOS_VERSION" --nvidia "$NVIDIA_VERSION" --trust "$TRUST" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")" \
        --mounts-released "${5:-true}" \
        --compression-policy-restored "${6:-true}" \
        --validation "$VALIDATION_JSON"
    [[ -z "${MODULE_VERIFICATION_JSON:-}" || ! -s "$MODULE_VERIFICATION_JSON" ]] ||
        set -- "$@" --module-verification "$MODULE_VERIFICATION_JSON"
    "$@"
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
MODULE_VERIFICATION_JSON="$MUTATION_WORK/module-verification.json"
MOUNTS=()
PHASE=mutation_preflight
COMPLETED=0
ACTIVE_CHILD=""
CANCELLED=0
COMPRESSION_POLICY_ACTIVE=0
ORIGINAL_COMPRESSION_OPTION=""
PACMAN_TRANSACTION_CONFIG=""

require_validation_document_unchanged()
{
    local current
    current="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$VALIDATION_JSON")" || return 1
    [[ "$current" == "$VALIDATION_SHA256" ]]
}

require_measured_pacman_admission()
{
    [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]] || return 1
    python3 - "$VALIDATION_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    document = json.load(stream)
compression = document.get("compression", {})
storage = document.get("storage", {})
authorized = (
    document.get("schemaVersion") == 1
    and document.get("status") == "verified"
    and compression.get("requestedProfile") == "btrfs-zstd3"
    and compression.get("writePolicy") == "compress-force=zstd:3"
    and compression.get("admissionAuthorized") is True
    and compression.get("mutationProfileImplemented") is True
    and compression.get("pacmanCheckSpaceBypassAuthorized") is True
    and compression.get("pacmanCheckSpacePolicy")
        == "temporary-config-disable-after-live-revalidation"
    and compression.get("assessment") == "measured-profile-admission-ready"
    and all(
        isinstance(storage.get(f"{name}AvailableBytes"), int)
        and not isinstance(storage.get(f"{name}AvailableBytes"), bool)
        and isinstance(storage.get(f"{name}RequiredBytes"), int)
        and not isinstance(storage.get(f"{name}RequiredBytes"), bool)
        and storage[f"{name}AvailableBytes"] >= 0
        and storage[f"{name}RequiredBytes"] >= 0
        and storage[f"{name}RequiredBytes"] <= storage[f"{name}AvailableBytes"]
        for name in ("root", "var", "efi")
    )
)
raise SystemExit(0 if authorized else 1)
PY
}

compression_option()
{
    local output filesystem options option found=""
    output="$(findmnt -rn -T "$ROOT" -o FSTYPE,OPTIONS)" || return 1
    read -r filesystem options <<< "$output"
    [[ "$filesystem" == btrfs && -n "$options" ]] || return 1
    local old_ifs="$IFS"
    IFS=,
    for option in $options; do
        case "$option" in
            nodatacow|nodatasum)
                IFS="$old_ifs"
                return 1
                ;;
            compress=*|compress-force=*)
                [[ -z "$found" ]] || { IFS="$old_ifs"; return 1; }
                found="$option"
                ;;
        esac
    done
    IFS="$old_ifs"
    printf '%s\n' "$found"
}

require_exclusive_root_mount()
{
    local root_device mounted_devices mounted_device count=0
    root_device="$(findmnt -rn -T "$ROOT" -o MAJ:MIN)" || return 1
    [[ "$root_device" =~ ^[0-9]+:[0-9]+$ ]] || return 1
    mounted_devices="$(findmnt -rn -o MAJ:MIN)" || return 1
    while read -r mounted_device; do
        [[ "$mounted_device" != "$root_device" ]] || count=$((count + 1))
    done <<< "$mounted_devices"
    (( count == 1 ))
}

require_active_compression_policy()
{
    [[ "$(compression_option)" == "compress-force=zstd:3" ]] ||
        die "The measured Btrfs compression policy is no longer active."
}

require_runtime_bind_mount()
{
    local source="$1" target="$2" source_device target_device
    mountpoint -q "$target" || return 1
    source_device="$(findmnt -rn -T "$source" -o MAJ:MIN)" || return 1
    target_device="$(findmnt -rn -M "$target" -o MAJ:MIN)" || return 1
    [[ -n "$source_device" && "$source_device" == "$target_device" ]]
}

require_runtime_bind_mounts()
{
    local bind_path
    for bind_path in dev proc sys; do
        require_runtime_bind_mount "/$bind_path" "$ROOT/$bind_path" || return 1
    done
}

activate_compression_policy()
{
    [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]] || return 0
    require_exclusive_root_mount || return 1
    ORIGINAL_COMPRESSION_OPTION="$(compression_option)" || return 1
    COMPRESSION_POLICY_ACTIVE=1
    mount -o remount,compress-force=zstd:3 "$ROOT" || return 1
    require_active_compression_policy
}

restore_compression_policy()
{
    [[ "$COMPRESSION_POLICY_ACTIVE" == 1 ]] || return 0
    if [[ -n "$ORIGINAL_COMPRESSION_OPTION" &&
          "$ORIGINAL_COMPRESSION_OPTION" != compress=no ]]; then
        mount -o "remount,$ORIGINAL_COMPRESSION_OPTION" "$ROOT" || return 1
        [[ "$(compression_option)" == "$ORIGINAL_COMPRESSION_OPTION" ]] || return 1
    else
        mount -o remount,compress=no "$ROOT" || return 1
        case "$(compression_option)" in
            ""|compress=no) ;;
            *) return 1 ;;
        esac
    fi
    COMPRESSION_POLICY_ACTIVE=0
}

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
    local rc=$? index mounts_released=true compression_restored=true
    local released_mounts=0 total_mounts=${#MOUNTS[@]}
    trap - EXIT INT TERM
    emit_progress_items mount_cleanup 0 "$total_mounts"
    for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
        if unmount_tree "${MOUNTS[$index]}"; then
            released_mounts=$((released_mounts + 1))
            emit_progress_items mount_cleanup "$released_mounts" "$total_mounts"
        else
            mounts_released=false
        fi
    done
    restore_compression_policy || compression_restored=false
    if [[ "$COMPLETED" != 1 ]]; then
        if [[ "$mounts_released" == true && "$compression_restored" == true ]]; then
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
            write_install_result failed mutation_cleanup_failed \
                "Offline-root mutation failed and mount or compression policy cleanup was incomplete." \
                cleanup "$mounts_released" "$compression_restored" || true
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

if [[ -n "$COMPRESSION_PROFILE" ]]; then
    PHASE=compression_policy_activation
    activate_compression_policy ||
        die "The measured Btrfs compression policy could not be activated and verified."
fi

emit_progress_indeterminate pacman_policy
if [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]]; then
    PHASE=pacman_checkspace_policy
    require_validation_document_unchanged ||
        die "The validated compression admission document changed before mutation."
    require_measured_pacman_admission ||
        die "Pacman CheckSpace cannot be bypassed without exact measured Btrfs admission."
    require_active_compression_policy ||
        die "Pacman CheckSpace cannot be bypassed because the measured Btrfs policy is inactive."
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        PACMAN_CONFIG_SOURCE="${PROJECT_TEST_PACMAN_CONFIG:-}"
        [[ -n "$PACMAN_CONFIG_SOURCE" ]] ||
            die "The test appliance pacman configuration was not provided."
    else
        PACMAN_CONFIG_SOURCE=/etc/pacman.conf
    fi
    PACMAN_TRANSACTION_CONFIG="$MUTATION_WORK/pacman-measured-admission.conf"
    run_mutation_command python3 "$SUPPORT_ROOT/lib/prepare_pacman_config.py" \
        --source "$PACMAN_CONFIG_SOURCE" --output "$PACMAN_TRANSACTION_CONFIG" ||
        die "A confined pacman configuration could not be prepared for measured admission."
    require_validation_document_unchanged ||
        die "The validated compression admission document changed during transaction preparation."
    require_active_compression_policy ||
        die "The measured Btrfs policy changed during pacman transaction preparation."
fi
emit_progress_items pacman_policy 1 1

PHASE=runtime_mounts
runtime_mount_count=0
emit_progress_items runtime_mounts 0 3
for bind_path in dev proc sys; do
    install -d -m 0755 "$ROOT/$bind_path"
    MOUNTS+=("$ROOT/$bind_path")
    run_mutation_command mount --rbind "/$bind_path" "$ROOT/$bind_path"
    run_mutation_command mount --make-rslave "$ROOT/$bind_path"
    require_runtime_bind_mount "/$bind_path" "$ROOT/$bind_path" ||
        die "A target runtime bind mount could not be independently verified."
    runtime_mount_count=$((runtime_mount_count + 1))
    emit_progress_items runtime_mounts "$runtime_mount_count" 3
done
require_runtime_bind_mounts ||
    die "The complete target runtime mount set is unavailable."

PHASE=userspace_install
require_runtime_bind_mounts ||
    die "Target runtime mounts disappeared before the pacman transaction."
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
TARGET_PACMAN_DATABASE="$ROOT/usr/lib/holo/pacmandb"
PACMAN_PACKAGES=("$NVIDIA_UTILS" "$LIB32_NVIDIA_UTILS")
for (( dependency_index=0; dependency_index<${#DEPENDENCY_PACKAGES[@]}; dependency_index++ )); do
    PACMAN_PACKAGES+=("${DEPENDENCY_PACKAGES[$dependency_index]}")
done
package_count=${#PACMAN_PACKAGES[@]}
emit_progress_items userspace_install 0 "$package_count"
PACMAN_ARGS=(
    --root "$ROOT" --dbpath "$TARGET_PACMAN_DATABASE"
    --noconfirm --needed -U "${PACMAN_PACKAGES[@]}"
)
[[ -z "$PACMAN_TRANSACTION_CONFIG" ]] ||
    PACMAN_ARGS=(--config "$PACMAN_TRANSACTION_CONFIG" "${PACMAN_ARGS[@]}")
require_validation_document_unchanged ||
    die "The validated compression admission document changed before the pacman transaction."
run_mutation_command env SYSTEMD_OFFLINE=1 pacman "${PACMAN_ARGS[@]}"
emit_progress_items userspace_install "$package_count" "$package_count"
require_runtime_bind_mounts ||
    die "A pacman hook changed or released a target runtime mount."
require_validation_document_unchanged ||
    die "The validated compression admission document changed during the pacman transaction."
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
POST_INSTALL_VERIFY_ARGS=(
    --root "$ROOT"
    --validation "$VALIDATION_JSON"
)
for package in "${PACMAN_PACKAGES[@]}"; do
    POST_INSTALL_VERIFY_ARGS+=(--package "$package")
done
emit_progress_items userspace_verification 0 "$package_count"
run_mutation_command python3 "$SUPPORT_ROOT/lib/verify_installed_userspace.py" \
    "${POST_INSTALL_VERIFY_ARGS[@]}" --progress-attempt "$PROGRESS_ATTEMPT_VALUE"
find "$ROOT/usr/lib/firmware/nvidia/$NVIDIA_VERSION" \
    -type f -name 'gsp*.bin' -print -quit 2>/dev/null | grep -q . ||
    die "Matching GSP firmware was not installed into the target root."

PHASE=module_install
module_count=0
emit_progress_items module_install 0 5
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
bsdtar -xzf "$ARCHIVE" -C "$MUTATION_WORK"
TARGET_MODULES="$ROOT/usr/lib/modules/$KERNEL/updates/open-gpu-kernel-modules-steamos"
if [[ "$MODULE_PAYLOAD_NOOP" != True ]]; then
    rm -rf "$TARGET_MODULES"
    install -d -m 0755 "$TARGET_MODULES"
    install -d -m 0700 "$MUTATION_WORK/module-compression"
    for module in "$MUTATION_WORK"/modules/*.ko*; do
        module_name="$(basename "$module")"
        case "$module_name" in
            *.ko.zst)
                if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
                    install -m 0644 "$module" "$TARGET_MODULES/$module_name"
                else
                    install -o 0 -g 0 -m 0644 \
                        "$module" "$TARGET_MODULES/$module_name"
                fi
                ;;
            *.ko)
                compressed="$MUTATION_WORK/module-compression/${module_name}.zst"
                zstd -q -f -T0 "$module" -o "$compressed"
                if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
                    install -m 0644 \
                        "$compressed" "$TARGET_MODULES/${module_name}.zst"
                else
                    install -o 0 -g 0 -m 0644 \
                        "$compressed" "$TARGET_MODULES/${module_name}.zst"
                fi
                rm -f "$compressed"
                ;;
            *) die "Unexpected module filename after validated extraction." ;;
        esac
        module_count=$((module_count + 1))
        emit_progress_items module_install "$module_count" 5
    done
    (( module_count == 5 )) || die "The validated module payload count changed during installation."
else
    emit_progress_items module_install 5 5
fi
emit_progress_items module_verification 0 5
run_mutation_command python3 "$SUPPORT_ROOT/lib/verify_installed_modules.py" \
    --root "$ROOT" --kernel "$KERNEL" --validation "$VALIDATION_JSON" \
    --output "$MODULE_VERIFICATION_JSON" --progress-attempt "$PROGRESS_ATTEMPT_VALUE"

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
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy

PHASE=bootloader_config
emit_progress_indeterminate grub_update
run_mutation_command python3 "$SUPPORT_ROOT/lib/update_grub_nvidia_args.py" \
    --grub-config "$ROOT/efi/EFI/steamos/grub.cfg"
emit_progress_items grub_update 1 1

PHASE=depmod
emit_progress_indeterminate depmod
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
run_mutation_command depmod -b "$ROOT" -a "$KERNEL"
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
emit_progress_items depmod 1 1

PHASE=initramfs
emit_progress_indeterminate initramfs
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
require_runtime_bind_mounts ||
    die "Target runtime mounts disappeared before initramfs generation."
run_mutation_command env SYSTEMD_OFFLINE=1 chroot "$ROOT" /usr/bin/mkinitcpio -P
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
emit_progress_items initramfs 1 1

PHASE=state_write
emit_progress_indeterminate installation_state
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
STATE_ROOT="$ROOT/var/lib/$PROJECT_NAME/offline-install"
install -d -m 0755 "$STATE_ROOT"
install -m 0644 "$MUTATION_WORK/BUILD-INFO.txt" "$STATE_ROOT/BUILD-INFO.txt"
install -m 0644 "$PROVENANCE" "$STATE_ROOT/PROVENANCE.json"
printf '%s\n' "$KERNEL" > "$STATE_ROOT/kernel-version"
printf '%s\n' "$NVIDIA_VERSION" > "$STATE_ROOT/nvidia-version"
emit_progress_items installation_state 1 1

PHASE=cleanup
released_mounts=0
total_mounts=${#MOUNTS[@]}
emit_progress_items mount_cleanup 0 "$total_mounts"
for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
    unmount_tree "${MOUNTS[$index]}"
    released_mounts=$((released_mounts + 1))
    emit_progress_items mount_cleanup "$released_mounts" "$total_mounts"
done
MOUNTS=()
if findmnt -rn -R "$ROOT/dev" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/proc" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/sys" >/dev/null 2>&1; then
    die "One or more target bind mounts remain after recursive cleanup."
fi
if [[ -n "$COMPRESSION_PROFILE" ]]; then
    PHASE=compression_policy_restore
    restore_compression_policy ||
        die "The target Btrfs compression policy could not be restored."
fi

PHASE=complete
write_install_result success install_complete \
    "NVIDIA modules, authenticated userspace, GSP firmware, and initramfs were installed." \
    complete true
COMPLETED=1
trap - EXIT INT TERM
rm -rf "$MUTATION_WORK" "$VALIDATION_JSON"
ok "Offline-root NVIDIA installation completed with all mounts released."
