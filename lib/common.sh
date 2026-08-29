#!/usr/bin/env bash

set -euo pipefail

PROJECT_NAME="open-gpu-kernel-modules-steamos-support"

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"

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
    need_cmd nvidia-smi
    local version
    version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"
    [[ "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || die "Could not determine NVIDIA driver version."
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
