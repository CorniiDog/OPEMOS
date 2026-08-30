#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

usage()
{
    printf 'Usage: %s\n' "$0"
    printf 'Prepare the rootless Fedora/Podman NVIDIA build environment.\n'
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
fi

require_steamos

RO_WAS_ENABLED=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 || true
        RO_WAS_ENABLED=0
    fi
}

trap restore_readonly EXIT

if ! command -v podman >/dev/null 2>&1; then
    need_cmd sudo
    log "Installing Podman for NVIDIA development builds..."

    if command -v steamos-readonly >/dev/null 2>&1 &&
       steamos-readonly status 2>/dev/null | grep -qi enabled; then
        sudo steamos-readonly disable
        RO_WAS_ENABLED=1
    fi

    sudo pacman -Sy --needed --noconfirm podman
fi

need_cmd podman
need_cmd realpath

GRAPH_ROOT="$(podman info --format "{{.Store.GraphRoot}}" 2>/dev/null || true)"
[[ -n "$GRAPH_ROOT" ]] || die "Could not determine Podman storage directory."

GRAPH_REAL="$(canonicalize_path "$GRAPH_ROOT")"
HOME_REAL="$(canonicalize_path "$HOME")"

case "$GRAPH_REAL" in
    "$HOME_REAL"/*) ;;
    *)
        die "Refusing development build: Podman storage is outside /home: ${GRAPH_ROOT}"
        ;;
esac

log "Podman storage: ${GRAPH_ROOT}"
log "Preparing Fedora build image: ${NVIDIA_BUILD_IMAGE}"

podman pull "$NVIDIA_BUILD_IMAGE"

restore_readonly
trap - EXIT

ok "Fedora NVIDIA build environment ready."
