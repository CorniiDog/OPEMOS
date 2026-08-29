#!/usr/bin/env bash

set -euo pipefail

SUPPORT_REPO="https://github.com/CorniiDog/open-gpu-kernel-modules-steamos-support.git"
RAW_ROOT="https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support"

command -v git >/dev/null 2>&1 || { echo "ERROR: git is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl is required." >&2; exit 1; }

REV="$(git ls-remote "$SUPPORT_REPO" refs/heads/main | awk 'NR == 1 { print $1 }')"
[[ -n "$REV" ]] || { echo "ERROR: Could not resolve support repository main branch." >&2; exit 1; }

TMP="$(project_mktemp_dir online-dev)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bootstrap" "$TMP/lib"

curl -fsSL "${RAW_ROOT}/${REV}/lib/common.sh" -o "$TMP/lib/common.sh"
curl -fsSL "${RAW_ROOT}/${REV}/bootstrap/setup_dev.sh" -o "$TMP/bootstrap/setup_dev.sh"

chmod +x "$TMP/bootstrap/setup_dev.sh"

exec "$TMP/bootstrap/setup_dev.sh" "$@"
