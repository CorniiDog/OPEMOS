#!/usr/bin/env bash
set -euo pipefail

usage()
{
    printf 'Usage: %s\n' "${0##*/}"
    printf '%s\n' 'Resolve and launch the authenticated OPEMOS Desktop Mode companion.'
}

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
    usage
    exit 0
fi
[[ $# -eq 0 ]] || {
    usage >&2
    printf 'Unknown argument: %s\n' "$1" >&2
    exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UPDATE_TOOL="$SUPPORT_ROOT/lib/desktop_update_generations.py"
DATA_HOME="${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}"
UPDATE_STORE="$DATA_HOME/opemos/desktop-updates"

# Development-only trust overrides are accepted by the helper's test harness,
# never by the installed production launcher or its child process.
unset OPEMOS_DEVELOPMENT_TRUST_OVERRIDE OPEMOS_DESKTOP_UPDATE_POLICY \
    OPEMOS_DESKTOP_UPDATE_KEYRING OPEMOS_TEST_ARCHITECTURE OPEMOS_TEST_GPGV \
    OPEMOS_TEST_NOW

[[ -x "$UPDATE_TOOL" ]] || {
    printf '%s\n' 'The installed OPEMOS desktop update manager is unavailable.' >&2
    exit 1
}

resolution="$(/usr/bin/python3 "$UPDATE_TOOL" resolve --store "$UPDATE_STORE")" || {
    printf '%s\n' 'No authenticated healthy OPEMOS desktop generation is available.' >&2
    exit 1
}
[[ ${#resolution} -le 65536 ]] || {
    printf '%s\n' 'The OPEMOS desktop generation response was excessive.' >&2
    exit 1
}
generation="$(/usr/bin/python3 -c '
import json,re,sys
try: value=json.loads(sys.argv[1]); identity=value["generation"]
except (KeyError,TypeError,ValueError): raise SystemExit(1)
if value.get("schemaVersion") != 1 or value.get("status") != "verified" or not re.fullmatch(r"[0-9a-f]{64}", identity): raise SystemExit(1)
print(identity)
' "$resolution")" || {
    printf '%s\n' 'The OPEMOS desktop generation response was invalid.' >&2
    exit 1
}
executable="$UPDATE_STORE/generations/$generation/opemos-recovery-status"
[[ -f "$executable" && ! -L "$executable" && -x "$executable" ]] || {
    printf '%s\n' 'The resolved OPEMOS desktop executable is unavailable.' >&2
    exit 1
}

export OPEMOS_UPDATE_STORE="$UPDATE_STORE"
export OPEMOS_UPDATE_GENERATION="$generation"
exec "$executable"
