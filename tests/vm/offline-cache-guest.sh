#!/usr/bin/env bash
set -euo pipefail
repository_root="${1:?repository root is required}"
status=failed
trap 'rc=$?; printf '\''{"schemaVersion":1,"status":"%s","offlineAuthenticatedCache":"%s"}\n'\'' "$([[ "$rc" == 0 ]] && printf passed || printf failed)" "$status"; exit "$rc"' EXIT
python3 "$repository_root/tests/authenticated_cache_bundle.py"
status=passed
