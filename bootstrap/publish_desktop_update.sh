#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANONICAL_REPOSITORY="CorniiDog/OPEMOS"
REPOSITORY="$CANONICAL_REPOSITORY"
BINARY=""
MANIFEST=""
SIGNATURE=""
POLICY="$SUPPORT_ROOT/trust/desktop-update-signers.json"
KEYRING="$SUPPORT_ROOT/trust/keyrings/opemos-desktop-updates.gpg"
VERSION=""
DRY_RUN=0
CREATE_ONLY=0
DEVELOPMENT_REPOSITORY=0
TRUST_OVERRIDE=0
ACTIVE_PROCESS_GROUP=""

usage()
{
    cat <<'EOF'
Usage: publish_desktop_update.sh --binary FILE --manifest FILE --signature FILE
                                 --version VERSION [options]

Options:
  --policy FILE                   Development-only signer policy override.
  --keyring FILE                  Development-only public keyring override.
  --dry-run                       Validate and emit a publication plan only.
  --create-only                   Refuse an existing release (required live).
  --development-repository OWNER/REPO
                                  Explicit nonproduction repository override.
  -h, --help                      Show this help.
EOF
}

die() { printf 'publish_desktop_update.sh: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --binary) [[ $# -ge 2 ]] || die "$1 requires a file"; BINARY="$2"; shift 2 ;;
        --manifest) [[ $# -ge 2 ]] || die "$1 requires a file"; MANIFEST="$2"; shift 2 ;;
        --signature) [[ $# -ge 2 ]] || die "$1 requires a file"; SIGNATURE="$2"; shift 2 ;;
        --policy) [[ $# -ge 2 ]] || die "$1 requires a file"; POLICY="$2"; TRUST_OVERRIDE=1; shift 2 ;;
        --keyring) [[ $# -ge 2 ]] || die "$1 requires a file"; KEYRING="$2"; TRUST_OVERRIDE=1; shift 2 ;;
        --version) [[ $# -ge 2 ]] || die "$1 requires a version"; VERSION="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --create-only) CREATE_ONLY=1; shift ;;
        --development-repository)
            [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"
            REPOSITORY="$2"; DEVELOPMENT_REPOSITORY=1; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "$BINARY" && -n "$MANIFEST" && -n "$SIGNATURE" && -n "$VERSION" ]] ||
    die "--binary, --manifest, --signature, and --version are required"
if (( DEVELOPMENT_REPOSITORY )) && [[ "${OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE:-}" != 1 ]]; then
    die "development repository override requires OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE=1"
fi
if (( TRUST_OVERRIDE )) && [[ "${OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE:-}" != 1 ]]; then
    die "trust-anchor overrides require OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE=1"
fi

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/opemos-desktop-publish.XXXXXX")" ||
    die "private publication staging directory could not be created"
cleanup()
{
    chmod -R u+w "$STAGING" 2>/dev/null || true
    rm -rf "$STAGING"
}
terminate_active_process_group()
{
    local attempt
    [[ -n "$ACTIVE_PROCESS_GROUP" ]] || return 0
    kill -TERM -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null ||
        kill -TERM "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null &&
           ! kill -0 "$ACTIVE_PROCESS_GROUP" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    kill -KILL -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null || true
    kill -KILL "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
}
cancel_publish()
{
    terminate_active_process_group
    exit 130
}
run_cancellable()
{
    local rc
    python3 "$SUPPORT_ROOT/lib/run_in_process_group.py" "$@" &
    ACTIVE_PROCESS_GROUP=$!
    set +e
    wait "$ACTIVE_PROCESS_GROUP"
    rc=$?
    set -e
    ACTIVE_PROCESS_GROUP=""
    return "$rc"
}
trap cleanup EXIT
trap cancel_publish HUP INT TERM
snapshot()
{
    python3 "$SUPPORT_ROOT/lib/snapshot_install_input.py" \
        --source "$1" --destination "$2" --max-bytes "$3" >/dev/null
}
snapshot "$BINARY" "$STAGING/opemos-recovery-status" 33554432 ||
    die "desktop executable could not be snapshotted"
snapshot "$MANIFEST" "$STAGING/opemos-desktop-v${VERSION}.manifest.json" 65536 ||
    die "desktop manifest could not be snapshotted"
snapshot "$SIGNATURE" "$STAGING/opemos-desktop-v${VERSION}.manifest.json.sig" 1048576 ||
    die "desktop signature could not be snapshotted"
snapshot "$POLICY" "$STAGING/reviewed-policy.json" 65536 ||
    die "desktop signer policy could not be snapshotted"
snapshot "$KEYRING" "$STAGING/reviewed-keyring.gpg" 16777216 ||
    die "desktop public keyring could not be snapshotted"
BINARY="$STAGING/opemos-recovery-status"
MANIFEST="$STAGING/opemos-desktop-v${VERSION}.manifest.json"
SIGNATURE="$STAGING/opemos-desktop-v${VERSION}.manifest.json.sig"
POLICY="$STAGING/reviewed-policy.json"
KEYRING="$STAGING/reviewed-keyring.gpg"

PLAN_ARGS=(
    plan --binary "$BINARY" --manifest "$MANIFEST" --signature "$SIGNATURE"
    --policy "$POLICY" --keyring "$KEYRING" --version "$VERSION"
    --repository "$REPOSITORY"
)
(( DEVELOPMENT_REPOSITORY == 0 )) || PLAN_ARGS+=(--development-repository)
PLAN="$(python3 "$SUPPORT_ROOT/lib/desktop_update_release.py" "${PLAN_ARGS[@]}")"
if (( DRY_RUN )); then
    printf '%s\n' "$PLAN"
    exit 0
fi
(( CREATE_ONLY )) || die "live publication requires --create-only"

command -v gh >/dev/null 2>&1 || die "GitHub CLI is required"
gh auth status --hostname github.com >/dev/null 2>&1 || die "GitHub authentication failed"
CAN_PUSH="$(gh api "repos/$REPOSITORY" --jq '.permissions.push' 2>/dev/null)" ||
    die "GitHub repository permission could not be verified"
[[ "$CAN_PUSH" == true ]] || die "authenticated account cannot publish to $REPOSITORY"

json_value() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$PLAN" "$1"; }
TAG="$(json_value tag)"
TITLE="$(json_value title)"
TARGET_COMMIT="$(json_value targetCommit)"
REMOTE_COMMIT="$(gh api "repos/$REPOSITORY/commits/$TARGET_COMMIT" --jq '.sha' 2>/dev/null)" ||
    die "support revision is not present in the target repository"
[[ "$REMOTE_COMMIT" == "$TARGET_COMMIT" ]] ||
    die "target repository returned a different support revision"
NOTES_FILE="$STAGING/release-notes.md"
python3 -c 'import json,sys; print(json.loads(sys.argv[1])["notes"], end="")' "$PLAN" > "$NOTES_FILE"

if gh release view "$TAG" --repo "$REPOSITORY" >/dev/null 2>&1; then
    die "release already exists: $TAG"
fi
run_cancellable gh release create "$TAG" "$BINARY" "$MANIFEST" "$SIGNATURE" \
    --repo "$REPOSITORY" --target "$TARGET_COMMIT" --title "$TITLE" \
    --notes-file "$NOTES_FILE"
printf 'Published %s to %s.\n' "$TAG" "$REPOSITORY"
