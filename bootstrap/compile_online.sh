#!/usr/bin/env bash

set -euo pipefail

REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
BRANCH="${SUPPORT_BRANCH:-main}"

command -v git >/dev/null 2>&1 || { echo "Missing git." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "Missing curl." >&2; exit 1; }

REV="$(git ls-remote "https://github.com/${REPO}.git" "refs/heads/${BRANCH}" | awk 'NR == 1 {print $1}')"
[[ "$REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve ${REPO}:${BRANCH}." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[open-gpu-kernel-modules-steamos-support] Using support revision ${REV:0:12}..."

git clone --quiet --depth 1 "https://github.com/${REPO}.git" "$TMP/support"
git -C "$TMP/support" fetch --quiet --depth 1 origin "$REV"
git -C "$TMP/support" checkout --quiet --detach "$REV"

chmod +x "$TMP/support/bootstrap/"*.sh "$TMP/support/commit_myself.sh"

"$TMP/support/bootstrap/compile.sh" "$@"
