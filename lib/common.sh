#!/usr/bin/env bash

set -euo pipefail

PROJECT_NAME="open-gpu-kernel-modules-steamos-support"

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"

NVIDIA_BUILD_IMAGE="${NVIDIA_BUILD_IMAGE:-registry.fedoraproject.org/fedora:42}"

SOURCE_REPO="${SOURCE_REPO:-CorniiDog/open-gpu-kernel-modules-steamos}"
SOURCE_REPO_URL="https://github.com/${SOURCE_REPO}.git"
UPSTREAM_URL="https://github.com/NVIDIA/open-gpu-kernel-modules.git"

DEFAULT_SOURCE_DIR="${HOME}/open-gpu-kernel-modules-steamos"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/${PROJECT_NAME}"
STATE_FILE="${STATE_DIR}/dev-state"

log()
{
    printf '\033[1;34m[%s]\033[0m %s\n' "$PROJECT_NAME" "$*"
}

ok()
{
    printf '\033[1;32m[%s]\033[0m %s\n' "$PROJECT_NAME" "$*"
}

warn()
{
    printf '\033[1;33m[%s]\033[0m %s\n' "$PROJECT_NAME" "$*" >&2
}

die()
{
    printf '\033[1;31m[%s]\033[0m %s\n' "$PROJECT_NAME" "$*" >&2
    exit 1
}

need_cmd()
{
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_steamos()
{
    local os_release
    os_release="$(project_system_path /etc/os-release)"
    [[ -r "$os_release" ]] || die "Cannot read ${os_release}."

    source "$os_release"

    [[ "${ID:-}" == "steamos" || "${NAME:-}" == *"SteamOS"* ]] ||
        die "This operation is intended for SteamOS."
}

get_steamos_version()
{
    require_steamos
    local os_release
    os_release="$(project_system_path /etc/os-release)"
    source "$os_release"
    [[ -n "${VERSION_ID:-}" ]] || die "Could not determine SteamOS VERSION_ID."
    printf '%s\n' "$VERSION_ID"
}

get_kernel_version()
{
    uname -r
}

get_nvidia_version()
{
    local version=""

    if command -v nvidia-smi >/dev/null 2>&1; then
        version="$(
            nvidia-smi \
                --query-gpu=driver_version \
                --format=csv,noheader,nounits \
                2>/dev/null |
            head -n1 |
            tr -d '[:space:]' ||
            true
        )"
    fi

    if [[ ! "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] &&
       command -v pacman >/dev/null 2>&1; then
        version="$(pacman -Q nvidia-utils 2>/dev/null | awk '{print $2}' || true)"
        version="${version%-*}"
    fi

    if [[ ! "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] &&
       [[ -f /var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup/nvidia-version ]]; then
        version="$(tr -d '[:space:]' < /var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup/nvidia-version)"
    fi

    [[ "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] ||
        die "Could not determine NVIDIA userspace driver version."

    printf '%s\n' "$version"
}

source_branch_for_nvidia()
{
    printf 'nvidia/%s\n' "${1:-$(get_nvidia_version)}"
}

sha256_file()
{
    sha256sum "$1" | awk '{print $1}'
}

strings_equal_case_insensitive()
{
    [[ "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" == \
       "$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" ]]
}

canonicalize_path()
{
    local path="$1"

    if realpath -m / >/dev/null 2>&1; then
        realpath -m "$path"
    else
        python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$path"
    fi
}

state_value()
{
    sed -n "s/^${1}=//p" "$STATE_FILE" | head -n1
}

sanitize_release_component()
{
    printf '%s' "$1" | tr '/ :+' '----'
}

kernel_compiler_version_from_definition()
{
    printf '%s\n' "$1" |
        sed -n 's/.*[Gg][Cc][Cc][^0-9]*\([0-9][0-9.]*\).*/\1/p'
}

release_tag()
{
    local steamos kernel nvidia
    steamos="$(get_steamos_version)"
    kernel="$(sanitize_release_component "$(get_kernel_version)")"
    nvidia="$(get_nvidia_version)"
    printf 'steamos-%s-nvidia-%s-k%s\n' "$steamos" "$nvidia" "$kernel"
}

release_asset()
{
    printf 'nvidia-open-%s-x86_64.tar.gz\n' "$(release_tag)"
}

source_branch_matches_expected()
{
    local expected_branch="$1"
    local current_branch="$2"

    if [[ "$expected_branch" == "HEAD" ]]; then
        [[ -z "$current_branch" ]]
    else
        [[ "$current_branch" == "$expected_branch" ]]
    fi
}

validate_nvidia_module_set()
{
    local module module_name seen=""

    # Keep this validator compatible with macOS Bash 3.2. The build paths run
    # on newer Bash, but the repository's non-destructive checks also run on
    # the macOS development host.
    (( $# == 5 )) || return 1

    for module in "$@"; do
        module_name="$(basename "$module")"
        module_name="${module_name%.zst}"

        case "$module_name" in
            nvidia.ko|nvidia-drm.ko|nvidia-modeset.ko|nvidia-peermem.ko|nvidia-uvm.ko) ;;
            *) return 1 ;;
        esac
        case " $seen " in
            *" $module_name "*) return 1 ;;
        esac
        seen="${seen}${seen:+ }${module_name}"
    done

    return 0
}

get_neptune_series()
{
    local kernel="${1:-$(get_kernel_version)}"
    local series

    series="$(printf "%s\n" "$kernel" | sed -n "s/.*-neptune-\([0-9][0-9]*\).*/\1/p")"
    [[ -n "$series" ]] || die "Could not determine Neptune series from kernel: $kernel"
    printf "%s\n" "$series"
}

get_neptune_headers_package()
{
    printf "linux-neptune-%s-headers\n" "$(get_neptune_series "${1:-$(get_kernel_version)}")"
}

valve_repository_names_from_html()
{
    LC_ALL=C grep -oE 'href="jupiter-[A-Za-z0-9._-]+/"' |
        sed -e 's/^href="//' -e 's|/"$||' |
        grep -vxE 'jupiter-(main|ci-test)' |
        awk 'length($0) <= 128' |
        sort -u -rV |
        awk 'NR <= 128'
}

project_cache_root()
{
    printf '%s\n' "${HOME}/.cache/${PROJECT_NAME}"
}

project_mktemp_dir()
{
    local prefix="${1:-tmp}"
    local root
    root="$(project_cache_root)"
    mkdir -p "$root"
    mktemp -d "${root}/${prefix}.XXXXXX"
}

project_mktemp_file()
{
    local prefix="${1:-tmp}"
    local root
    root="$(project_cache_root)"
    mkdir -p "$root"
    mktemp "${root}/${prefix}.XXXXXX"
}

project_system_path()
{
    local path="$1"
    [[ "$path" == /* ]] || die "Internal error: system path must be absolute: $path"

    if [[ "${PROJECT_TEST_MODE:-0}" != "1" ]]; then
        printf '%s\n' "$path"
        return 0
    fi

    local test_root="${PROJECT_TEST_ROOT:-}"
    local temp_root home_root
    [[ -n "$test_root" ]] || die "PROJECT_TEST_MODE requires PROJECT_TEST_ROOT."
    test_root="$(canonicalize_path "$test_root")"
    temp_root="$(canonicalize_path /tmp)"
    home_root="$(canonicalize_path "$HOME")"

    case "$test_root" in
        "$temp_root"/*|"$home_root"/*) ;;
        *) die "Refusing test root outside /tmp or HOME: $test_root" ;;
    esac

    printf '%s%s\n' "$test_root" "$path"
}

acquire_lifecycle_lock()
{
    need_cmd flock

    local lock_file
    lock_file="$(project_system_path "/run/lock/${PROJECT_NAME}.lock")"

    # Preserve one inode so concurrent lifecycle operations cannot lock
    # different files after a pathname replacement.
    sudo mkdir -p "$(dirname "$lock_file")"
    sudo touch "$lock_file"
    sudo chmod 0666 "$lock_file"

    exec 9>"$lock_file"
    flock -n 9 ||
        die "Another ${PROJECT_NAME} install or uninstall is already running."
}
