#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROGRESS_ROOT=/run/opemos/interstitial
PROGRESS="$PROGRESS_ROOT/progress.json"
WRITER="$SUPPORT_ROOT/lib/interstitial_progress.py"
RECOVERYCTL="$SUPPORT_ROOT/bootstrap/recoveryctl.sh"

if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    printf 'Usage: %s\n' "${0##*/}"
    printf '%s\n' 'Run the installed boot guardian while publishing bounded interstitial progress.'
    exit 0
fi
if [[ $# -ne 0 ]]; then
    printf 'Usage: %s\n' "${0##*/}" >&2
    exit 2
fi

install -d -o root -g root -m 0755 "$PROGRESS_ROOT"
python3 "$WRITER" reset --state "$PROGRESS" >/dev/null
python3 "$WRITER" set --state "$PROGRESS" --phase inspecting >/dev/null
FINALIZED=0

finalize_failure()
{
    python3 "$WRITER" fail --state "$PROGRESS" >/dev/null 2>&1 || true
    FINALIZED=1
}
trap '[[ "$FINALIZED" == 1 ]] || finalize_failure' EXIT
trap 'finalize_failure; exit 130' INT
trap 'finalize_failure; exit 143' TERM

guardian_status=0
"$RECOVERYCTL" guard --json || guardian_status=$?
document="$("$RECOVERYCTL" status --json)" || {
    finalize_failure
    exit "${guardian_status:-1}"
}
if python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("moduleVerification",{}).get("status") == "verified" else 1)' "$document"
then
    python3 "$WRITER" succeed --state "$PROGRESS" >/dev/null
    FINALIZED=1
else
    finalize_failure
fi

# A safe fallback is a successful guardian outcome even when NVIDIA remains
# unavailable. The visual status is terminal and the renderer releases DRM.
exit "$guardian_status"
