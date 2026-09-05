#!/usr/bin/env bash
set -euo pipefail
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TEST_DIR/.." && pwd)"
source "$PROJECT_ROOT/lib/common.sh"
WORK="$(mktemp -d /tmp/open-gpu-module-hash.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT INT TERM
printf 'canonical module payload\n' > "$WORK/nvidia.ko"
zstd -q "$WORK/nvidia.ko" -o "$WORK/nvidia.ko.zst"
[[ "$(module_content_sha256 "$WORK/nvidia.ko")" == \
   "$(module_content_sha256 "$WORK/nvidia.ko.zst")" ]] ||
    die "Raw and compressed module content hashes differ."
printf 'not zstd data\n' > "$WORK/corrupt.ko.zst"
if module_content_sha256 "$WORK/corrupt.ko.zst" >/dev/null 2>&1; then
    die "Corrupt compressed module produced a content hash."
fi
if module_content_sha256 "$WORK/nvidia.txt" >/dev/null 2>&1; then
    die "Unsupported module suffix produced a content hash."
fi
printf 'Module content hash tests passed.\n'
