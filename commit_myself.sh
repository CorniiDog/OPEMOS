#!/usr/bin/env bash

set -euo pipefail

SUPPORT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

YES=0
COMMIT_MESSAGE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            YES=1
            shift
            ;;
        -m|--message)
            [[ $# -ge 2 ]] || die "Missing commit message."
            COMMIT_MESSAGE="$2"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

need git
need nvidia-smi
need sed

[[ -f "$STATE_FILE" ]] || die "Missing development state. Run setup_dev.sh first."

SOURCE_REPO="$(state_value source_repo)"
EXPECTED_NVIDIA="$(state_value installed_nvidia)"
EXPECTED_BRANCH="$(state_value source_branch)"
UPSTREAM_COMMIT="$(state_value upstream_commit)"

[[ -n "$SOURCE_REPO" ]] || die "Invalid state: source_repo missing."
[[ -n "$EXPECTED_NVIDIA" ]] || die "Invalid state: installed_nvidia missing."
[[ -n "$EXPECTED_BRANCH" ]] || die "Invalid state: source_branch missing."
[[ -n "$UPSTREAM_COMMIT" ]] || die "Invalid state: upstream_commit missing."
[[ -d "$SOURCE_REPO/.git" ]] || die "Source repository is missing: ${SOURCE_REPO}"

cd "$SOURCE_REPO"

CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] ||
    die "Current branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."

CURRENT_NVIDIA="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"
[[ "$CURRENT_NVIDIA" == "$EXPECTED_NVIDIA" ]] ||
    die "Installed NVIDIA is ${CURRENT_NVIDIA}; expected ${EXPECTED_NVIDIA}."

SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' version.mk | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$EXPECTED_NVIDIA" ]] ||
    die "version.mk reports ${SOURCE_VERSION}; expected ${EXPECTED_NVIDIA}."

git merge-base --is-ancestor "$UPSTREAM_COMMIT" HEAD ||
    die "Current branch is not descended from the recorded NVIDIA upstream source."

git fetch --quiet --prune origin

if git show-ref --verify --quiet "refs/remotes/origin/${EXPECTED_BRANCH}"; then
    git merge-base --is-ancestor "origin/${EXPECTED_BRANCH}" HEAD ||
        die "origin/${EXPECTED_BRANCH} contains commits not present locally. Update before publishing."
fi

[[ -n "$(git status --porcelain --untracked-files=all)" ]] || die "No changes to commit."

echo
log "Downstream NVIDIA commit"
log "  NVIDIA: ${EXPECTED_NVIDIA}"
log "  Branch: ${EXPECTED_BRANCH}"
echo
git status --short
echo

if [[ -z "$COMMIT_MESSAGE" ]]; then
    if [[ "$YES" == "1" ]]; then
        COMMIT_MESSAGE="SteamOS NVIDIA ${EXPECTED_NVIDIA} updates"
    else
        read -r -p "[${SUPPORT_NAME}] Commit message: " COMMIT_MESSAGE
        [[ -n "$COMMIT_MESSAGE" ]] || die "Commit message cannot be empty."
    fi
fi

if [[ "$YES" != "1" ]]; then
    read -r -p "[${SUPPORT_NAME}] Commit and push ${EXPECTED_BRANCH}? [y/N]: " REPLY
    case "$REPLY" in
        y|Y|yes|YES|Yes) ;;
        *) die "Commit cancelled." ;;
    esac
fi

log "Staging changes..."
git add -A

git diff --cached --quiet && die "No staged changes remain."

echo
git diff --cached --stat
echo

log "Creating commit..."
git commit -m "$COMMIT_MESSAGE"

log "Pushing ${EXPECTED_BRANCH}..."
git push -u origin "$EXPECTED_BRANCH"

echo
log "Published successfully."
log "Branch: ${EXPECTED_BRANCH}"
log "Commit: $(git rev-parse HEAD)"
