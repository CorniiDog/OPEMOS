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
    [[ -r /etc/os-release ]] || die "Cannot read /etc/os-release."

    source /etc/os-release

    [[ "${ID:-}" == "steamos" || "${NAME:-}" == *"SteamOS"* ]] ||
        die "This operation is intended for SteamOS."
}

get_steamos_version()
{
    require_steamos
    source /etc/os-release
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

state_value()
{
    sed -n "s/^${1}=//p" "$STATE_FILE" | head -n1
}

sanitize_release_component()
{
    printf '%s' "$1" | tr '/ :+' '----'
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

acquire_lifecycle_lock()
{
    need_cmd flock

    local lock_file="/run/lock/${PROJECT_NAME}.lock"

    # Preserve one inode so concurrent lifecycle operations cannot lock
    # different files after a pathname replacement.
    sudo touch "$lock_file"
    sudo chmod 0666 "$lock_file"

    exec 9>"$lock_file"
    flock -n 9 ||
        die "Another ${PROJECT_NAME} install or uninstall is already running."
}
