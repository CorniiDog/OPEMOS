#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
source "${PROJECT_ROOT}/lib/common.sh"

RUN_ONLINE=0
ARTIFACT=""
REPORT=""

usage()
{
    cat <<EOF
Usage: non_sudo.sh [options]

Run the repeatable user-space regression baseline. The suite places a failing
sudo shim first in PATH and aborts if any tested workflow tries to use it.

Options:
      --artifact FILE  Validate FILE and dry-run its installer through the
                       confirmation boundary when it matches this system.
      --online         Also exercise all three network-backed resolver modes.
      --report FILE    Write machine-readable baseline data to FILE.
  -h, --help           Show this help.

If --artifact is omitted, the newest cached pristine-upstream archive is used
when available. The default report is stored under the project cache.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifact)
            [[ $# -ge 2 ]] || die "--artifact requires a file."
            ARTIFACT="$2"
            shift 2
            ;;
        --online)
            RUN_ONLINE=1
            shift
            ;;
        --report)
            [[ $# -ge 2 ]] || die "--report requires a file."
            REPORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

(( EUID != 0 )) || die "Run this baseline as the normal deck/user account, not root."

for command_name in \
    awk basename cmp cut date diff find git grep head id install mktemp python3 \
    realpath sed sha256sum sort stat tar zstd
do
    need_cmd "$command_name"
done

require_steamos

CACHE_ROOT="$(project_cache_root)"
mkdir -p "$CACHE_ROOT"

if [[ -z "$REPORT" ]]; then
    REPORT="${CACHE_ROOT}/baselines/non-sudo-current.txt"
fi

REPORT="$(realpath -m "$REPORT")"
mkdir -p "$(dirname "$REPORT")"

SCRATCH="$(project_mktemp_dir non-sudo-test)"
SUDO_GUARD_MARKER="${SCRATCH}/sudo-called"
export SUDO_GUARD_MARKER
export PATH="${TEST_DIR}/fixtures/no-sudo/bin:${PATH}"

cleanup()
{
    local rc=$?
    rm -rf "$SCRATCH" >/dev/null 2>&1 || true
    exit "$rc"
}
trap cleanup EXIT INT TERM

REPORT_TMP="${SCRATCH}/baseline.txt"
: > "$REPORT_TMP"

report()
{
    local key="$1"
    local value="${2:-}"
    value="${value//$'\n'/ }"
    value="${value//$'\t'/ }"
    printf '%s=%s\n' "$key" "$value" >> "$REPORT_TMP"
}

assert_user_owned()
{
    local path="$1"
    local owner_uid
    owner_uid="$(stat -c '%u' "$path")"
    [[ "$owner_uid" == "$(id -u)" ]] ||
        die "Not owned by the invoking user: $path (uid ${owner_uid})"
}

metadata_value_from_text()
{
    local text="$1"
    local key="$2"
    sed -n "s/^${key}=//p" <<< "$text" | head -n1
}

module_content_sha256()
{
    local module="$1"

    case "$module" in
        *.ko.zst) zstd -q -dc -- "$module" | sha256sum | awk '{print $1}' ;;
        *.ko) sha256_file "$module" ;;
        *) return 1 ;;
    esac
}

printf 'Running fast repository checks...\n'
"${TEST_DIR}/check.sh"

printf 'Testing user-owned cache and zstd staging...\n'
assert_user_owned "$CACHE_ROOT"
assert_user_owned "$SCRATCH"

STAGE="$(project_mktemp_dir non-sudo-zstd)"
assert_user_owned "$STAGE"
printf 'non-sudo zstd regression fixture\n' > "${STAGE}/fixture.ko"
zstd -q "${STAGE}/fixture.ko" -o "${STAGE}/fixture.ko.zst"
zstd -q -t "${STAGE}/fixture.ko.zst"
cmp "${STAGE}/fixture.ko" <(zstd -q -dc "${STAGE}/fixture.ko.zst")
assert_user_owned "${STAGE}/fixture.ko.zst"
rm -rf "$STAGE"

mapfile -t FOREIGN_CACHE_PATHS < <(
    find "$CACHE_ROOT" -xdev ! -user "$(id -u)" -print
)
(( ${#FOREIGN_CACHE_PATHS[@]} == 0 )) || {
    printf 'Cache paths not owned by uid %s:\n' "$(id -u)" >&2
    printf '  %s\n' "${FOREIGN_CACHE_PATHS[@]}" >&2
    die "Project cache contains files owned by another account."
}

STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"
NVIDIA_VERSION="$(get_nvidia_version 2>/dev/null || true)"
BUILD_ID_VALUE="$(
    source /etc/os-release
    printf '%s' "${BUILD_ID:-unknown}"
)"

report baseline_schema 1
report generated_at "$(date --iso-8601=seconds)"
report invoking_user "$(id -un)"
report invoking_uid "$(id -u)"
report invoking_gid "$(id -g)"
report steamos_version "$STEAMOS_VERSION"
report steamos_build_id "$BUILD_ID_VALUE"
report kernel_version "$KERNEL_VERSION"
report nvidia_userspace_version "${NVIDIA_VERSION:-unavailable}"
report support_commit "$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
report support_dirty "$([[ -z "$(git -C "$PROJECT_ROOT" status --porcelain)" ]] && printf 0 || printf 1)"
report cache_root "$CACHE_ROOT"
report cache_owner_uid "$(stat -c '%u' "$CACHE_ROOT")"
report cache_owner_gid "$(stat -c '%g' "$CACHE_ROOT")"

if command -v steamos-readonly >/dev/null 2>&1; then
    report steamos_readonly "$(steamos-readonly status 2>/dev/null || printf unknown)"
else
    report steamos_readonly unavailable
fi

TARGET_DIR="/usr/lib/modules/${KERNEL_VERSION}/updates/open-gpu-kernel-modules-steamos"
if [[ -d "$TARGET_DIR" ]]; then
    mapfile -t INSTALLED_MODULES < <(
        find "$TARGET_DIR" -maxdepth 1 -type f \
            \( -name '*.ko' -o -name '*.ko.zst' \) -print |
            LC_ALL=C sort
    )
    validate_nvidia_module_set "${INSTALLED_MODULES[@]}" ||
        die "Installed project directory does not contain the exact NVIDIA module set."

    report installed_project_modules present
    report installed_module_count "${#INSTALLED_MODULES[@]}"
    for module in "${INSTALLED_MODULES[@]}"; do
        name="$(basename "$module")"
        report "installed_${name%.zst}_content_sha256" \
            "$(module_content_sha256 "$module")"
    done
else
    report installed_project_modules absent
    report installed_module_count 0
fi

if [[ -z "$ARTIFACT" ]]; then
    mapfile -t CACHED_ARTIFACTS < <(
        find "${CACHE_ROOT}/upstream-builds" -maxdepth 1 -type f \
            -name '*.tar.gz' -printf '%T@ %p\n' 2>/dev/null |
            sort -rn |
            cut -d' ' -f2-
    )
    if (( ${#CACHED_ARTIFACTS[@]} > 0 )); then
        ARTIFACT="${CACHED_ARTIFACTS[0]}"
    fi
fi

if [[ -n "$ARTIFACT" ]]; then
    ARTIFACT="$(realpath "$ARTIFACT")"
    [[ -f "$ARTIFACT" ]] || die "Artifact not found: $ARTIFACT"
    [[ -f "${ARTIFACT}.sha256" ]] || die "Artifact checksum not found: ${ARTIFACT}.sha256"

    printf 'Validating cached artifact without installation...\n'
    EXPECTED_SHA="$(awk '{print $1}' "${ARTIFACT}.sha256" | head -n1)"
    ACTUAL_SHA="$(sha256_file "$ARTIFACT")"
    [[ "$EXPECTED_SHA" == "$ACTUAL_SHA" ]] || die "Artifact checksum mismatch."

    ARTIFACT_DIR="${SCRATCH}/artifact"
    mkdir -p "$ARTIFACT_DIR"
    tar -xzf "$ARTIFACT" -C "$ARTIFACT_DIR"
    [[ -f "${ARTIFACT_DIR}/BUILD-INFO.txt" ]] || die "Artifact lacks BUILD-INFO.txt."

    mapfile -t ARTIFACT_MODULES < <(
        find "${ARTIFACT_DIR}/modules" -maxdepth 1 -type f \
            \( -name '*.ko' -o -name '*.ko.zst' \) -print |
            LC_ALL=C sort
    )
    validate_nvidia_module_set "${ARTIFACT_MODULES[@]}" ||
        die "Artifact does not contain the exact NVIDIA module set."

    BUILD_INFO="$(<"${ARTIFACT_DIR}/BUILD-INFO.txt")"
    ARTIFACT_STEAMOS="$(metadata_value_from_text "$BUILD_INFO" steamos_version)"
    ARTIFACT_KERNEL="$(metadata_value_from_text "$BUILD_INFO" kernel_version)"
    ARTIFACT_NVIDIA="$(metadata_value_from_text "$BUILD_INFO" nvidia_version)"

    report artifact_path "$ARTIFACT"
    report artifact_sha256 "$ACTUAL_SHA"
    report artifact_steamos_version "$ARTIFACT_STEAMOS"
    report artifact_kernel_version "$ARTIFACT_KERNEL"
    report artifact_nvidia_version "$ARTIFACT_NVIDIA"
    report artifact_source_provider "$(metadata_value_from_text "$BUILD_INFO" source_provider)"
    report artifact_project_patches "$(metadata_value_from_text "$BUILD_INFO" project_patches)"
    report artifact_support_commit "$(metadata_value_from_text "$BUILD_INFO" support_commit)"

    if [[ "$ARTIFACT_STEAMOS" == "$STEAMOS_VERSION" &&
          "$ARTIFACT_KERNEL" == "$KERNEL_VERSION" &&
          -n "$NVIDIA_VERSION" &&
          "$ARTIFACT_NVIDIA" == "$NVIDIA_VERSION" ]]; then
        printf 'Testing installer validation through the no-confirmation boundary...\n'
        set +e
        INSTALL_OUTPUT="$(
            printf 'n\n' |
                "${PROJECT_ROOT}/bootstrap/install.sh" \
                    --archive "$ARTIFACT" \
                    --checksum "${ARTIFACT}.sha256" \
                    2>&1
        )"
        INSTALL_RC=$?
        set -e

        [[ "$INSTALL_RC" == 1 && "$INSTALL_OUTPUT" == *"Install candidate"* &&
           "$INSTALL_OUTPUT" == *"Install cancelled"* ]] || {
            printf '%s\n' "$INSTALL_OUTPUT" >&2
            die "Installer did not reach the safe cancellation boundary."
        }
        report installer_validation candidate_then_cancelled
    else
        report installer_validation skipped_metadata_mismatch
    fi
else
    report artifact_path unavailable
    report installer_validation skipped_no_artifact
fi

if [[ "$RUN_ONLINE" == "1" ]]; then
    printf 'Testing network-backed resolver modes...\n'

    CERTIFIED_OUTPUT="$("${PROJECT_ROOT}/bootstrap/setup_nvidia.sh" --resolve-only)"
    [[ "$CERTIFIED_OUTPUT" == *"Selection mode:    certified"* ]] ||
        die "Certified resolver mode failed."

    DEVELOPMENT_OUTPUT="$(
        "${PROJECT_ROOT}/bootstrap/setup_nvidia.sh" --development 580 --resolve-only
    )"
    [[ "$DEVELOPMENT_OUTPUT" == *"Selection mode:    development"* &&
       "$DEVELOPMENT_OUTPUT" == *"leave installed kernel modules unchanged"* ]] ||
        die "Development resolver crossed its module boundary."

    UPSTREAM_OUTPUT="$(
        "${PROJECT_ROOT}/bootstrap/setup_nvidia.sh" --use-upstream 580 --resolve-only
    )"
    [[ "$UPSTREAM_OUTPUT" == *"Selection mode:    upstream-development"* &&
       "$UPSTREAM_OUTPUT" == *"project fixes are not applied"* ]] ||
        die "Upstream resolver semantics failed."

    report online_resolvers pass
else
    report online_resolvers skipped
fi

[[ ! -e "$SUDO_GUARD_MARKER" ]] || die "A tested workflow attempted to call sudo."

report sudo_invocations 0
report result pass
install -m 0644 "$REPORT_TMP" "$REPORT"
assert_user_owned "$REPORT"

printf 'Non-sudo baseline passed.\n'
printf 'Report: %s\n' "$REPORT"
