#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROGRESS=/run/opemos/interstitial/progress.json
if [[ "${PROJECT_TEST_MODE:-0}" == 1 && -n "${PROJECT_TEST_PROGRESS_STATE:-}" ]]; then
    PROGRESS="$PROJECT_TEST_PROGRESS_STATE"
fi
WRITER="$SUPPORT_ROOT/lib/interstitial_progress.py"

if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    printf 'Usage: %s\n' "${0##*/}"
    printf '%s\n' 'Run automatic exact-kernel repair while publishing fullscreen recovery progress.'
    exit 0
fi
if [[ $# -ne 0 ]]; then
    printf 'Usage: %s\n' "${0##*/}" >&2
    exit 2
fi

# Guardian and delayed repair are distinct attempts that share one visible
# document.  Guardian may leave a terminal result before systemd starts this
# ordered unit, so begin the repair attempt explicitly in every environment.
python3 "$WRITER" reset --state "$PROGRESS" >/dev/null || exit 1

status=0
OPEMOS_RECOVERY_PROGRESS_STATE="$PROGRESS" \
    "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" repair-auto --json || status=$?
if [[ "$status" -ne 0 ]]; then
    # Leave every unsuccessful automatic attempt visibly terminal. The writer
    # refuses to replace a terminal document, which is safe for future phases.
    python3 "$WRITER" fail --state "$PROGRESS" >/dev/null 2>&1 || true
fi
exit "$status"
