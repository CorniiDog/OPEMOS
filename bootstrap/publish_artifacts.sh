#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANONICAL_REPOSITORY="CorniiDog/open-gpu-kernel-modules-steamos-support"
REPOSITORY="$CANONICAL_REPOSITORY"
ARCHIVE=""
CHECKSUM=""
BUILD_INFO=""
PROVENANCE=""
DRY_RUN=0
CREATE_ONLY=0

usage()
{
    cat <<'EOF'
Usage: publish_artifacts.sh --archive FILE --checksum FILE --build-info FILE
                            --provenance FILE [options]

Options:
  --dry-run                       Validate and emit a JSON publication plan.
  --create-only                   Refuse to modify an existing release.
  --development-repository OWNER/REPO
                                  Publish to a noncanonical development repo.
  -h, --help                      Show this help.
EOF
}

die() { printf 'publish_artifacts.sh: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --archive) [[ $# -ge 2 ]] || die "$1 requires a file"; ARCHIVE="$2"; shift 2 ;;
        --checksum) [[ $# -ge 2 ]] || die "$1 requires a file"; CHECKSUM="$2"; shift 2 ;;
        --build-info) [[ $# -ge 2 ]] || die "$1 requires a file"; BUILD_INFO="$2"; shift 2 ;;
        --provenance) [[ $# -ge 2 ]] || die "$1 requires a file"; PROVENANCE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --create-only) CREATE_ONLY=1; shift ;;
        --development-repository) [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"; REPOSITORY="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

for required in ARCHIVE CHECKSUM BUILD_INFO PROVENANCE; do
    [[ -n "${!required}" ]] || die "required argument is missing: $required"
done
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid repository"

PLAN="$(python3 "$SUPPORT_ROOT/lib/validate_publish_inputs.py" \
    --archive "$ARCHIVE" --checksum "$CHECKSUM" --build-info "$BUILD_INFO" \
    --provenance "$PROVENANCE" --repository "$REPOSITORY")"
if (( DRY_RUN )); then
    printf '%s\n' "$PLAN"
    exit 0
fi

command -v gh >/dev/null 2>&1 || die "GitHub CLI is required"
gh auth status --hostname github.com >/dev/null 2>&1 || die "GitHub authentication failed"
CAN_PUSH="$(gh api "repos/$REPOSITORY" --jq '.permissions.push' 2>/dev/null)" ||
    die "GitHub repository permission could not be verified"
[[ "$CAN_PUSH" == true ]] || die "authenticated account cannot publish to $REPOSITORY"

json_value() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$PLAN" "$1"; }
TAG="$(json_value tag)"
TITLE="$(json_value title)"
NOTES_FILE="$(mktemp /tmp/nvidia-release-notes.XXXXXX)"
trap 'rm -f "$NOTES_FILE"' EXIT
python3 -c 'import json,sys; print(json.loads(sys.argv[1])["notes"])' "$PLAN" > "$NOTES_FILE"
TARGET_COMMIT="$(json_value targetCommit)"

if gh release view "$TAG" --repo "$REPOSITORY" >/dev/null 2>&1; then
    (( CREATE_ONLY == 0 )) || die "release already exists: $TAG"
    gh release edit "$TAG" --repo "$REPOSITORY" --title "$TITLE" --notes-file "$NOTES_FILE"
    gh release upload "$TAG" "$ARCHIVE" "$CHECKSUM" "$BUILD_INFO" "$PROVENANCE" \
        --repo "$REPOSITORY" --clobber
else
    gh release create "$TAG" "$ARCHIVE" "$CHECKSUM" "$BUILD_INFO" "$PROVENANCE" \
        --repo "$REPOSITORY" --target "$TARGET_COMMIT" --title "$TITLE" \
        --notes-file "$NOTES_FILE"
fi

printf 'Published %s to %s.\n' "$TAG" "$REPOSITORY"
