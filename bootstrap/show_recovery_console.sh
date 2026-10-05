#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == --help ]]; then
    printf '%s\n' 'Usage: show_recovery_console.sh [ERROR_MESSAGE]'
    exit 0
fi

MESSAGE=${1:-OPEMOS recovery needs attention.}
TTY=${OPEMOS_RECOVERY_TTY:-/dev/tty4}

if [[ "${PROJECT_TEST_MODE:-0}" == 1 && "$(id -u)" -ne 0 ]]; then
    [[ -e "$TTY" && ! -L "$TTY" ]] || {
        printf '%s\n' 'Test recovery console target is unavailable.' >&2
        exit 2
    }
elif [[ "$TTY" != /dev/tty[1-9] ]]; then
    printf '%s\n' 'Recovery console TTY is outside the supported set.' >&2
    exit 2
fi

# Release a boot splash before selecting the text VT. Either command may be
# absent on a minimal recovery system, so the visible console remains the
# authoritative fallback.
if command -v plymouth >/dev/null 2>&1; then
    plymouth quit 2>/dev/null || true
fi
if [[ "$TTY" == /dev/tty[1-9] ]] && command -v chvt >/dev/null 2>&1; then
    chvt "${TTY#/dev/tty}" 2>/dev/null || true
fi

{
    printf '\033c'
    printf '%s\n\n' 'OPEMOS RECOVERY NEEDS ATTENTION'
    printf '%s\n\n' "$MESSAGE"
    printf '%s\n' 'The automatic graphics repair did not complete.'
    printf '%s\n' 'Connect to a trusted network, then run:'
    printf '  sudo %q status\n' "$SCRIPT_DIR/recoveryctl.sh"
    printf '  sudo %q repair\n' "$SCRIPT_DIR/recoveryctl.sh"
} >"$TTY"
