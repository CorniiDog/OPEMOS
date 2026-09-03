#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CANONICAL_REPOSITORY="CorniiDog/OPEMOS"
REPOSITORY="$CANONICAL_REPOSITORY"
SUPPORT_COMMIT=""
DRY_RUN=0

usage()
{
    cat <<'EOF'
Usage: publish_installer_bundle.sh --support-commit COMMIT [options]

Options:
  --dry-run                       Validate and emit a publication-plan JSON.
  --development-repository OWNER/REPO
                                  Publish to a noncanonical development repo.
  -h, --help                      Show this help.

Publication is always create-only. Existing releases are never modified.
EOF
}

die() { printf 'publish_installer_bundle.sh: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --support-commit)
            [[ $# -ge 2 ]] || die "$1 requires a commit"
            SUPPORT_COMMIT="$2"
            shift 2
            ;;
        --dry-run) DRY_RUN=1; shift ;;
        --development-repository)
            [[ $# -ge 2 ]] || die "$1 requires OWNER/REPO"
            REPOSITORY="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "$SUPPORT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "a full lowercase support commit is required"
[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid repository"

RESOLVED_COMMIT="$(git -C "$SUPPORT_ROOT" rev-parse --verify "${SUPPORT_COMMIT}^{commit}" 2>/dev/null)" ||
    die "support commit is unavailable"
[[ "$RESOLVED_COMMIT" == "$SUPPORT_COMMIT" ]] || die "support commit did not resolve exactly"

RUNTIME="$(mktemp -d "${TMPDIR:-/tmp}/opemos-bundle-release.XXXXXX")"
cleanup() { rm -rf "$RUNTIME"; }
cancel() { trap - HUP INT TERM; exit 130; }
trap cleanup EXIT
trap cancel HUP INT TERM

TAG="opemos-installer-bundle-${SUPPORT_COMMIT}"
ASSET_NAME="${TAG}.json"
ASSET="$RUNTIME/$ASSET_NAME"
python3 "$SUPPORT_ROOT/lib/installer_bundle_manifest.py" create \
    --root "$SUPPORT_ROOT" --support-commit "$SUPPORT_COMMIT" --output "$ASSET"

PLAN="$(python3 - "$ASSET" "$REPOSITORY" "$TAG" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

asset = Path(sys.argv[1])
repository = sys.argv[2]
tag = sys.argv[3]
document = json.loads(asset.read_text(encoding="utf-8"))
digest = hashlib.sha256(asset.read_bytes()).hexdigest()
short = document["supportCommit"][:7]
notes = (
    "Canonical, immutable OPEMOS installer-consumer bundle.\n\n"
    f"Support commit: [{short}](https://github.com/{repository}/commit/"
    f"{document['supportCommit']})\n"
    f"Bundle ID: `{document['bundleId']}`\n"
    f"Manifest SHA-256: `{digest}`\n"
    f"Files: {len(document['files'])}\n\n"
    "Consumers must independently pin this manifest SHA-256 before trusting "
    "its file inventory."
)
print(json.dumps({
    "schemaVersion": 1,
    "status": "ready",
    "repository": repository,
    "tag": tag,
    "targetCommit": document["supportCommit"],
    "title": f"OPEMOS installer bundle {short}",
    "notes": notes,
    "asset": {
        "name": asset.name,
        "sha256": digest,
        "bundleId": document["bundleId"],
        "files": len(document["files"]),
    },
}, sort_keys=True, separators=(",", ":")))
PY
)" || die "publication plan could not be created"

if (( DRY_RUN )); then
    printf '%s\n' "$PLAN"
    exit 0
fi

command -v gh >/dev/null 2>&1 || die "GitHub CLI is required"
gh auth status --hostname github.com >/dev/null 2>&1 || die "GitHub authentication failed"
CAN_PUSH="$(gh api "repos/$REPOSITORY" --jq '.permissions.push' 2>/dev/null)" ||
    die "GitHub repository permission could not be verified"
[[ "$CAN_PUSH" == true ]] || die "authenticated account cannot publish to $REPOSITORY"
if gh release view "$TAG" --repo "$REPOSITORY" >/dev/null 2>&1; then
    die "release already exists: $TAG"
fi

TITLE="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["title"])' "$PLAN")"
NOTES="$RUNTIME/notes.md"
python3 -c 'import json,sys; print(json.loads(sys.argv[1])["notes"])' "$PLAN" > "$NOTES"
gh release create "$TAG" "$ASSET" --repo "$REPOSITORY" \
    --target "$SUPPORT_COMMIT" --title "$TITLE" --notes-file "$NOTES"
printf 'Published immutable installer bundle %s to %s.\n' "$TAG" "$REPOSITORY"
