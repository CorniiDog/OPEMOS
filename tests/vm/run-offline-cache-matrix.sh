#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$SCRIPT_DIR/.runtime/offline-cache-matrix"
mkdir -p "$RUNTIME_DIR"
rm -f "$RUNTIME_DIR"/*.result "$RUNTIME_DIR"/*.log

fedora_pid=""
arch_pid=""
cleanup()
{
    local rc=$?
    for pid in "$fedora_pid" "$arch_pid"; do
        [[ -n "$pid" ]] || continue
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    exit "$rc"
}
trap cleanup INT TERM

("$SCRIPT_DIR/run.sh" --no-image-download --offline-cache-only >"$RUNTIME_DIR/fedora.result" 2>"$RUNTIME_DIR/fedora.log") &
fedora_pid=$!
("$SCRIPT_DIR/run-arch.sh" --no-download --offline-cache-only >"$RUNTIME_DIR/arch.result" 2>"$RUNTIME_DIR/arch.log") &
arch_pid=$!

fedora_status=0
arch_status=0
wait "$fedora_pid" || fedora_status=$?
fedora_pid=""
wait "$arch_pid" || arch_status=$?
arch_pid=""
trap - INT TERM

python3 - "$fedora_status" "$arch_status" \
    "$RUNTIME_DIR/fedora.result" "$RUNTIME_DIR/arch.result" <<'PY'
import json
import sys
from pathlib import Path

statuses = [int(sys.argv[1]), int(sys.argv[2])]
results = []
for path in map(Path, sys.argv[3:]):
    try:
        results.append(json.loads(path.read_text().splitlines()[-1]))
    except (OSError, IndexError, json.JSONDecodeError):
        results.append(None)
passed = all(status == 0 for status in statuses) and all(
    isinstance(result, dict) and result.get("status") == "passed"
    and result.get("offlineAuthenticatedCache") == "passed"
    and result.get("offlineBundleSelection") == "passed"
    for result in results
)
print(json.dumps({"schemaVersion": 1, "status": "passed" if passed else "failed",
                  "artifactDownloads": "disabled", "concurrent": True,
                  "fedora": results[0], "arch": results[1]}, sort_keys=True,
                 separators=(",", ":")))
raise SystemExit(0 if passed else 1)
PY
