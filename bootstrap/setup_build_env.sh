#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

need_cmd sudo
need_cmd pacman
need_cmd uname

require_steamos

KERNEL_VERSION="$(get_kernel_version)"
KERNEL_BUILD="/lib/modules/${KERNEL_VERSION}/build"

if [[ -e "$KERNEL_BUILD" ]]; then
    ok "Kernel build environment already exists: ${KERNEL_BUILD}"
    exit 0
fi

NEPTUNE_SERIES="$(printf '%s\n' "$KERNEL_VERSION" | sed -n 's/.*-neptune-\([0-9][0-9]*\).*/\1/p')"
[[ -n "$NEPTUNE_SERIES" ]] || die "Could not determine Neptune kernel series from ${KERNEL_VERSION}."

HEADERS_PACKAGE="linux-neptune-${NEPTUNE_SERIES}-headers"

log "Kernel build environment is missing."
log "Kernel:  ${KERNEL_VERSION}"
log "Headers: ${HEADERS_PACKAGE}"
echo

READONLY_WAS_ENABLED=0

if command -v steamos-readonly >/dev/null 2>&1 && steamos-readonly status 2>/dev/null | grep -qi enabled; then
    log "Disabling SteamOS read-only mode temporarily..."
    sudo steamos-readonly disable
    READONLY_WAS_ENABLED=1
fi

restore_readonly()
{
    if [[ "$READONLY_WAS_ENABLED" == "1" ]]; then
        log "Re-enabling SteamOS read-only mode..."
        sudo steamos-readonly enable || warn "Failed to re-enable SteamOS read-only mode."
    fi
}

trap restore_readonly EXIT

log "Initializing SteamOS package keys if needed..."
sudo pacman-key --init
sudo pacman-key --populate archlinux holo

log "Installing build toolchain and matching Neptune headers..."
sudo pacman -Sy --needed --noconfirm base-devel linux-api-headers "$HEADERS_PACKAGE"

[[ -e "$KERNEL_BUILD" ]] || die "Headers installed, but kernel build directory is still missing: ${KERNEL_BUILD}"

ok "Kernel build environment ready: ${KERNEL_BUILD}"
