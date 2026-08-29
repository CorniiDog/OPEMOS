#!/usr/bin/env bash

set -euo pipefail

SUPPORT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

need git
need nvidia-smi
need uname
need sed

SOURCE_REPO="${OPEN_GPU_SOURCE_REPO:-${DEFAULT_SOURCE_REPO}}"

[[ -r /etc/os-release ]] || die "Cannot read /etc/os-release."
source /etc/os-release

STEAMOS_VERSION="${VERSION_ID:-}"
KERNEL_VERSION="$(uname -r)"
NVIDIA_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"

[[ -n "$STEAMOS_VERSION" ]] || die "Could not determine SteamOS version."
[[ -n "$KERNEL_VERSION" ]] || die "Could not determine kernel version."
[[ "$NVIDIA_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || die "Invalid NVIDIA driver version: ${NVIDIA_VERSION}"

BRANCH="nvidia/${NVIDIA_VERSION}"

log "SteamOS: ${STEAMOS_VERSION}"
log "Kernel:  ${KERNEL_VERSION}"
log "NVIDIA:  ${NVIDIA_VERSION}"
log "Branch:  ${BRANCH}"
echo

if [[ ! -e "$SOURCE_REPO" ]]; then
    log "Cloning NVIDIA source fork..."
    git clone "$SOURCE_REPO_URL" "$SOURCE_REPO"
elif [[ ! -d "$SOURCE_REPO/.git" ]]; then
    die "${SOURCE_REPO} exists but is not a Git repository."
fi

cd "$SOURCE_REPO"

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    die "Source repository has uncommitted changes. Commit or stash them first."
fi

git remote set-url origin "$SOURCE_REPO_URL"

if git remote get-url upstream >/dev/null 2>&1; then
    git remote set-url upstream "$UPSTREAM_URL"
else
    log "Adding NVIDIA upstream remote..."
    git remote add upstream "$UPSTREAM_URL"
fi

log "Refreshing fork branches..."
git fetch --quiet --prune origin

log "Checking NVIDIA upstream ${NVIDIA_VERSION}..."
UPSTREAM_REF="$(git ls-remote --tags --refs upstream "refs/tags/${NVIDIA_VERSION}" | awk 'NR == 1 { print $2 }')"

[[ "$UPSTREAM_REF" == "refs/tags/${NVIDIA_VERSION}" ]] ||
    die "NVIDIA upstream does not have exact tag ${NVIDIA_VERSION}."

log "Fetching exact NVIDIA upstream source..."
git fetch --quiet upstream "+refs/tags/${NVIDIA_VERSION}:refs/remotes/nvidia-source/${NVIDIA_VERSION}"

UPSTREAM_COMMIT="$(git rev-parse "refs/remotes/nvidia-source/${NVIDIA_VERSION}")"
REMOTE_BRANCH="refs/remotes/origin/${BRANCH}"
LOCAL_BRANCH="refs/heads/${BRANCH}"

if git show-ref --verify --quiet "$LOCAL_BRANCH"; then
    log "Using existing local branch ${BRANCH}."
    git switch "$BRANCH"

    if git show-ref --verify --quiet "$REMOTE_BRANCH"; then
        log "Updating from origin/${BRANCH}..."
        git merge --ff-only "origin/${BRANCH}"
    fi
elif git show-ref --verify --quiet "$REMOTE_BRANCH"; then
    log "Existing downstream branch found on fork."
    git switch -c "$BRANCH" --track "origin/${BRANCH}"
else
    log "No downstream branch exists yet."
    log "Creating ${BRANCH} from NVIDIA ${NVIDIA_VERSION}..."
    git switch -c "$BRANCH" "$UPSTREAM_COMMIT"
fi

git merge-base --is-ancestor "$UPSTREAM_COMMIT" HEAD ||
    die "${BRANCH} is not descended from NVIDIA upstream ${NVIDIA_VERSION}."

mkdir -p "$STATE_DIR"
cat > "$STATE_FILE" <<EOF
source_repo=${SOURCE_REPO}
steamos_version=${STEAMOS_VERSION}
kernel_version=${KERNEL_VERSION}
installed_nvidia=${NVIDIA_VERSION}
source_branch=${BRANCH}
upstream_version=${NVIDIA_VERSION}
upstream_commit=${UPSTREAM_COMMIT}
EOF

echo
log "Development environment ready."
log "Repository: ${SOURCE_REPO}"
log "Branch:     ${BRANCH}"
log "Upstream:   ${NVIDIA_VERSION}"
log "State:      ${STATE_FILE}"
echo
git status --short
