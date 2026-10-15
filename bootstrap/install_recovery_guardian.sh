#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "$SUPPORT_ROOT/lib/common.sh"

INTERSTITIAL_BINARY=""
INTERSTITIAL_SHA256=""
usage()
{
    printf 'Usage: %s [--interstitial-binary FILE --interstitial-sha256 HASH]\n' "$0"
    printf 'Install or refresh the persistent OPEMOS NVIDIA recovery guardian.\n'
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --interstitial-binary) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_BINARY="$2"; shift 2 ;;
        --interstitial-sha256) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; INTERSTITIAL_SHA256="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done
if ! { [[ -z "$INTERSTITIAL_BINARY" && -z "$INTERSTITIAL_SHA256" ]] ||
       [[ -n "$INTERSTITIAL_BINARY" && -n "$INTERSTITIAL_SHA256" ]]; }; then
    die "Interstitial binary and SHA-256 must be supplied together."
fi

require_steamos
need_cmd sudo
sudo -v
acquire_lifecycle_lock

STAGING=""
RO_WAS_ENABLED=0
cleanup()
{
    if [[ "$RO_WAS_ENABLED" == 1 ]]; then sudo steamos-readonly enable >/dev/null 2>&1 || true; fi
    [[ -z "$STAGING" ]] || rm -rf "$STAGING"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/opemos-interstitial.XXXXXX")"
    python3 "$SUPPORT_ROOT/lib/snapshot_install_input.py" \
        --source "$INTERSTITIAL_BINARY" --destination "$STAGING/opemos-interstitial" \
        --max-bytes 33554432
    python3 "$SUPPORT_ROOT/lib/validate_interstitial_binary.py" \
        --binary "$STAGING/opemos-interstitial" --sha256 "$INTERSTITIAL_SHA256" >/dev/null
fi

REVISION="$(git -C "$SUPPORT_ROOT" rev-parse HEAD)"
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || die "Could not bind guardian to an exact support revision."
# The inactive rootfs is replaced by SteamOS atomic updates. Keep the bounded,
# root-owned executable snapshot on the shared home filesystem and migrate only
# its systemd/configuration entry points through Valve's supported /etc keep
# list. A service stored solely in the current /usr would disappear precisely
# when the guardian is needed.
DEST=/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery
# Compose source, policy, unit and entry-point changes together. The retained
# bootstrap is reachable before any mutable guardian import is replaced.
NVIDIA_VERSION="$(get_nvidia_version)"
args=(--source "$SUPPORT_ROOT" --revision "$REVISION" --nvidia "$NVIDIA_VERSION")
if [[ -n "$INTERSTITIAL_BINARY" ]]; then
    args+=(--interstitial "$STAGING/opemos-interstitial" --interstitial-sha256 "$INTERSTITIAL_SHA256")
fi
result="$(sudo python3 "$SUPPORT_ROOT/lib/install_guardian_generation.py" "${args[@]}")"
printf '%s\n' "$result"
sudo systemctl daemon-reload
log "Guardian generation installed; a new boot and health acknowledgement are required."

if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("state")=="pending-health" else 1)' "$result"; then
    log "Guardian requires a new boot and health acknowledgement before driver activation."
    exit 75
fi
