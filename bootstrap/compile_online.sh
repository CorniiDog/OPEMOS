#!/usr/bin/env bash

set -euo pipefail

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"
SOURCE_REPO="${SOURCE_REPO:-CorniiDog/open-gpu-kernel-modules-steamos}"

need()
{
    command -v "$1" >/dev/null 2>&1 || { printf 'Missing command: %s\n' "$1" >&2; exit 1; }
}

need git
need curl
need nvidia-smi

NVIDIA_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"
SOURCE_BRANCH="nvidia/${NVIDIA_VERSION}"

printf '[open-gpu-kernel-modules-steamos-support] NVIDIA: %s\n' "$NVIDIA_VERSION"
printf '[open-gpu-kernel-modules-steamos-support] Source branch: %s\n' "$SOURCE_BRANCH"

SUPPORT_REV="$(git ls-remote "https://github.com/${SUPPORT_REPO}.git" "refs/heads/${SUPPORT_BRANCH}" | awk 'NR==1 {print $1}')"
[[ "$SUPPORT_REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve support revision." >&2; exit 1; }

SOURCE_REV="$(git ls-remote "https://github.com/${SOURCE_REPO}.git" "refs/heads/${SOURCE_BRANCH}" | awk 'NR==1 {print $1}')"
[[ "$SOURCE_REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Source branch ${SOURCE_BRANCH} does not exist on ${SOURCE_REPO}." >&2; exit 1; }

printf '[open-gpu-kernel-modules-steamos-support] Support: %s\n' "${SUPPORT_REV:0:12}"
printf '[open-gpu-kernel-modules-steamos-support] Source:  %s\n' "${SOURCE_REV:0:12}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet --depth 1 "https://github.com/${SUPPORT_REPO}.git" "$TMP/support"
git -C "$TMP/support" fetch --quiet --depth 1 origin "$SUPPORT_REV"
git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"

git clone \
    --quiet \
    --depth 1 \
    --branch "$SOURCE_BRANCH" \
    "https://github.com/${SOURCE_REPO}.git" \
    "$TMP/source"

ACTUAL_SOURCE_REV="$(git -C "$TMP/source" rev-parse HEAD)"
[[ "$ACTUAL_SOURCE_REV" == "$SOURCE_REV" ]] || { echo "Source revision changed during checkout." >&2; exit 1; }

UPSTREAM_COMMIT="$(git ls-remote --tags https://github.com/NVIDIA/open-gpu-kernel-modules.git "refs/tags/${NVIDIA_VERSION}^{}" | awk 'NR==1 {print $1}')"

if [[ ! "$UPSTREAM_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]]; then
    UPSTREAM_COMMIT="$(git ls-remote --tags --refs https://github.com/NVIDIA/open-gpu-kernel-modules.git "refs/tags/${NVIDIA_VERSION}" | awk 'NR==1 {print $1}')"
fi

[[ "$UPSTREAM_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve NVIDIA upstream ${NVIDIA_VERSION}." >&2; exit 1; }

STEAMOS_VERSION="$(bash -c 'source /etc/os-release; printf "%s" "$VERSION_ID"')"
KERNEL_VERSION="$(uname -r)"

export XDG_STATE_HOME="$TMP/state"
STATE_DIR="${XDG_STATE_HOME}/open-gpu-kernel-modules-steamos-support"
mkdir -p "$STATE_DIR"

cat > "$STATE_DIR/dev-state" <<EOF
source_repo=$TMP/source
steamos_version=$STEAMOS_VERSION
kernel_version=$KERNEL_VERSION
installed_nvidia=$NVIDIA_VERSION
source_branch=$SOURCE_BRANCH
upstream_version=$NVIDIA_VERSION
upstream_commit=$UPSTREAM_COMMIT
EOF

chmod +x "$TMP/support/bootstrap/"*.sh

"$TMP/support/bootstrap/compile.sh" "$@"
