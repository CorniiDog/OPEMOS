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

# A healthy network/timer retry must not reset progress, acquire DRM, or
# switch VTs. Still run repair-auto so its transaction reconciliation occurs.
document="$("$SUPPORT_ROOT/bootstrap/recoveryctl.sh" status --json)" || exit 1
if python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("status") == "healthy" and d.get("moduleVerification",{}).get("status") == "verified" else 1)' "$document"; then
    "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" repair-auto --json
    exit $?
fi

# Guardian and delayed repair are distinct attempts that share one visible
# document.  Guardian may leave a terminal result before systemd starts this
# ordered unit, so begin the repair attempt explicitly in every environment.
# Stop the preceding attempt before replacing its terminal document. A running
# renderer deliberately rejects terminal replacement within one attempt.
if [[ "$PROGRESS" == /run/opemos/interstitial/progress.json ]]; then
    systemctl stop opemos-interstitial.service
fi
python3 "$WRITER" reset --state "$PROGRESS" >/dev/null || exit 1
if [[ "$PROGRESS" == /run/opemos/interstitial/progress.json ]]; then
    systemctl start opemos-interstitial.service || {
        "$SUPPORT_ROOT/bootstrap/show_recovery_console.sh" \
            'Graphics status is unavailable. Automatic NVIDIA repair is starting.' || true
    }
fi

status=0
OPEMOS_RECOVERY_PROGRESS_STATE="$PROGRESS" \
    "$SUPPORT_ROOT/bootstrap/recoveryctl.sh" repair-auto --json || status=$?
if [[ "$status" -ne 0 ]]; then
    # Leave every unsuccessful automatic attempt visibly terminal. The writer
    # refuses to replace a terminal document, which is safe for future phases.
    python3 "$WRITER" fail --state "$PROGRESS" >/dev/null 2>&1 || true
fi
if [[ "$status" -eq 0 && "$PROGRESS" == /run/opemos/interstitial/progress.json ]]; then
    # Type=simple ordering does not wait for the renderer's success dwell.
    # Stop synchronously before returning control to a healthy desktop.
    systemctl stop opemos-interstitial.service
fi
exit "$status"
