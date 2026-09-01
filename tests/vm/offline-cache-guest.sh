#!/usr/bin/env bash
set -euo pipefail
repository_root="${1:?repository root is required}"
status=failed
selection_status=not-run
retention_status=not-run
trap 'rc=$?; printf '\''{"schemaVersion":1,"status":"%s","offlineAuthenticatedCache":"%s","offlineBundleSelection":"%s","offlineCacheRetention":"%s"}\n'\'' "$([[ "$rc" == 0 ]] && printf passed || printf failed)" "$status" "$selection_status" "$retention_status"; exit "$rc"' EXIT
python3 "$repository_root/tests/authenticated_cache_bundle.py"
status=passed
python3 "$repository_root/tests/authenticated_install_bundle.py"
selection_status=passed
python3 "$repository_root/tests/authenticated_cache_retention.py"
retention_status=passed
