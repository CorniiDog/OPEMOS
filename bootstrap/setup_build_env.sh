#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

require_steamos
need_cmd sudo

RO_WAS_ENABLED=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable
        RO_WAS_ENABLED=0
    fi
}

trap restore_readonly EXIT

if ! command -v podman >/dev/null 2>&1; then
    log "Podman is not installed; installing build-container runtime..."

    if command -v steamos-readonly >/dev/null 2>&1 &&
       steamos-readonly status 2>/dev/null | grep -qi enabled; then
        sudo steamos-readonly disable
        RO_WAS_ENABLED=1
    fi

    sudo pacman -Sy --needed --noconfirm podman
fi

need_cmd podman

GRAPH_ROOT="$(podman info --format "{{.Store.GraphRoot}}" 2>/dev/null || true)"

[[ -n "$GRAPH_ROOT" ]] ||
    die "Could not determine Podman storage location."

case "$(realpath -m "$GRAPH_ROOT")" in
    "$(realpath -m "$HOME")"/*)
        ;;
    *)
        die "Refusing build environment: Podman storage is not under /home: ${GRAPH_ROOT}"
        ;;
esac

log "Podman storage: ${GRAPH_ROOT}"
log "Preparing Fedora build environment: ${NVIDIA_BUILD_IMAGE}"

podman pull "$NVIDIA_BUILD_IMAGE"

ok "Fedora build environment ready."
