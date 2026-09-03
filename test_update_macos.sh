#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DURATION=45
OPEN_BROWSER=1

usage()
{
    printf 'Usage: %s [--duration 5..600] [--no-open]\n' "${0##*/}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --duration)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            DURATION="$2"
            shift 2
            ;;
        --no-open) OPEN_BROWSER=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

[[ "$DURATION" =~ ^[0-9]+$ ]] && (( DURATION >= 5 && DURATION <= 600 )) || {
    printf 'Duration must be an integer between 5 and 600 seconds.\n' >&2
    exit 2
}
command -v python3 >/dev/null 2>&1 || { printf 'python3 is required.\n' >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { printf 'curl is required.\n' >&2; exit 2; }
if [[ "$OPEN_BROWSER" == 1 && "$(uname -s)" != Darwin ]]; then
    printf 'The browser-opening mode is intended for macOS; use --no-open elsewhere.\n' >&2
    exit 2
fi
if [[ "$OPEN_BROWSER" == 1 ]]; then
    command -v open >/dev/null 2>&1 || { printf 'macOS open command is unavailable.\n' >&2; exit 2; }
fi

RUNTIME="$(mktemp -d "${TMPDIR:-/tmp}/opemos-interstitial-demo.XXXXXX")"
SERVER_PID=""
cleanup()
{
    if [[ -n "$SERVER_PID" ]]; then
        kill -TERM "$SERVER_PID" >/dev/null 2>&1 || true
        wait "$SERVER_PID" >/dev/null 2>&1 || true
    fi
    rm -rf "$RUNTIME"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

python3 "$SCRIPT_DIR/tests/interstitial_demo_server.py" \
    --directory "$SCRIPT_DIR/interstitial/demo" \
    --pill "$SCRIPT_DIR/docs/assets/images/opemos-pill.svg" \
    --port-file "$RUNTIME/port" --duration "$DURATION" &
SERVER_PID=$!
deadline=$(( $(date +%s) + 10 ))
while [[ ! -s "$RUNTIME/port" ]]; do
    kill -0 "$SERVER_PID" 2>/dev/null || { printf 'Demo server exited before becoming ready.\n' >&2; exit 1; }
    (( $(date +%s) < deadline )) || { printf 'Demo server did not become ready.\n' >&2; exit 1; }
    sleep 0.1
done
PORT="$(tr -d '[:space:]' < "$RUNTIME/port")"
[[ "$PORT" =~ ^[0-9]+$ ]] || { printf 'Demo server returned an invalid port.\n' >&2; exit 1; }
URL="http://127.0.0.1:$PORT/"
HEALTH="$(curl -fsS --max-time 5 "${URL}health")"
[[ "$HEALTH" == '{"schemaVersion":1,"status":"ready"}' ]] || {
    printf 'Demo health contract failed.\n' >&2
    exit 1
}

if [[ "$OPEN_BROWSER" == 1 ]]; then
    printf 'Opening the OPEMOS no-input update simulation for %s seconds:\n%s\n' "$DURATION" "$URL"
    open "$URL"
    wait "$SERVER_PID"
    SERVER_PID=""
else
    PAGE="$(curl -fsS --max-time 5 "$URL")"
    [[ "$PAGE" == *'CHECKING EXACT NVIDIA SUPPORT'* &&
       "$PAGE" == *'GENERATING INITRAMFS'* &&
       "$PAGE" == *'NVIDIA GRAPHICS READY'* &&
       "$PAGE" == *'id="overall-track"'* &&
       "$PAGE" == *'id="step-track"'* ]] || {
        printf 'Demo page did not contain the required bounded phases.\n' >&2
        exit 1
    }
    PILL="$(curl -fsS --max-time 5 "${URL}opemos-pill.svg")"
    [[ "$PILL" == *'OPEMOS gradient pill'* && "$PILL" == *'#76b900'* ]] || {
        printf 'Demo did not serve the canonical OPEMOS pill.\n' >&2
        exit 1
    }
    printf '%s\n' '{"schemaVersion":1,"status":"passed","macosBrowserSimulation":"passed"}'
fi
