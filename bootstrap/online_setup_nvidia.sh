#!/usr/bin/env bash
set -euo pipefail

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"

usage()
{
    cat <<EOF
Usage: online_setup_nvidia.sh [setup_nvidia.sh options]

Download a pinned support revision, then run NVIDIA userspace selection or a
pristine-upstream control installation without requiring a local support
repository checkout.

Examples:
  online_setup_nvidia.sh --development 580 --resolve-only
  online_setup_nvidia.sh --development 580
  online_setup_nvidia.sh --use-upstream 580 --resolve-only
  online_setup_nvidia.sh --use-upstream 580 --yes --offer-reboot
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

need()
{
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Missing command: %s\n' "$1" >&2
        exit 1
    }
}

need git
need awk

SUPPORT_REV="$(
    git ls-remote \
        "https://github.com/${SUPPORT_REPO}.git" \
        "refs/heads/${SUPPORT_BRANCH}" |
        awk 'NR == 1 { print $1 }'
)"
[[ "$SUPPORT_REV" =~ ^[0-9a-fA-F]{40}$ ]] || {
    echo "Could not resolve support revision." >&2
    exit 1
}

# setup_nvidia.sh calls other support scripts for pristine-upstream builds, so
# use a pinned temporary checkout instead of downloading individual files.
mkdir -p "${HOME}/.cache/open-gpu-kernel-modules-steamos-support"
TMP="$(mktemp -d "${HOME}/.cache/open-gpu-kernel-modules-steamos-support/online-setup-nvidia.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet --depth 1 \
    "https://github.com/${SUPPORT_REPO}.git" \
    "$TMP/support"
git -C "$TMP/support" fetch --quiet --depth 1 origin "$SUPPORT_REV"
git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"

"$TMP/support/bootstrap/setup_nvidia.sh" "$@"
