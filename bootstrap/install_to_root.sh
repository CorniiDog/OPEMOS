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
GAMING_PAYLOAD_OUTPUT_DIR=""
DEPENDENCY_PACKAGES=()
DEPENDENCY_SIGNATURES=()
RESULT_JSON=""
PROGRESS_ATTEMPT=""
COMPRESSION_PROFILE=""
VALIDATE_ONLY=0
INPUT_SOURCE=direct
AUTHENTICATED_BUNDLE=""
BUNDLE_STORE=""
BUNDLE_KEYRING=""
BUNDLE_REVIEWED_SIGNERS=""
BUNDLE_STEAMOS=""
BUNDLE_NVIDIA=""
BUNDLE_CACHE_ID=""
BUNDLE_GENERATION=""
BUNDLE_LEASE=""
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
  --input-source MODE            direct or authenticated-bundle (no fallback).
  --authenticated-bundle DIR    Verified offline bundle for bundle mode.
  --bundle-store DIR             Immutable imported-generation store.
  --bundle-keyring FILE          Exact reviewed package keyring.
  --bundle-reviewed-signers FILE Exact reviewed signer policy.
  --bundle-steamos VERSION       Exact target SteamOS identity.
  --bundle-nvidia VERSION        Exact target NVIDIA identity.
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
        --input-source|--authenticated-bundle|--bundle-store|--bundle-keyring|\
        --bundle-reviewed-signers|--bundle-steamos|--bundle-nvidia)
            require_option_value "$@"
            mark_single_option "$1"
            case "$1" in
                --input-source) INPUT_SOURCE="$2" ;;
                --authenticated-bundle) AUTHENTICATED_BUNDLE="$2" ;;
                --bundle-store) BUNDLE_STORE="$2" ;;
                --bundle-keyring) BUNDLE_KEYRING="$2" ;;
                --bundle-reviewed-signers) BUNDLE_REVIEWED_SIGNERS="$2" ;;
                --bundle-steamos) BUNDLE_STEAMOS="$2" ;;
                --bundle-nvidia) BUNDLE_NVIDIA="$2" ;;
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
[[ "$INPUT_SOURCE" == direct || "$INPUT_SOURCE" == authenticated-bundle ]] ||
    fail_argument "Input source must be direct or authenticated-bundle."
if [[ "$INPUT_SOURCE" == authenticated-bundle ]]; then
    [[ -n "$AUTHENTICATED_BUNDLE" && -n "$BUNDLE_STORE" && -n "$BUNDLE_KEYRING" &&
       -n "$BUNDLE_REVIEWED_SIGNERS" && "$BUNDLE_STEAMOS" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ &&
       "$BUNDLE_NVIDIA" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] ||
        fail_argument "Authenticated-bundle mode requires its bundle, store, trust, and exact target options."
    [[ -z "$PROVENANCE$NVIDIA_UTILS$NVIDIA_UTILS_SIGNATURE$LIB32_NVIDIA_UTILS$LIB32_NVIDIA_UTILS_SIGNATURE$PACKAGE_KEYRING$USERSPACE_LOCK" &&
       ${#DEPENDENCY_PACKAGES[@]} == 0 && ${#DEPENDENCY_SIGNATURES[@]} == 0 ]] ||
        fail_argument "Authenticated-bundle mode cannot be mixed with direct userspace or provenance inputs."
    BUNDLE_IMPORT_RESULT="$(mktemp /tmp/offline-bundle-import.XXXXXX)"
    BUNDLE_RESOLUTION="$(mktemp /tmp/offline-bundle-resolution.XXXXXX)"
    rm -f "$BUNDLE_RESOLUTION"
    trap 'rm -f "$BUNDLE_IMPORT_RESULT" "$BUNDLE_RESOLUTION"' EXIT
    python3 "$SUPPORT_ROOT/lib/authenticated_cache_bundle.py" import-set \
        --bundle "$AUTHENTICATED_BUNDLE" --store "$BUNDLE_STORE" \
        --keyring "$BUNDLE_KEYRING" --reviewed-signers "$BUNDLE_REVIEWED_SIGNERS" \
        --lease-token "installer-$$-$RANDOM" \
        > "$BUNDLE_IMPORT_RESULT" || fail_argument "Authenticated bundle import failed closed."
    BUNDLE_CACHE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cacheId"])' "$BUNDLE_IMPORT_RESULT")" ||
        fail_argument "Authenticated bundle import result is invalid."
    BUNDLE_GENERATION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"])' "$BUNDLE_IMPORT_RESULT")" ||
        fail_argument "Authenticated bundle generation result is invalid."
    BUNDLE_LEASE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["lease"])' "$BUNDLE_IMPORT_RESULT")" ||
        fail_argument "Authenticated bundle lease result is invalid."
    python3 "$SUPPORT_ROOT/lib/resolve_authenticated_install_bundle.py" \
        --generation "$BUNDLE_GENERATION" --cache-id "$BUNDLE_CACHE_ID" \
        --steamos "$BUNDLE_STEAMOS" --nvidia "$BUNDLE_NVIDIA" \
        --keyring "$BUNDLE_KEYRING" --output "$BUNDLE_RESOLUTION" ||
        fail_argument "Authenticated bundle does not satisfy the exact install policy."
    PROVENANCE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["provenance"])' "$BUNDLE_RESOLUTION")"
    USERSPACE_LOCK="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["policy"])' "$BUNDLE_RESOLUTION")"
    PACKAGE_KEYRING="$BUNDLE_KEYRING"
    NVIDIA_UTILS="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["package"] for x in d["packages"] if x["name"]=="nvidia-utils"))' "$BUNDLE_RESOLUTION")"
    NVIDIA_UTILS_SIGNATURE="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["signature"] for x in d["packages"] if x["name"]=="nvidia-utils"))' "$BUNDLE_RESOLUTION")"
    LIB32_NVIDIA_UTILS="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["package"] for x in d["packages"] if x["name"]=="lib32-nvidia-utils"))' "$BUNDLE_RESOLUTION")"
    LIB32_NVIDIA_UTILS_SIGNATURE="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["signature"] for x in d["packages"] if x["name"]=="lib32-nvidia-utils"))' "$BUNDLE_RESOLUTION")"
    while IFS= read -r dependency_path; do DEPENDENCY_PACKAGES+=("$dependency_path"); done < <(
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(x["package"]) for x in d["packages"] if x["name"] not in ("nvidia-utils","lib32-nvidia-utils")]' "$BUNDLE_RESOLUTION")
    while IFS= read -r dependency_path; do DEPENDENCY_SIGNATURES+=("$dependency_path"); done < <(
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(x["signature"]) for x in d["packages"] if x["name"] not in ("nvidia-utils","lib32-nvidia-utils")]' "$BUNDLE_RESOLUTION")
elif [[ -n "$AUTHENTICATED_BUNDLE$BUNDLE_STORE$BUNDLE_KEYRING$BUNDLE_REVIEWED_SIGNERS$BUNDLE_STEAMOS$BUNDLE_NVIDIA" ]]; then
    fail_argument "Bundle options require --input-source authenticated-bundle."
fi
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

INSTALLER_TEMP_ROOT=/tmp
if [[ "${PROJECT_TEST_MODE:-0}" == 1 && -n "${PROJECT_TEST_TEMP_ROOT:-}" ]]; then
    [[ "$PROJECT_TEST_TEMP_ROOT" == /* && -d "$PROJECT_TEST_TEMP_ROOT" &&
       ! -L "$PROJECT_TEST_TEMP_ROOT" ]] ||
        fail_argument "The test-only installer temporary root is invalid."
    INSTALLER_TEMP_ROOT="$PROJECT_TEST_TEMP_ROOT"
fi
VALIDATION_JSON="$(mktemp "$INSTALLER_TEMP_ROOT/offline-root-validation.XXXXXX")"
INPUT_SNAPSHOT_ROOT="$(mktemp -d "$INSTALLER_TEMP_ROOT/offline-root-inputs.XXXXXX")"
chmod 0700 "$INPUT_SNAPSHOT_ROOT"
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

acquire_target_lifecycle_lock()
{
    [[ -d "$ROOT" ]] || return 0

    # Lock the target root directory inode itself. This avoids a shared global
    # lock and does not create state inside the image being validated. Keep the
    # descriptor open for the installer lifetime so validation and mutation
    # are one exclusive operation.
    exec 8<"$ROOT" || return 2
    flock -n 8 || return 3
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

cleanup_prevalidation_files()
{
    rm -f "$VALIDATION_JSON"
    [[ -z "${INITRAMFS_WORKSPACE_JSON:-}" ]] || rm -f "$INITRAMFS_WORKSPACE_JSON"
    rm -rf "$INPUT_SNAPSHOT_ROOT"
    [[ -z "${BUNDLE_IMPORT_RESULT:-}" ]] || rm -f "$BUNDLE_IMPORT_RESULT"
    [[ -z "${BUNDLE_RESOLUTION:-}" ]] || rm -f "$BUNDLE_RESOLUTION"
    [[ -z "${BUNDLE_LEASE:-}" ]] || rm -f "$BUNDLE_LEASE"
}

trap cleanup_prevalidation_files EXIT
trap cancel_validation INT TERM

if ! command -v flock >/dev/null 2>&1; then
    write_prevalidation_result failed appliance_dependency_missing \
        "The managed appliance lacks the target lifecycle locking command."
    exit 1
fi
set +e
acquire_target_lifecycle_lock
TARGET_LOCK_RC=$?
set -e
case "$TARGET_LOCK_RC" in
    0) ;;
    2)
        write_prevalidation_result failed target_lifecycle_lock_unavailable \
            "The target lifecycle lock could not be established."
        exit 1
        ;;
    *)
        write_prevalidation_result failed target_lifecycle_locked \
            "Another offline-root installer operation already owns this target."
        exit 1
        ;;
esac

if [[ "${PROJECT_TEST_MODE:-0}" != 1 ]]; then
    command -v findmnt >/dev/null 2>&1 && command -v mountpoint >/dev/null 2>&1 || {
        write_prevalidation_result failed appliance_dependency_missing \
            "The managed appliance lacks target mount identity commands."
        exit 1
    }
    mountpoint -q "$ROOT" && mountpoint -q "$ROOT/efi" || {
        write_prevalidation_result failed target_mount_invalid \
            "Target rootfs and EFI must be explicit mounts before validation."
        exit 1
    }
    PREVALIDATION_ROOT_MOUNT_IDENTITY="$(findmnt -rn -M "$ROOT" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS)" || {
        write_prevalidation_result failed target_mount_invalid \
            "Target root mount identity could not be recorded before validation."
        exit 1
    }
    PREVALIDATION_EFI_MOUNT_IDENTITY="$(findmnt -rn -M "$ROOT/efi" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS,FS-OPTIONS)" || {
        write_prevalidation_result failed target_efi_invalid \
            "Target EFI mount identity could not be recorded before validation."
        exit 1
    }
else
    PREVALIDATION_ROOT_MOUNT_IDENTITY=project-test-root
    PREVALIDATION_EFI_MOUNT_IDENTITY=project-test-efi
fi

snapshot_input()
{
    local variable_name="$1" label="$2" maximum="$3" source="${!1}" destination
    destination="$INPUT_SNAPSHOT_ROOT/$label/$(basename "$source")"
    mkdir -m 0700 "$INPUT_SNAPSHOT_ROOT/$label"
    python3 "$SUPPORT_ROOT/lib/snapshot_install_input.py" \
        --source "$source" --destination "$destination" \
        --max-bytes "$maximum" || return 1
    printf -v "$variable_name" '%s' "$destination"
}

for input_specification in \
    "ARCHIVE:archive:1073741824" "CHECKSUM:checksum:4096" \
    "PROVENANCE:provenance:1048576" \
    "NVIDIA_UTILS:nvidia-utils:2147483648" \
    "NVIDIA_UTILS_SIGNATURE:nvidia-utils-signature:1048576" \
    "LIB32_NVIDIA_UTILS:lib32-nvidia-utils:2147483648" \
    "LIB32_NVIDIA_UTILS_SIGNATURE:lib32-nvidia-utils-signature:1048576" \
    "PACKAGE_KEYRING:package-keyring:67108864" \
    "USERSPACE_LOCK:userspace-lock:1048576"
do
    input_variable="${input_specification%%:*}"
    input_remainder="${input_specification#*:}"
    input_label="${input_remainder%%:*}"
    input_maximum="${input_remainder#*:}"
    snapshot_input "$input_variable" "$input_label" "$input_maximum" || {
        write_prevalidation_result failed input_snapshot_failed \
            "An authenticated installer input could not be snapshotted safely."
        exit 1
    }
done
if [[ -n "$GAMING_PAYLOAD_PROFILE" ]]; then
    snapshot_input GAMING_PAYLOAD_PROFILE gaming-payload-profile 1048576 || {
        write_prevalidation_result failed input_snapshot_failed \
            "The gaming payload profile could not be snapshotted safely."
        exit 1
    }
    GAMING_PAYLOAD_OUTPUT_DIR="$INPUT_SNAPSHOT_ROOT/gaming-payload-packages"
    install -d -m 0700 "$GAMING_PAYLOAD_OUTPUT_DIR"
fi
for (( dependency_index=0; dependency_index<${#DEPENDENCY_PACKAGES[@]}; dependency_index++ )); do
    dependency_package="${DEPENDENCY_PACKAGES[$dependency_index]}"
    dependency_signature="${DEPENDENCY_SIGNATURES[$dependency_index]}"
    dependency_label="dependency-$dependency_index"
    dependency_signature_label="dependency-signature-$dependency_index"
    snapshot_input dependency_package "$dependency_label" 2147483648 || {
        write_prevalidation_result failed input_snapshot_failed \
            "A dependency package could not be snapshotted safely."
        exit 1
    }
    snapshot_input dependency_signature "$dependency_signature_label" 1048576 || {
        write_prevalidation_result failed input_snapshot_failed \
            "A dependency signature could not be snapshotted safely."
        exit 1
    }
    DEPENDENCY_PACKAGES[$dependency_index]="$dependency_package"
    DEPENDENCY_SIGNATURES[$dependency_index]="$dependency_signature"
done

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
    --input-source "$INPUT_SOURCE"
)
[[ -z "$BUNDLE_CACHE_ID" ]] || VALIDATOR_ARGS+=(--input-bundle-id "$BUNDLE_CACHE_ID")
[[ -z "$PROGRESS_ATTEMPT" ]] || VALIDATOR_ARGS+=(--progress-attempt "$PROGRESS_ATTEMPT")
[[ -z "$COMPRESSION_PROFILE" ]] || VALIDATOR_ARGS+=(--compression-profile "$COMPRESSION_PROFILE")
[[ -z "$GAMING_PAYLOAD_PROFILE" ]] || VALIDATOR_ARGS+=(
    --gaming-payload-profile "$GAMING_PAYLOAD_PROFILE"
    --gaming-payload-output-dir "$GAMING_PAYLOAD_OUTPUT_DIR"
)
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
INITRAMFS_WORKSPACE_JSON="$(mktemp "$INSTALLER_TEMP_ROOT/offline-root-workspace.XXXXXX")"
if [[ -n "$GAMING_PAYLOAD_PROFILE" ]]; then
    NVIDIA_UTILS_NAME="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["filename"] for x in d["gamingPayload"]["packageRecords"] if x["name"]=="nvidia-utils"))' "$VALIDATION_JSON")" || {
        write_prevalidation_result failed gaming_payload_repack_invalid \
            "The validated gaming payload lacks its nvidia-utils package."
        exit 1
    }
    LIB32_NVIDIA_UTILS_NAME="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next(x["filename"] for x in d["gamingPayload"]["packageRecords"] if x["name"]=="lib32-nvidia-utils"))' "$VALIDATION_JSON")" || {
        write_prevalidation_result failed gaming_payload_repack_invalid \
            "The validated gaming payload lacks its lib32-nvidia-utils package."
        exit 1
    }
    NVIDIA_UTILS="$GAMING_PAYLOAD_OUTPUT_DIR/$NVIDIA_UTILS_NAME"
    LIB32_NVIDIA_UTILS="$GAMING_PAYLOAD_OUTPUT_DIR/$LIB32_NVIDIA_UTILS_NAME"
    [[ -f "$NVIDIA_UTILS" && ! -L "$NVIDIA_UTILS" &&
       -f "$LIB32_NVIDIA_UTILS" && ! -L "$LIB32_NVIDIA_UTILS" ]] || {
        write_prevalidation_result failed gaming_payload_repack_invalid \
            "The validated gaming payload package staging is incomplete."
        exit 1
    }
fi

write_install_result()
{
    local result_status="$1" result_reason="$2"
    set -- python3 "${SUPPORT_ROOT}/lib/write_install_result.py" \
        --output "$RESULT_JSON" --status "$1" --reason "$2" --message "$3" \
        --phase "$4" --root /target-root --kernel "$KERNEL" \
        --steamos "$STEAMOS_VERSION" --nvidia "$NVIDIA_VERSION" --trust "$TRUST" \
        --archive "$(basename "$ARCHIVE")" --provenance "$(basename "$PROVENANCE")" \
        --nvidia-utils "$(basename "$NVIDIA_UTILS")" \
        --lib32-nvidia-utils "$(basename "$LIB32_NVIDIA_UTILS")" \
        --mounts-released "${5:-true}" \
        --compression-policy-restored "${6:-true}" \
        --runtime-mounts-expected "${RUNTIME_MOUNTS_EXPECTED:-0}" \
        --runtime-mounts-released "${RUNTIME_MOUNTS_RELEASED:-0}" \
        --validation "$VALIDATION_JSON"
    [[ -z "${MODULE_VERIFICATION_JSON:-}" || ! -s "$MODULE_VERIFICATION_JSON" ]] ||
        set -- "$@" --module-verification "$MODULE_VERIFICATION_JSON"
    [[ -z "${USERSPACE_VERIFICATION_JSON:-}" || ! -s "$USERSPACE_VERIFICATION_JSON" ]] ||
        set -- "$@" --userspace-verification "$USERSPACE_VERIFICATION_JSON"
    [[ -z "${INITRAMFS_WORKSPACE_JSON:-}" || ! -s "$INITRAMFS_WORKSPACE_JSON" ]] ||
        set -- "$@" --initramfs-workspace "$INITRAMFS_WORKSPACE_JSON"
    [[ -z "${INITRAMFS_VERIFICATION_JSON:-}" || ! -s "$INITRAMFS_VERIFICATION_JSON" ]] ||
        set -- "$@" --initramfs-verification "$INITRAMFS_VERIFICATION_JSON"
    [[ -z "${PAYLOAD_RECEIPT_JSON:-}" || ! -s "$PAYLOAD_RECEIPT_JSON" ]] ||
        set -- "$@" --payload-receipt "$PAYLOAD_RECEIPT_JSON"
    if [[ "$result_status" == failed && "$result_reason" == target_execution_trust &&
          -n "${TARGET_EXECUTION_FAILURE_JSON:-}" &&
          -s "$TARGET_EXECUTION_FAILURE_JSON" ]]; then
        set -- "$@" --target-execution-failure "$TARGET_EXECUTION_FAILURE_JSON"
    fi
    "$@"
}

fail_pre_mutation()
{
    local reason="$1" message="$2"
    write_install_result failed "$reason" "$message" mutation_preflight true || true
    die "$message"
}

require_prevalidation_mount_identities()
{
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        [[ "${MOCK_PREVALIDATION_MOUNT_IDENTITY_DRIFT:-0}" == 0 ]]
        return
    fi
    local current_root current_efi
    current_root="$(findmnt -rn -M "$ROOT" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS)" || return 1
    current_efi="$(findmnt -rn -M "$ROOT/efi" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS,FS-OPTIONS)" || return 1
    [[ "$current_root" == "$PREVALIDATION_ROOT_MOUNT_IDENTITY" &&
       "$current_efi" == "$PREVALIDATION_EFI_MOUNT_IDENTITY" ]]
}

require_prevalidation_mount_identities ||
    fail_pre_mutation target_mount_identity \
        "The target rootfs or EFI mount identity changed during validation."

set +e
python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
    --root "$ROOT" --target-only --required-bytes 4096 --required-inodes 1 \
    --output "$INITRAMFS_WORKSPACE_JSON"
TARGET_WORKSPACE_RC=$?
set -e
if (( TARGET_WORKSPACE_RC != 0 )); then
    write_install_result failed initramfs_workspace_unavailable \
        "The mounted target /var/tmp is unsafe or unavailable." validation true
    exit 1
fi

if (( VALIDATE_ONLY )); then
    write_install_result validated validation_complete \
        "All offline-root inputs passed validation without mutation." validated true
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
    ROOT_MOUNT_IDENTITY="$PREVALIDATION_ROOT_MOUNT_IDENTITY"
    EFI_MOUNT_IDENTITY="$PREVALIDATION_EFI_MOUNT_IDENTITY"
else
    ROOT_MOUNT_IDENTITY=project-test-root
    EFI_MOUNT_IDENTITY=project-test-efi
fi

for command_name in bsdtar chroot depmod findmnt install mount mountpoint pacman umount vercmp zstd; do
    command -v "$command_name" >/dev/null 2>&1 ||
        fail_pre_mutation appliance_dependency_missing \
            "The managed appliance lacks a required installer command: $command_name"
done

MUTATION_WORK="$(mktemp -d "$INSTALLER_TEMP_ROOT/offline-root-mutation.XXXXXX")"
MODULE_VERIFICATION_JSON="$MUTATION_WORK/module-verification.json"
USERSPACE_VERIFICATION_JSON="$MUTATION_WORK/userspace-verification.json"
INITRAMFS_VERIFICATION_JSON="$MUTATION_WORK/initramfs-verification.json"
PAYLOAD_RECEIPT_JSON="$MUTATION_WORK/payload-receipt.json"
PACMAN_TRANSACTION_RESULT="$MUTATION_WORK/pacman-transaction.json"
TARGET_EXECUTION_MANIFEST="$MUTATION_WORK/target-execution.json"
POST_TRANSACTION_EXECUTION_MANIFEST="$MUTATION_WORK/post-transaction-execution.json"
TARGET_EXECUTION_FAILURE_JSON="$MUTATION_WORK/target-execution-failure.json"
MOUNTS=()
RUNTIME_MOUNTS_EXPECTED=4
RUNTIME_MOUNTS_RELEASED=0
PHASE=mutation_preflight
COMPLETED=0
ACTIVE_CHILD=""
CANCELLED=0
COMPRESSION_POLICY_ACTIVE=0
ORIGINAL_COMPRESSION_OPTION=""
PACMAN_TRANSACTION_CONFIG=""
INITRAMFS_SCRATCH_ROOT=""
INITRAMFS_SCRATCH=""
INITRAMFS_WORKSPACE_REQUIRED_BYTES="$(json_value storage initramfsReserveBytes)"
INITRAMFS_WORKSPACE_REQUIRED_INODES=4096

require_target_mount_identities()
{
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        [[ "${MOCK_MOUNT_IDENTITY_DRIFT:-0}" == 0 ]] || return 1
        return 0
    fi
    local current_root current_efi
    mountpoint -q "$ROOT" && ! mountpoint -q "$ROOT/boot" &&
        mountpoint -q "$ROOT/efi" || return 1
    current_root="$(findmnt -rn -M "$ROOT" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS)" || return 1
    current_efi="$(findmnt -rn -M "$ROOT/efi" \
        -o ID,SOURCE,FSTYPE,MAJ:MIN,VFS-OPTIONS,FS-OPTIONS)" || return 1
    [[ "$current_root" == "$ROOT_MOUNT_IDENTITY" &&
       "$current_efi" == "$EFI_MOUNT_IDENTITY" ]]
}

guard_target_mount_identities()
{
    require_target_mount_identities || {
        PHASE=target_mount_identity
        die "The target rootfs or EFI mount identity changed during installation."
    }
}

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
    local source="$1" target="$2"
    mountpoint -q "$target" || return 1
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        local source_device target_device
        source_device="$(findmnt -rn -T "$source" -o MAJ:MIN)" || return 1
        target_device="$(findmnt -rn -M "$target" -o MAJ:MIN)" || return 1
        [[ -n "$source_device" && "$source_device" == "$target_device" ]]
        return
    fi
    python3 "$SUPPORT_ROOT/lib/verify_bind_mount.py" \
        --source "$source" --target "$target"
}

require_runtime_bind_mounts()
{
    local bind_path
    for bind_path in dev proc sys; do
        require_runtime_bind_mount "/$bind_path" "$ROOT/$bind_path" || return 1
    done
    [[ -n "$INITRAMFS_SCRATCH" ]] || return 1
    require_runtime_bind_mount "$INITRAMFS_SCRATCH" "$ROOT/var/tmp" || return 1
    if ! python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
        --root "$ROOT" --backing "$INITRAMFS_SCRATCH" \
        --required-bytes "$INITRAMFS_WORKSPACE_REQUIRED_BYTES" \
        --required-inodes "$INITRAMFS_WORKSPACE_REQUIRED_INODES" \
        --output "$INITRAMFS_WORKSPACE_JSON" --mounted >/dev/null 2>&1
    then
        PHASE=initramfs_workspace_unavailable
        return 1
    fi
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
    # Every entry in MOUNTS was recorded only after its mount command
    # succeeded.  Always attempt the corresponding recursive unmount instead
    # of allowing a failed/stale discovery probe to strand a known mount.
    # A target that disappeared concurrently is acceptable only when the
    # authoritative postcondition confirms that no mount remains there.
    umount -R "$target" >/dev/null 2>&1 ||
        ! findmnt -rn -R "$target" >/dev/null 2>&1 || return 1
    ! findmnt -rn -R "$target" >/dev/null 2>&1
}

cleanup_mutation()
{
    local rc=$? index mounts_released=true compression_restored=true
    local workspace_released=true target_identity_safe=true
    local released_mounts=0 total_mounts=${#MOUNTS[@]}
    trap - EXIT INT TERM
    emit_progress_items mount_cleanup 0 "$total_mounts"
    if (( total_mounts > 0 )) || [[ "$COMPRESSION_POLICY_ACTIVE" == 1 ]]; then
        require_target_mount_identities || target_identity_safe=false
    fi
    for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
        if unmount_tree "${MOUNTS[$index]}"; then
            released_mounts=$((released_mounts + 1))
            emit_progress_items mount_cleanup "$released_mounts" "$total_mounts"
        else
            mounts_released=false
        fi
        require_target_mount_identities || target_identity_safe=false
    done
    if (( released_mounts != total_mounts )); then
        mounts_released=false
    fi
    RUNTIME_MOUNTS_RELEASED=$released_mounts
    if [[ "$target_identity_safe" == true ]]; then
        restore_compression_policy || compression_restored=false
    else
        compression_restored=false
    fi
    if [[ -n "$INITRAMFS_SCRATCH_ROOT" ]]; then
        rm -rf "$INITRAMFS_SCRATCH_ROOT" || workspace_released=false
        [[ ! -e "$INITRAMFS_SCRATCH_ROOT" ]] || workspace_released=false
    fi
    if [[ "$COMPLETED" != 1 ]]; then
        if [[ "$mounts_released" == true && "$compression_restored" == true &&
              "$workspace_released" == true ]]; then
            if [[ "$CANCELLED" == 1 ]]; then
                write_install_result cancelled cancelled \
                    "Offline-root mutation was cancelled; discard the disposable overlay." \
                    "$PHASE" true || true
            else
                if [[ "$PHASE" == target_execution_trust ]]; then
                    write_install_result failed target_execution_trust \
                        "Target-owned execution trust validation failed; discard the disposable overlay." \
                        target_execution_trust true || true
                else
                    write_install_result failed "$PHASE" \
                        "Offline-root mutation failed; discard the disposable overlay." \
                        "$PHASE" true || true
                fi
            fi
        else
            write_install_result failed mutation_cleanup_failed \
                "Offline-root mutation failed and mount or compression policy cleanup was incomplete." \
                cleanup "$mounts_released" "$compression_restored" || true
        fi
    fi
    rm -rf "$MUTATION_WORK" "$INPUT_SNAPSHOT_ROOT" "$VALIDATION_JSON" \
        "$INITRAMFS_WORKSPACE_JSON" \
        ${BUNDLE_IMPORT_RESULT:+"$BUNDLE_IMPORT_RESULT"} \
        ${BUNDLE_RESOLUTION:+"$BUNDLE_RESOLUTION"} \
        ${BUNDLE_LEASE:+"$BUNDLE_LEASE"} \
        >/dev/null 2>&1 || true
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

guard_target_mount_identities

PHASE=target_execution_trust
run_mutation_command python3 "$SUPPORT_ROOT/lib/snapshot_target_execution.py" \
    --root "$ROOT" --output "$TARGET_EXECUTION_MANIFEST" \
    --diagnostic "$TARGET_EXECUTION_FAILURE_JSON" ||
    die "Target-owned pacman hooks or initramfs inputs are unsafe."

PHASE=initramfs_workspace_unavailable
run_mutation_command python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
    --root "$ROOT" --target-only --create-missing-target \
    --required-bytes 4096 --required-inodes 1 \
    --output "$INITRAMFS_WORKSPACE_JSON" ||
    die "The mounted target /var/tmp could not be prepared safely."
guard_target_mount_identities
PHASE=mutation_preflight

if [[ -n "$COMPRESSION_PROFILE" ]]; then
    PHASE=compression_policy_activation
    activate_compression_policy ||
        die "The measured Btrfs compression policy could not be activated and verified."
fi

emit_progress_indeterminate pacman_policy
if [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 || -n "$GAMING_PAYLOAD_PROFILE" ]]; then
    PHASE=gaming_payload_pacman_policy
    PACMAN_POLICY_ARGS=(--check-space-policy preserve --local-file-policy preserve)
    if [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]]; then
        PHASE=pacman_checkspace_policy
        PACMAN_POLICY_ARGS=(--check-space-policy disable-measured --local-file-policy preserve)
    fi
    if [[ -n "$GAMING_PAYLOAD_PROFILE" ]]; then
        PACMAN_POLICY_ARGS[3]=validated-derived
    fi
    require_validation_document_unchanged ||
        die "The validated installer document changed before pacman policy preparation."
    if [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]]; then
        require_measured_pacman_admission ||
            die "Pacman CheckSpace cannot be bypassed without exact measured Btrfs admission."
        require_active_compression_policy ||
            die "Pacman CheckSpace cannot be bypassed because the measured Btrfs policy is inactive."
    fi
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        PACMAN_CONFIG_SOURCE="${PROJECT_TEST_PACMAN_CONFIG:-}"
        [[ -n "$PACMAN_CONFIG_SOURCE" ]] ||
            die "The test appliance pacman configuration was not provided."
    else
        PACMAN_CONFIG_SOURCE=/etc/pacman.conf
    fi
    PACMAN_TRANSACTION_CONFIG="$MUTATION_WORK/pacman-validated-transaction.conf"
    run_mutation_command python3 "$SUPPORT_ROOT/lib/prepare_pacman_config.py" \
        --source "$PACMAN_CONFIG_SOURCE" --output "$PACMAN_TRANSACTION_CONFIG" \
        "${PACMAN_POLICY_ARGS[@]}" ||
        die "A confined pacman configuration could not be prepared for validated inputs."
    require_validation_document_unchanged ||
        die "The validated installer document changed during transaction preparation."
    if [[ "$COMPRESSION_PROFILE" == btrfs-zstd3 ]]; then
        require_active_compression_policy ||
            die "The measured Btrfs policy changed during pacman transaction preparation."
    fi
fi
emit_progress_items pacman_policy 1 1

PHASE=runtime_mounts
runtime_mount_count=0
emit_progress_items runtime_mounts 0 4
for bind_path in dev proc sys; do
    install -d -m 0755 "$ROOT/$bind_path"
    run_mutation_command mount --rbind "/$bind_path" "$ROOT/$bind_path" ||
        die "A target runtime bind mount could not be created."
    MOUNTS+=("$ROOT/$bind_path")
    run_mutation_command mount --make-rslave "$ROOT/$bind_path" ||
        die "A target runtime bind mount could not be made recursively slave."
    require_runtime_bind_mount "/$bind_path" "$ROOT/$bind_path" ||
        die "A target runtime bind mount could not be independently verified."
    runtime_mount_count=$((runtime_mount_count + 1))
    emit_progress_items runtime_mounts "$runtime_mount_count" 4
done

PHASE=initramfs_workspace_unavailable
INITRAMFS_SCRATCH_PARENT="${PROJECT_INITRAMFS_SCRATCH_PARENT:-/var/tmp}"
if [[ ! -d "$INITRAMFS_SCRATCH_PARENT" || -L "$INITRAMFS_SCRATCH_PARENT" ]]; then
    python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
        --root "$ROOT" \
        --backing "$INITRAMFS_SCRATCH_PARENT/offline-root-initramfs-unavailable/workspace" \
        --required-bytes "$INITRAMFS_WORKSPACE_REQUIRED_BYTES" \
        --required-inodes "$INITRAMFS_WORKSPACE_REQUIRED_INODES" \
        --output "$INITRAMFS_WORKSPACE_JSON" >/dev/null 2>&1 || true
    die "The appliance initramfs scratch parent is unavailable."
fi
INITRAMFS_SCRATCH_ROOT="$(mktemp -d "$INITRAMFS_SCRATCH_PARENT/offline-root-initramfs.XXXXXX")" ||
    die "The private initramfs scratch workspace could not be created."
chmod 0700 "$INITRAMFS_SCRATCH_ROOT"
INITRAMFS_SCRATCH="$INITRAMFS_SCRATCH_ROOT/workspace"
install -d -m 1777 "$INITRAMFS_SCRATCH"
run_mutation_command python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
    --root "$ROOT" --backing "$INITRAMFS_SCRATCH" \
    --required-bytes "$INITRAMFS_WORKSPACE_REQUIRED_BYTES" \
    --required-inodes "$INITRAMFS_WORKSPACE_REQUIRED_INODES" \
    --output "$INITRAMFS_WORKSPACE_JSON" ||
    die "The private initramfs workspace failed validation."
run_mutation_command mount --bind "$INITRAMFS_SCRATCH" "$ROOT/var/tmp" ||
    die "The private initramfs workspace could not be mounted."
MOUNTS+=("$ROOT/var/tmp")
run_mutation_command mount --make-private "$ROOT/var/tmp" ||
    die "The private initramfs workspace could not be isolated."
run_mutation_command python3 "$SUPPORT_ROOT/lib/check_initramfs_workspace.py" \
    --root "$ROOT" --backing "$INITRAMFS_SCRATCH" \
    --required-bytes "$INITRAMFS_WORKSPACE_REQUIRED_BYTES" \
    --required-inodes "$INITRAMFS_WORKSPACE_REQUIRED_INODES" \
    --output "$INITRAMFS_WORKSPACE_JSON" --mounted ||
    die "The mounted initramfs workspace failed verification."
runtime_mount_count=$((runtime_mount_count + 1))
emit_progress_items runtime_mounts "$runtime_mount_count" 4
PHASE=runtime_mounts
require_runtime_bind_mounts ||
    die "The complete target runtime mount set is unavailable."

PHASE=userspace_install
guard_target_mount_identities
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
run_mutation_command python3 "$SUPPORT_ROOT/lib/snapshot_target_execution.py" \
    --root "$ROOT" --verify "$TARGET_EXECUTION_MANIFEST" \
    --diagnostic "$TARGET_EXECUTION_FAILURE_JSON" || {
    PHASE=target_execution_trust
    die "Target-owned pacman hooks or initramfs inputs changed before execution."
}
set +e
run_mutation_command python3 "$SUPPORT_ROOT/lib/run_pacman_transaction.py" \
    --output "$PACMAN_TRANSACTION_RESULT" \
    --progress-attempt "$PROGRESS_ATTEMPT_VALUE" -- \
    env SYSTEMD_OFFLINE=1 pacman "${PACMAN_ARGS[@]}"
PACMAN_RC=$?
set -e
PACMAN_HOOK_FAILURE="$(python3 -c \
    'import json,sys; print("true" if json.load(open(sys.argv[1])).get("hookFailure") is True else "false")' \
    "$PACMAN_TRANSACTION_RESULT" 2>/dev/null || printf unknown)"
if [[ "$PACMAN_HOOK_FAILURE" == true ]]; then
    PHASE=userspace_hook_failed
    die "A target pacman hook failed after the userspace transaction."
fi
(( PACMAN_RC == 0 )) || die "The authenticated userspace transaction failed."
emit_progress_items userspace_install "$package_count" "$package_count"
require_runtime_bind_mounts ||
    die "A pacman hook changed or released a target runtime mount."
require_validation_document_unchanged ||
    die "The validated compression admission document changed during the pacman transaction."
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
POST_INSTALL_VERIFY_ARGS=(
    --root "$ROOT"
    --validation "$VALIDATION_JSON"
    --output "$USERSPACE_VERIFICATION_JSON"
)
for package in "${PACMAN_PACKAGES[@]}"; do
    POST_INSTALL_VERIFY_ARGS+=(--package "$package")
done
emit_progress_items userspace_verification 0 "$package_count"
PHASE=userspace_verification
run_mutation_command python3 "$SUPPORT_ROOT/lib/verify_installed_userspace.py" \
    "${POST_INSTALL_VERIFY_ARGS[@]}" --progress-attempt "$PROGRESS_ATTEMPT_VALUE"

PHASE=module_install
guard_target_mount_identities
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
INITRAMFS_REQUIRED_MODULES=(
    nvidia.ko nvidia-modeset.ko nvidia-uvm.ko nvidia-drm.ko
)
INITRAMFS_MKINITCPIO_MODULES=()
for initramfs_module in "${INITRAMFS_REQUIRED_MODULES[@]}"; do
    initramfs_module="${initramfs_module%.ko}"
    INITRAMFS_MKINITCPIO_MODULES+=("${initramfs_module//-/_}")
done
printf '%s\n' \
    "# Managed by ${PROJECT_NAME}" \
    'blacklist nouveau' \
    'options nouveau modeset=0' \
    'options nvidia-drm modeset=1 fbdev=1' \
    'options nvidia NVreg_PreserveVideoMemoryAllocations=1' \
    > "$ROOT/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
printf '%s\n' \
    "# Managed by ${PROJECT_NAME}" \
    "MODULES=(${INITRAMFS_MKINITCPIO_MODULES[*]})" \
    > "$ROOT/etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf"
run_mutation_command python3 "$SUPPORT_ROOT/lib/snapshot_target_execution.py" \
    --root "$ROOT" --output "$POST_TRANSACTION_EXECUTION_MANIFEST" \
    --diagnostic "$TARGET_EXECUTION_FAILURE_JSON" || {
    PHASE=target_execution_trust
    die "The authenticated transaction left unsafe hook or initramfs inputs."
}
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy

PHASE=bootloader_config
guard_target_mount_identities
emit_progress_indeterminate grub_update
run_mutation_command python3 "$SUPPORT_ROOT/lib/update_grub_nvidia_args.py" \
    --grub-config "$ROOT/efi/EFI/steamos/grub.cfg"
emit_progress_items grub_update 1 1

PHASE=depmod
guard_target_mount_identities
emit_progress_indeterminate depmod
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
run_mutation_command depmod -b "$ROOT" -a "$KERNEL"
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
emit_progress_items depmod 1 1

PHASE=initramfs
guard_target_mount_identities
emit_progress_indeterminate initramfs
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
require_runtime_bind_mounts ||
    die "Target runtime mounts disappeared before initramfs generation."
run_mutation_command python3 "$SUPPORT_ROOT/lib/snapshot_target_execution.py" \
    --root "$ROOT" --verify "$POST_TRANSACTION_EXECUTION_MANIFEST" \
    --diagnostic "$TARGET_EXECUTION_FAILURE_JSON" || {
    PHASE=target_execution_trust
    die "Target-owned initramfs inputs changed before execution."
}
run_mutation_command env SYSTEMD_OFFLINE=1 chroot "$ROOT" /usr/bin/mkinitcpio -P
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
INITRAMFS_VERIFY_ARGS=(
    --kernel "$KERNEL" --execution-manifest "$POST_TRANSACTION_EXECUTION_MANIFEST"
    --config "$ROOT/etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf"
    --output "$INITRAMFS_VERIFICATION_JSON"
)
for initramfs_module in "${INITRAMFS_REQUIRED_MODULES[@]}"; do
    INITRAMFS_VERIFY_ARGS+=(--module "$initramfs_module")
done
shopt -s nullglob
INITRAMFS_IMAGES=("$ROOT"/boot/initramfs-*.img)
shopt -u nullglob
(( ${#INITRAMFS_IMAGES[@]} > 0 && ${#INITRAMFS_IMAGES[@]} <= 32 )) || {
    PHASE=initramfs_verification
    die "Initramfs generation did not produce a bounded image set."
}
for (( initramfs_index=0; initramfs_index<${#INITRAMFS_IMAGES[@]}; initramfs_index++ )); do
    image="${INITRAMFS_IMAGES[$initramfs_index]}"
    listing="$MUTATION_WORK/initramfs-listing-$initramfs_index"
    image_sha256="$(python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from verify_initramfs import regular_digest; print(regular_digest(Path(sys.argv[2]), 2 * 1024 * 1024 * 1024, "initramfs image")[1])' "$SUPPORT_ROOT/lib" "$image")" || {
        PHASE=initramfs_verification
        die "An initramfs image could not be hashed safely."
    }
    run_mutation_command python3 "$SUPPORT_ROOT/lib/capture_bounded_command.py" \
        --output "$listing" --max-bytes 8388608 --timeout 120 -- \
        chroot "$ROOT" /usr/bin/lsinitcpio -l "/boot/${image##*/}" || {
        PHASE=initramfs_verification
        die "An initramfs image listing could not be captured safely."
    }
    INITRAMFS_VERIFY_ARGS+=(--image "$image" --listing "$listing" \
        --image-sha256 "$image_sha256")
done
run_mutation_command python3 "$SUPPORT_ROOT/lib/verify_initramfs.py" \
    "${INITRAMFS_VERIFY_ARGS[@]}" || {
    PHASE=initramfs_verification
    die "Generated initramfs contents failed exact verification."
}
emit_progress_items initramfs 1 1

PHASE=state_write
guard_target_mount_identities
emit_progress_indeterminate installation_state
[[ -z "$COMPRESSION_PROFILE" ]] || require_active_compression_policy
run_mutation_command python3 "$SUPPORT_ROOT/lib/payload_receipt.py" commit \
    --root "$ROOT" --build-info "$MUTATION_WORK/BUILD-INFO.txt" \
    --provenance "$PROVENANCE" --validation "$VALIDATION_JSON" \
    --module-verification "$MODULE_VERIFICATION_JSON" \
    --userspace-verification "$USERSPACE_VERIFICATION_JSON" \
    --initramfs-verification "$INITRAMFS_VERIFICATION_JSON" \
    --output "$PAYLOAD_RECEIPT_JSON" || {
    PHASE=payload_receipt
    die "The rootfs payload receipt could not be committed and verified."
}
STATE_ROOT="$ROOT/var/lib/$PROJECT_ID/offline-install"
install -d -m 0755 "$STATE_ROOT"
install -m 0644 "$MUTATION_WORK/BUILD-INFO.txt" "$STATE_ROOT/BUILD-INFO.txt"
install -m 0644 "$PROVENANCE" "$STATE_ROOT/PROVENANCE.json"
printf '%s\n' "$KERNEL" > "$STATE_ROOT/kernel-version"
printf '%s\n' "$NVIDIA_VERSION" > "$STATE_ROOT/nvidia-version"
if [[ -n "$GAMING_PAYLOAD_PROFILE" ]]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d["gamingPayload"], sort_keys=True, separators=(",", ":")))' \
        "$VALIDATION_JSON" > "$MUTATION_WORK/gaming-payload.json"
    install -m 0644 "$MUTATION_WORK/gaming-payload.json" \
        "$STATE_ROOT/gaming-payload.json"
else
    rm -f "$STATE_ROOT/gaming-payload.json"
fi
emit_progress_items installation_state 1 1

PHASE=cleanup
guard_target_mount_identities
released_mounts=0
total_mounts=${#MOUNTS[@]}
emit_progress_items mount_cleanup 0 "$total_mounts"
for (( index=${#MOUNTS[@]}-1; index>=0; index-- )); do
    guard_target_mount_identities
    unmount_tree "${MOUNTS[$index]}"
    released_mounts=$((released_mounts + 1))
    emit_progress_items mount_cleanup "$released_mounts" "$total_mounts"
done
MOUNTS=()
RUNTIME_MOUNTS_RELEASED=$released_mounts
guard_target_mount_identities
if findmnt -rn -R "$ROOT/dev" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/proc" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/sys" >/dev/null 2>&1 ||
   findmnt -rn -R "$ROOT/var/tmp" >/dev/null 2>&1; then
    die "One or more target bind mounts remain after recursive cleanup."
fi
if [[ -n "$COMPRESSION_PROFILE" ]]; then
    PHASE=compression_policy_restore
    restore_compression_policy ||
        die "The target Btrfs compression policy could not be restored."
fi

PHASE=initramfs_workspace_cleanup
rm -rf "$INITRAMFS_SCRATCH_ROOT"
INITRAMFS_SCRATCH_ROOT=""
PHASE=complete
write_install_result success install_complete \
    "NVIDIA modules, authenticated userspace, GSP firmware, and initramfs were installed." \
    complete true
COMPLETED=1
trap - EXIT INT TERM
rm -rf "$MUTATION_WORK" "$INPUT_SNAPSHOT_ROOT" "$VALIDATION_JSON" \
    "$INITRAMFS_WORKSPACE_JSON" \
    ${BUNDLE_IMPORT_RESULT:+"$BUNDLE_IMPORT_RESULT"} \
    ${BUNDLE_RESOLUTION:+"$BUNDLE_RESOLUTION"} \
    ${BUNDLE_LEASE:+"$BUNDLE_LEASE"}
ok "Offline-root NVIDIA installation completed with all mounts released."
