#!/usr/bin/env bash

set -euo pipefail

SUPPORT_REPO="https://github.com/CorniiDog/OPEMOS.git"
RAW_ROOT="https://raw.githubusercontent.com/CorniiDog/OPEMOS"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<EOF
Usage: online_dev.sh

Download the pinned development helper and create or refresh the NVIDIA source
branch matching the currently installed NVIDIA userspace.
EOF
    exit 0
fi

command -v git >/dev/null 2>&1 || { echo "ERROR: git is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl is required." >&2; exit 1; }

REV="$(git ls-remote "$SUPPORT_REPO" refs/heads/main | awk 'NR == 1 { print $1 }')"
[[ -n "$REV" ]] || { echo "ERROR: Could not resolve support repository main branch." >&2; exit 1; }

# common.sh is one of the files this entry point downloads, so bootstrap its
# cache-rooted temporary directory without calling project_mktemp_dir.
mkdir -p "${HOME}/.cache/open-gpu-kernel-modules-steamos-support"
TMP="$(mktemp -d "${HOME}/.cache/open-gpu-kernel-modules-steamos-support/online-dev.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bootstrap" "$TMP/lib"

curl -fsSL "${RAW_ROOT}/${REV}/lib/common.sh" -o "$TMP/lib/common.sh"
curl -fsSL "${RAW_ROOT}/${REV}/bootstrap/setup_dev.sh" -o "$TMP/bootstrap/setup_dev.sh"

chmod +x "$TMP/bootstrap/setup_dev.sh"

exec "$TMP/bootstrap/setup_dev.sh" "$@"
