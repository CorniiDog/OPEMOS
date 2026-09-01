#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DRY_RUN=0
PUBLISH=0
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; ARGS+=("$1"); shift ;;
        --publish) PUBLISH=1; shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
(( DRY_RUN == 0 || PUBLISH == 0 )) || { echo "repack_artifacts.sh: --dry-run and --publish are exclusive" >&2; exit 1; }
PLAN="$(python3 "$SUPPORT_ROOT/lib/repack_module_artifact.py" "${ARGS[@]}")"
printf '%s\n' "$PLAN"
(( PUBLISH )) || exit 0
OUTPUT_DIR="$(python3 -c 'import sys; a=sys.argv[1:]; print(a[a.index("--output-dir")+1])' "${ARGS[@]}")"
python3 - "$PLAN" "$OUTPUT_DIR" "$SUPPORT_ROOT/bootstrap/publish_artifacts.sh" <<'PY'
import json, os, subprocess, sys
p=json.loads(sys.argv[1]); root=sys.argv[2]; publisher=sys.argv[3]; out=p["output"]
subprocess.run([publisher,"--create-only","--archive",os.path.join(root,out["archive"]),
 "--checksum",os.path.join(root,out["archive"]+".sha256"),"--build-info",os.path.join(root,out["buildInfo"]),
 "--provenance",os.path.join(root,out["provenance"])],check=True)
PY
