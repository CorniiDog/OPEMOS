#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BINARY="$SUPPORT_ROOT/bin/opemos-interstitial"
HASH_FILE="$SUPPORT_ROOT/interstitial.sha256"

if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    printf 'Usage: %s\n' "${0##*/}"
    printf '%s\n' 'Verify and launch the installed no-input OPEMOS interstitial.'
    exit 0
fi
[[ $# -eq 0 ]] || { printf 'Usage: %s\n' "${0##*/}" >&2; exit 2; }
[[ -f "$HASH_FILE" && ! -L "$HASH_FILE" ]] || {
    printf 'Installed interstitial hash binding is unavailable.\n' >&2
    exit 1
}
python3 "$SUPPORT_ROOT/lib/validate_recovery_install_path.py" --root / \
    --path "${BINARY#/}" --path "${HASH_FILE#/}"
EXPECTED_SHA256="$(tr -d '[:space:]' < "$HASH_FILE")"
[[ "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
    printf 'Installed interstitial hash binding is malformed.\n' >&2
    exit 1
}
python3 "$SUPPORT_ROOT/lib/validate_interstitial_binary.py" \
    --binary "$BINARY" --sha256 "$EXPECTED_SHA256" >/dev/null
exec "$BINARY" --timeout 300
