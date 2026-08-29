#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
source "${PROJECT_ROOT}/lib/common.sh"

usage()
{
    cat <<EOF
Usage: transaction.sh

Run install/uninstall transaction and rollback integration tests inside a
temporary fake system root. Real sudo and real SteamOS system paths are never
used.
EOF
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
fi

(( EUID != 0 )) || die "Run transaction tests as the normal deck/user account."

for command_name in cmp diff find git id install realpath sha256sum sort tar xargs zstd; do
    need_cmd "$command_name"
done

REAL_KERNEL="$(get_kernel_version)"
MOCK_STEAMOS="3.8.16"
MOCK_NVIDIA="580.119.02"
export MOCK_NVIDIA

WORK_ROOT="$(mktemp -d /tmp/open-gpu-transaction.XXXXXX)"
REAL_MODULE_DIR="/usr/lib/modules/${REAL_KERNEL}/updates/open-gpu-kernel-modules-steamos"
REAL_BEFORE="${WORK_ROOT}/real-modules.before"
REAL_AFTER="${WORK_ROOT}/real-modules.after"

cleanup()
{
    local rc=$?
    rm -rf "$WORK_ROOT" >/dev/null 2>&1 || true
    exit "$rc"
}
trap cleanup EXIT INT TERM

snapshot_real_modules()
{
    local output="$1"
    if [[ -d "$REAL_MODULE_DIR" ]]; then
        find "$REAL_MODULE_DIR" -maxdepth 1 -type f -name '*.ko*' -print0 |
            LC_ALL=C sort -z |
            xargs -0 -r sha256sum > "$output"
    else
        : > "$output"
    fi
}

snapshot_fake_state()
{
    local output="$1"
    : > "$output"

    for root in "$MOCK_TARGET_DIR" "$MOCK_STATE_ROOT"; do
        if [[ -d "$root" ]]; then
            find "$root" -type f -print0 |
                LC_ALL=C sort -z |
                while IFS= read -r -d '' file; do
                    printf '%s  %s\n' "$(sha256_file "$file")" "${file#"$CASE_ROOT"/}"
                done >> "$output"
        fi
    done
}

assert_fake_state_restored()
{
    local before="$1"
    local after="$2"
    cmp "$before" "$after" || {
        diff -u "$before" "$after" >&2 || true
        die "Fake module/state tree was not restored byte-for-byte."
    }

    [[ "$(<"$MOCK_READONLY_STATE")" == "enabled" ]] ||
        die "SteamOS readonly state was not restored."

    mapfile -t remaining_stages < <(
        find "${HOME}/.cache/${PROJECT_NAME}" -maxdepth 1 -type d \
            \( -name 'install-stage.*' -o -name 'install-extract.*' \) -print \
            2>/dev/null
    )
    (( ${#remaining_stages[@]} == 0 )) ||
        die "Installer left an orphaned stage directory."

    mapfile -t foreign_cache_paths < <(
        find "${HOME}/.cache/${PROJECT_NAME}" -xdev ! -user "$(id -u)" -print \
            2>/dev/null
    )
    (( ${#foreign_cache_paths[@]} == 0 )) ||
        die "Transaction left cache files owned by another user."
}

make_release_archive()
{
    local package_dir="${WORK_ROOT}/release-package"
    mkdir -p "${package_dir}/modules"

    for module_name in \
        nvidia-drm.ko \
        nvidia-modeset.ko \
        nvidia-peermem.ko \
        nvidia-uvm.ko \
        nvidia.ko
    do
        printf 'new fixture content for %s\n' "$module_name" > \
            "${package_dir}/modules/${module_name}"
    done

    cat > "${package_dir}/BUILD-INFO.txt" <<EOF
open-gpu-kernel-modules-steamos build information

schema_version=1
steamos_version=${MOCK_STEAMOS}
kernel_version=${REAL_KERNEL}
nvidia_version=${MOCK_NVIDIA}
source_provider=test-fixture
project_patches=0
EOF

    RELEASE_ARCHIVE="${WORK_ROOT}/transaction-fixture.tar.gz"
    tar -C "$package_dir" -czf "$RELEASE_ARCHIVE" modules BUILD-INFO.txt
    printf '%s  %s\n' "$(sha256_file "$RELEASE_ARCHIVE")" \
        "$(basename "$RELEASE_ARCHIVE")" > "${RELEASE_ARCHIVE}.sha256"
}

reset_case()
{
    local case_name="$1"
    local old_module_format="${2:-compressed}"
    CASE_ROOT="${WORK_ROOT}/cases/${case_name}"
    rm -rf "$CASE_ROOT"
    mkdir -p "$CASE_ROOT"

    export PROJECT_TEST_MODE=1
    export PROJECT_TEST_ROOT="${CASE_ROOT}/system"
    export HOME="${CASE_ROOT}/home"
    export XDG_STATE_HOME="${HOME}/.local/state"
    export PATH="${TEST_DIR}/fixtures/transaction/bin:${ORIGINAL_PATH}"
    export MOCK_KERNEL="$REAL_KERNEL"
    export MOCK_TARGET_DIR="${PROJECT_TEST_ROOT}/usr/lib/modules/${REAL_KERNEL}/updates/open-gpu-kernel-modules-steamos"
    export MOCK_STATE_ROOT="${PROJECT_TEST_ROOT}/var/lib/open-gpu-kernel-modules-steamos-support"
    export MOCK_FALLBACK_MODULE="${PROJECT_TEST_ROOT}/usr/lib/modules/${REAL_KERNEL}/fallback/nvidia.ko.zst"
    export MOCK_UNRELATED_FILE="${PROJECT_TEST_ROOT}/usr/lib/modules/${REAL_KERNEL}/updates/unrelated-nvidia/nvidia-extra.ko"
    export MOCK_READONLY_STATE="${CASE_ROOT}/readonly-state"
    export MOCK_COMMAND_LOG="${CASE_ROOT}/commands.log"
    export MOCK_FAILURE_MARKER="${CASE_ROOT}/failure-injected"
    export MOCK_FAIL_POINT=""

    mkdir -p "$MOCK_TARGET_DIR" "$MOCK_STATE_ROOT" \
        "${PROJECT_TEST_ROOT}/etc" \
        "$(dirname "$MOCK_FALLBACK_MODULE")" \
        "$(dirname "$MOCK_UNRELATED_FILE")" "$HOME"
    cat > "${PROJECT_TEST_ROOT}/etc/os-release" <<EOF
NAME=SteamOS
ID=steamos
VERSION_ID=${MOCK_STEAMOS}
BUILD_ID=transaction-test
EOF
    printf 'enabled\n' > "$MOCK_READONLY_STATE"
    : > "$MOCK_COMMAND_LOG"

    for module_name in \
        nvidia-drm.ko \
        nvidia-modeset.ko \
        nvidia-peermem.ko \
        nvidia-uvm.ko \
        nvidia.ko
    do
        if [[ "$old_module_format" == "raw" ]]; then
            printf 'old fixture content for %s\n' "$module_name" > \
                "${MOCK_TARGET_DIR}/${module_name}"
        else
            printf 'old fixture content for %s\n' "$module_name" |
                zstd -q -o "${MOCK_TARGET_DIR}/${module_name}.zst"
        fi
    done

    printf 'fallback fixture\n' | zstd -q -o "$MOCK_FALLBACK_MODULE"
    printf 'unrelated NVIDIA fixture\n' > "$MOCK_UNRELATED_FILE"
    printf 'old build info\n' > "${MOCK_STATE_ROOT}/installed-build-info.txt"
    printf 'old archive\n' > "${MOCK_STATE_ROOT}/installed-archive.txt"
    printf '%s\n' "$REAL_KERNEL" > "${MOCK_STATE_ROOT}/installed-kernel.txt"
    printf 'old-nvidia\n' > "${MOCK_STATE_ROOT}/installed-nvidia.txt"
}

run_install_failure_case()
{
    local case_name="$1"
    local fail_point="$2"
    local old_module_format="${3:-compressed}"
    local before="${WORK_ROOT}/${case_name}.before"
    local after="${WORK_ROOT}/${case_name}.after"
    local output rc

    printf 'Testing install rollback case %s (%s target)...\n' \
        "$case_name" "$old_module_format"
    reset_case "$case_name" "$old_module_format"
    snapshot_fake_state "$before"
    export MOCK_FAIL_POINT="$fail_point"

    set +e
    output="$(
        "${PROJECT_ROOT}/bootstrap/install.sh" \
            --archive "$RELEASE_ARCHIVE" \
            --checksum "${RELEASE_ARCHIVE}.sha256" \
            --yes \
            2>&1
    )"
    rc=$?
    set -e

    (( rc != 0 )) || die "Expected injected ${fail_point} install failure."
    [[ -e "$MOCK_FAILURE_MARKER" ]] || die "Failure injection did not run: $fail_point"
    [[ "$output" == *"restoring previous updates directory"* ]] || {
        printf '%s\n' "$output" >&2
        die "Installer did not report target rollback: $fail_point"
    }
    (( $(grep -c '^depmod ' "$MOCK_COMMAND_LOG") >= 1 )) ||
        die "Rollback did not refresh mocked module dependencies: $fail_point"
    (( $(grep -c '^mkinitcpio ' "$MOCK_COMMAND_LOG") >= 1 )) ||
        die "Rollback did not refresh mocked initramfs: $fail_point"
    if [[ "$fail_point" == "state-write" ]]; then
        [[ "$output" == *"Restoring previous install state metadata"* ]] ||
            die "Partial state write did not trigger state rollback."
    fi

    snapshot_fake_state "$after"
    assert_fake_state_restored "$before" "$after"
}

run_uninstall_failure_case()
{
    local before="${WORK_ROOT}/uninstall-depmod.before"
    local after="${WORK_ROOT}/uninstall-depmod.after"
    local output rc

    printf 'Testing uninstall rollback after target removal...\n'
    reset_case uninstall-depmod
    snapshot_fake_state "$before"
    export MOCK_FAIL_POINT=depmod

    set +e
    output="$("${PROJECT_ROOT}/bootstrap/uninstall.sh" --yes 2>&1)"
    rc=$?
    set -e

    (( rc != 0 )) || die "Expected injected uninstall depmod failure."
    [[ -e "$MOCK_FAILURE_MARKER" ]] || die "Uninstall failure injection did not run."
    [[ "$output" == *"restoring NVIDIA open kernel modules"* ]] || {
        printf '%s\n' "$output" >&2
        die "Uninstaller did not report rollback."
    }
    (( $(grep -c '^depmod ' "$MOCK_COMMAND_LOG") >= 2 )) ||
        die "Uninstall rollback did not retry mocked depmod."
    (( $(grep -c '^mkinitcpio ' "$MOCK_COMMAND_LOG") >= 1 )) ||
        die "Uninstall rollback did not rebuild mocked initramfs."

    snapshot_fake_state "$after"
    assert_fake_state_restored "$before" "$after"
}

run_successful_lifecycle()
{
    local output

    printf 'Testing successful fake-root install and uninstall...\n'
    reset_case successful-lifecycle

    output="$(
        "${PROJECT_ROOT}/bootstrap/install.sh" \
            --archive "$RELEASE_ARCHIVE" \
            --checksum "${RELEASE_ARCHIVE}.sha256" \
            --yes \
            2>&1
    )"
    [[ "$output" == *"installed successfully"* ]] || {
        printf '%s\n' "$output" >&2
        die "Fake-root install did not succeed."
    }

    mapfile -t installed_modules < <(
        find "$MOCK_TARGET_DIR" -maxdepth 1 -type f -name '*.ko.zst' -print
    )
    validate_nvidia_module_set "${installed_modules[@]}" ||
        die "Successful install produced the wrong module set."
    [[ "$(<"${MOCK_STATE_ROOT}/installed-nvidia.txt")" == "$MOCK_NVIDIA" ]] ||
        die "Successful install wrote incorrect state metadata."
    [[ "$(<"$MOCK_READONLY_STATE")" == "enabled" ]] ||
        die "Successful install did not restore readonly state."

    output="$("${PROJECT_ROOT}/bootstrap/uninstall.sh" --yes 2>&1)"
    [[ "$output" == *"removed successfully"* ]] || {
        printf '%s\n' "$output" >&2
        die "Fake-root uninstall did not succeed."
    }
    [[ ! -e "$MOCK_TARGET_DIR" ]] || die "Successful uninstall left target modules."
    [[ -f "$MOCK_UNRELATED_FILE" ]] ||
        die "Successful uninstall removed an unrelated NVIDIA file."
    [[ ! -e "${MOCK_STATE_ROOT}/installed-nvidia.txt" ]] ||
        die "Successful uninstall left installation state."
    [[ "$(<"$MOCK_READONLY_STATE")" == "enabled" ]] ||
        die "Successful uninstall did not restore readonly state."

    output="$(
        "${PROJECT_ROOT}/bootstrap/install.sh" \
            --archive "$RELEASE_ARCHIVE" \
            --checksum "${RELEASE_ARCHIVE}.sha256" \
            --yes \
            2>&1
    )"
    [[ "$output" == *"installed successfully"* ]] || {
        printf '%s\n' "$output" >&2
        die "Fake-root reinstall after uninstall did not succeed."
    }
    [[ -f "${MOCK_TARGET_DIR}/nvidia.ko.zst" ]] ||
        die "Reinstall after uninstall did not restore project modules."

    mapfile -t backup_generations < <(
        find "${HOME}/.cache/${PROJECT_NAME}/backups/${REAL_KERNEL}" \
            -mindepth 1 -maxdepth 1 -type d -print
    )
    (( ${#backup_generations[@]} == 3 )) ||
        die "Sequential lifecycle operations did not create unique backup generations."
}

ORIGINAL_PATH="$PATH"
snapshot_real_modules "$REAL_BEFORE"
make_release_archive

run_install_failure_case install-partial-copy target-copy
run_install_failure_case install-initramfs initramfs
run_install_failure_case install-legacy-raw-target initramfs raw
run_install_failure_case install-state-write state-write
run_uninstall_failure_case
run_successful_lifecycle

snapshot_real_modules "$REAL_AFTER"
cmp "$REAL_BEFORE" "$REAL_AFTER" ||
    die "Real installed NVIDIA modules changed during fake-root tests."

printf 'All fake-root transaction tests passed; real system modules were untouched.\n'
