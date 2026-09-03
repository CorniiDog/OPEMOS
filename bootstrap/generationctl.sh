#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SUPPORT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"

exec python3 "$SUPPORT_ROOT/lib/device_generation_lifecycle.py" "$@"
