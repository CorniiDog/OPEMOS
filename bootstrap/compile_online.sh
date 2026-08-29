#!/usr/bin/env bash

set -euo pipefail

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"
SOURCE_REPO="${SOURCE_REPO:-CorniiDog/open-gpu-kernel-modules-steamos}"

usage()
{
    cat <<EOF
Usage: compile_online.sh [options]

Options:
      --in-code       Compile the current NVIDIA source checkout.
  -o, --output DIR    Write release artifacts to DIR.
      --auto-upload   Upload or update the matching GitHub release.
  -y, --yes           Forward automatic confirmation to compile.sh.
  -h, --help          Show this help.

Other compile.sh options are forwarded unchanged.
EOF
}

need()
{
    command -v "$1" >/dev/null 2>&1 || { printf 'Missing command: %s\n' "$1" >&2; exit 1; }
}

IN_CODE=0
HAS_OUTPUT=0
AUTO_UPLOAD=0
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --in-code)
            IN_CODE=1
            shift
            ;;
        -o|--output)
            [[ $# -ge 2 ]] || { printf "%s requires a directory.\n" "$1" >&2; exit 1; }
            HAS_OUTPUT=1
            FORWARD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --auto-upload)
            AUTO_UPLOAD=1
            FORWARD_ARGS+=("$1")
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            FORWARD_ARGS+=("$1")
            shift
            ;;
    esac
done

need git
need curl
need nvidia-smi

NVIDIA_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"
SOURCE_BRANCH="nvidia/${NVIDIA_VERSION}"

printf '[open-gpu-kernel-modules-steamos-support] NVIDIA: %s\n' "$NVIDIA_VERSION"
printf '[open-gpu-kernel-modules-steamos-support] Source branch: %s\n' "$SOURCE_BRANCH"

SUPPORT_REV="$(git ls-remote "https://github.com/${SUPPORT_REPO}.git" "refs/heads/${SUPPORT_BRANCH}" | awk 'NR==1 {print $1}')"
[[ "$SUPPORT_REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve support revision." >&2; exit 1; }

# common.sh is not available until the support repository is cloned, so this
# bootstrap entry point must create its cache-rooted temporary directory itself.
mkdir -p "${HOME}/.cache/open-gpu-kernel-modules-steamos-support"
TMP="$(mktemp -d "${HOME}/.cache/open-gpu-kernel-modules-steamos-support/compile-online.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet --depth 1 "https://github.com/${SUPPORT_REPO}.git" "$TMP/support"
git -C "$TMP/support" fetch --quiet --depth 1 origin "$SUPPORT_REV"
git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"

if [[ "$IN_CODE" == "1" ]]; then
    [[ "$AUTO_UPLOAD" == "0" ]] || {
        echo "--in-code cannot be combined with --auto-upload." >&2
        exit 1
    }

    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
        echo "--in-code must be run inside the NVIDIA source Git repository." >&2
        exit 1
    }

    SOURCE_DIR="$(git rev-parse --show-toplevel)"
    CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"

    [[ "$CURRENT_BRANCH" == "$SOURCE_BRANCH" ]] || {
        printf "Current branch is %s; expected %s for installed NVIDIA %s.\n" \
            "${CURRENT_BRANCH:-DETACHED}" "$SOURCE_BRANCH" "$NVIDIA_VERSION" >&2
        exit 1
    }

    [[ -f "$SOURCE_DIR/version.mk" && -f "$SOURCE_DIR/Makefile" && -d "$SOURCE_DIR/kernel-open" ]] || {
        echo "Current repository does not look like the NVIDIA open kernel-module source tree." >&2
        exit 1
    }

    SOURCE_VERSION="$(sed -n "s/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p" "$SOURCE_DIR/version.mk" | head -n1 | tr -d "[:space:]")"

    [[ "$SOURCE_VERSION" == "$NVIDIA_VERSION" ]] || {
        printf "version.mk reports NVIDIA %s; installed NVIDIA is %s.\n" \
            "${SOURCE_VERSION:-unknown}" "$NVIDIA_VERSION" >&2
        exit 1
    }

    SOURCE_REV="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
    SOURCE_SUFFIX=""
    [[ -z "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || SOURCE_SUFFIX=" + working tree changes"

    printf "[open-gpu-kernel-modules-steamos-support] Support: %s\n" "${SUPPORT_REV:0:12}"
    printf "[open-gpu-kernel-modules-steamos-support] Source:  %s%s\n" "${SOURCE_REV:0:12}" "$SOURCE_SUFFIX"
    printf "[open-gpu-kernel-modules-steamos-support] Mode:    in-code (%s)\n" "$SOURCE_DIR"

    FORWARD_ARGS+=("--force-rebuild")

    if [[ "$HAS_OUTPUT" == "0" ]]; then
        FORWARD_ARGS+=("-o" "$HOME/releases")
    fi
else
    SOURCE_REV="$(git ls-remote "https://github.com/${SOURCE_REPO}.git" "refs/heads/${SOURCE_BRANCH}" | awk 'NR==1 {print $1}')"

    [[ "$SOURCE_REV" =~ ^[0-9a-fA-F]{40}$ ]] || {
        echo "Source branch ${SOURCE_BRANCH} does not exist on ${SOURCE_REPO}." >&2
        exit 1
    }

    printf "[open-gpu-kernel-modules-steamos-support] Support: %s\n" "${SUPPORT_REV:0:12}"
    printf "[open-gpu-kernel-modules-steamos-support] Source:  %s\n" "${SOURCE_REV:0:12}"

    git clone \
        --quiet \
        --depth 1 \
        --branch "$SOURCE_BRANCH" \
        "https://github.com/${SOURCE_REPO}.git" \
        "$TMP/source"

    ACTUAL_SOURCE_REV="$(git -C "$TMP/source" rev-parse HEAD)"

    [[ "$ACTUAL_SOURCE_REV" == "$SOURCE_REV" ]] || {
        echo "Source revision changed during checkout." >&2
        exit 1
    }

    SOURCE_DIR="$TMP/source"
fi

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
source_repo=$SOURCE_DIR
steamos_version=$STEAMOS_VERSION
kernel_version=$KERNEL_VERSION
installed_nvidia=$NVIDIA_VERSION
source_branch=$SOURCE_BRANCH
upstream_version=$NVIDIA_VERSION
upstream_commit=$UPSTREAM_COMMIT
EOF

chmod +x "$TMP/support/bootstrap/"*.sh

"$TMP/support/bootstrap/compile.sh" "${FORWARD_ARGS[@]}"
