#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

usage()
{
    printf 'Usage: %s [--install-podman]\n' "$0"
    printf 'Prepare the rootless Fedora/Podman NVIDIA build environment.\n'
    printf '  --install-podman  Explicitly allow installation through SteamOS pacman.\n'
    printf '  --temporary-podman STEAMOS KERNEL NVIDIA RELEASES OUTPUT [FAILURE_RESULT]\n'
    printf '                    Run one Automatic build with temporary missing dependencies.\n'
}

INSTALL_PODMAN=0
TEMPORARY_PODMAN=0
BUILD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-podman) INSTALL_PODMAN=1; shift ;;
        --temporary-podman) TEMPORARY_PODMAN=1; shift; BUILD_ARGS=("$@"); break ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_steamos

if [[ "$TEMPORARY_PODMAN" == 1 ]]; then
    [[ "$INSTALL_PODMAN" == 0 && ( ${#BUILD_ARGS[@]} == 5 || ${#BUILD_ARGS[@]} == 6 ) ]] ||
        die "Temporary provisioning requires one exact Automatic build."
    if command -v podman >/dev/null 2>&1; then
        exec "$SCRIPT_DIR/build_automatic_for_target.sh" "${BUILD_ARGS[@]}"
    fi
    for command in sudo pacman python3; do need_cmd "$command"; done
    # Use existing SteamOS repositories/keyrings. Do not refresh databases,
    # upgrade installed packages, alter configuration, or remove old packages.
    PROVISION="$(mktemp -d "${TMPDIR:-/tmp}/opemos-podman-provision.XXXXXX")"
    PROVISION_ID="$(python3 - "$PROVISION" <<'PY'
import os, sys
info = os.lstat(sys.argv[1])
print(f'{info.st_dev}:{info.st_ino}')
PY
    )"
    PROVISION_STARTED=0
    PROVISION_RO=0
    PROVISION_CHILD=""
    verify_provision_workspace()
    {
        python3 - "$PROVISION" "$PROVISION_ID" <<'PY'
import os, stat, sys
info = os.lstat(sys.argv[1])
if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or f'{info.st_dev}:{info.st_ino}' != sys.argv[2]):
    raise SystemExit('Provisioning workspace identity changed; preserving it')
PY
    }
    package_delta()
    {
        python3 - "$PROVISION/before" "$PROVISION/plan" "$PROVISION/current" <<'PY'
import re, sys
def load(path):
    data = open(path, 'rb').read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise SystemExit('Package inventory is excessive')
    result = {}
    for line in data.decode('utf-8').splitlines():
        fields = line.split()
        if len(fields) != 2 or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@._+\-]*', fields[0]) is None or fields[0] in result:
            raise SystemExit('Package inventory is malformed')
        result[fields[0]] = fields[1]
    return result
before, plan, current = map(load, sys.argv[1:])
if not plan or 'podman' not in plan or set(plan) & set(before):
    raise SystemExit('Provisioning would affect existing packages')
if any(current.get(name) != version for name, version in before.items()):
    raise SystemExit('Preexisting package state changed; preserving dependencies')
added = {name: version for name, version in current.items() if name not in before}
if any(plan.get(name) != version for name, version in added.items()):
    raise SystemExit('Unowned package state changed; preserving dependencies')
for name in sorted(added):
    print(name)
PY
    }
    cleanup_provision()
    {
        local rc=$? remove=() inventory_ok=1
        if [[ -n "$PROVISION_CHILD" ]]; then
            kill -TERM -- "-$PROVISION_CHILD" 2>/dev/null || true
            for attempt in {1..100}; do
                kill -0 -- "-$PROVISION_CHILD" 2>/dev/null || break
                sleep 0.1
            done
            kill -KILL -- "-$PROVISION_CHILD" 2>/dev/null || true
            wait "$PROVISION_CHILD" 2>/dev/null || true
        fi
        if ! verify_provision_workspace; then
            inventory_ok=0
        elif [[ "$PROVISION_STARTED" == 1 ]]; then
            if pacman -Q > "$PROVISION/current" && package_delta > "$PROVISION/remove"; then
                mapfile -t remove < "$PROVISION/remove"
                if (( ${#remove[@]} )); then
                    sudo pacman -R --noconfirm "${remove[@]}" || inventory_ok=0
                fi
                pacman -Q > "$PROVISION/after" || inventory_ok=0
                python3 - "$PROVISION/before" "$PROVISION/after" <<'PY' || inventory_ok=0
import sys
if sorted(open(sys.argv[1]).read().splitlines()) != sorted(open(sys.argv[2]).read().splitlines()):
    raise SystemExit('Temporary dependencies were not fully retired')
PY
            else
                inventory_ok=0
            fi
        fi
        if [[ "$PROVISION_RO" == 1 ]]; then
            sudo steamos-readonly enable || inventory_ok=0
        fi
        if [[ "$inventory_ok" == 0 ]]; then
            warn "Provisioning cleanup not proven; preserving evidence: $PROVISION"
            return 1
        fi
        verify_provision_workspace || return 1
        rm -rf -- "$PROVISION"
        return "$rc"
    }
    trap cleanup_provision EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    [[ "$SUPPORT_REPO" == CorniiDog/OPEMOS && "$NVIDIA_BUILD_IMAGE" == registry.fedoraproject.org/fedora:42 ]] ||
        die "Temporary provisioning requires the canonical Automatic build policy."
    [[ "$(uname -m)" == x86_64 ]] || die "Automatic builds require x86_64."
    [[ ! -e "${BUILD_ARGS[4]}" && ! -L "${BUILD_ARGS[4]}" ]] || die "Build output already exists."
    [[ ${#BUILD_ARGS[@]} == 5 || ( ! -e "${BUILD_ARGS[5]}" && ! -L "${BUILD_ARGS[5]}" ) ]] ||
        die "Failure output already exists."
    python3 "$SUPPORT_ROOT/lib/automatic_install_selection.py" \
        --steamos "${BUILD_ARGS[0]}" --kernel "${BUILD_ARGS[1]}" \
        --releases "${BUILD_ARGS[3]}" --build-as-fallback > "$PROVISION/action"
    python3 - "$PROVISION/action" "${BUILD_ARGS[2]}" <<'PY'
import json, sys
document = json.load(open(sys.argv[1]))
if (document['status'] != 'authorized' or document['action']['kind'] != 'build_exact_target'
        or document['action']['buildPlan']['target']['nvidiaVersion'] != sys.argv[2]):
    raise SystemExit('No reviewed Automatic build matches installed userspace')
PY
    pacman -Q > "$PROVISION/before"
    pacman -Sp --needed --print-format '%n %v' podman > "$PROVISION/plan"
    cp "$PROVISION/before" "$PROVISION/current"
    package_delta >/dev/null
    if command -v steamos-readonly >/dev/null 2>&1; then
        status="$(steamos-readonly status)"
        case "$status" in
            *enabled*) sudo steamos-readonly disable; PROVISION_RO=1 ;;
            *disabled*) ;;
            *) die "Could not determine SteamOS read-only state." ;;
        esac
    fi
    PROVISION_STARTED=1
    mkdir -m 0700 "$PROVISION/packages"
    sudo pacman -S --needed --noconfirm --cachedir "$PROVISION/packages" podman
    pacman -Q > "$PROVISION/current"
    package_delta >/dev/null
    need_cmd podman
    python3 "$SUPPORT_ROOT/lib/run_in_process_group.py" \
        "$SCRIPT_DIR/build_automatic_for_target.sh" "${BUILD_ARGS[@]}" &
    PROVISION_CHILD=$!
    if wait "$PROVISION_CHILD"; then result=0; else result=$?; fi
    PROVISION_CHILD=""
    exit "$result"
fi

RO_WAS_ENABLED=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 || true
        RO_WAS_ENABLED=0
    fi
}

trap restore_readonly EXIT

if ! command -v podman >/dev/null 2>&1; then
    [[ "$INSTALL_PODMAN" == "1" ]] ||
        die "Podman is required. Review and run: ${SCRIPT_DIR}/setup_build_env.sh --install-podman"
    need_cmd sudo
    log "Installing Podman for NVIDIA development builds..."

    if command -v steamos-readonly >/dev/null 2>&1 &&
       steamos-readonly status 2>/dev/null | grep -qi enabled; then
        sudo steamos-readonly disable
        RO_WAS_ENABLED=1
    fi

    sudo pacman -Sy --needed --noconfirm podman
fi

need_cmd podman
need_cmd realpath

GRAPH_ROOT="$(podman info --format "{{.Store.GraphRoot}}" 2>/dev/null || true)"
[[ -n "$GRAPH_ROOT" ]] || die "Could not determine Podman storage directory."

GRAPH_REAL="$(canonicalize_path "$GRAPH_ROOT")"
HOME_REAL="$(canonicalize_path "$HOME")"

case "$GRAPH_REAL" in
    "$HOME_REAL"/*) ;;
    *)
        die "Refusing development build: Podman storage is outside /home: ${GRAPH_ROOT}"
        ;;
esac

log "Podman storage: ${GRAPH_ROOT}"
log "Preparing Fedora build image: ${NVIDIA_BUILD_IMAGE}"

podman pull "$NVIDIA_BUILD_IMAGE"

restore_readonly
trap - EXIT

ok "Fedora NVIDIA build environment ready."
