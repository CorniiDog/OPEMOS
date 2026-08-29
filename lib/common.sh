#!/usr/bin/env bash

SUPPORT_NAME="open-gpu-kernel-modules-steamos-support"
SUPPORT_REPO_URL="https://github.com/CorniiDog/open-gpu-kernel-modules-steamos-support.git"
SOURCE_REPO_URL="https://github.com/CorniiDog/open-gpu-kernel-modules-steamos.git"
UPSTREAM_URL="https://github.com/NVIDIA/open-gpu-kernel-modules.git"

DEFAULT_SOURCE_REPO="${HOME}/open-gpu-kernel-modules-steamos"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/${SUPPORT_NAME}"
STATE_FILE="${STATE_DIR}/dev-state"

log()
{
    printf '[%s] %s\n' "$SUPPORT_NAME" "$*"
}

die()
{
    printf '[%s] ERROR: %s\n' "$SUPPORT_NAME" "$*" >&2
    exit 1
}

need()
{
    command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

state_value()
{
    sed -n "s/^${1}=//p" "$STATE_FILE" | head -n1
}
