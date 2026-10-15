#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

ORIGINAL_RECOVERY_ARGS=("$@")
COMMAND="${1:-status}"
[[ $# -eq 0 ]] || shift
PROFILE=console
ALLOW_NOUVEAU=0
JSON=0
YES=0
ROOT="${PROJECT_TEST_ROOT:-/}"
if [[ "$ROOT" == / && "$EUID" != 0 && "$COMMAND" == status ]]; then
    # No credential prompt or access to private root-owned intent from the UI.
    exec python3 "$SUPPORT_ROOT/lib/recovery_public_status.py" read
fi
if [[ "$ROOT" == / && "$EUID" != 0 ]]; then
    exec sudo /bin/bash "$0" "${ORIGINAL_RECOVERY_ARGS[@]}"
fi
ROOT_COMMAND=(sudo)
if (( EUID == 0 )); then ROOT_COMMAND=(); fi
RECOVERY_OPERATION_LOCK_HELD=0
RECOVERY_LIFECYCLE_LOCK_HELD=0
RO_WAS_ENABLED=0
ACTIVE_PROCESS_GROUP=""

usage()
{
    cat <<'EOF'
Usage: recoveryctl.sh COMMAND [options]

Commands:
  status                         Print installed-system recovery status
  installed-health               Acknowledge the actual new boot module identities
  guard                          Boot-time health check; enter safe console fallback on failure
  enable-fallback                Enable a mutually exclusive recovery profile
  disable-fallback               Disable fallback only after exact NVIDIA verification succeeds
  repair-online                  Re-run the pinned authenticated exact-kernel installer
  repair-auto                    Retry a queued repair without prompting (service use)
  cancel-repair                  Disable automatic retries without changing fallback
  rollback-plan                  Emit bounded A/B rollback coordination requirements

Options:
  --json                         Emit the machine-readable schema-1 result
  --profile PROFILE              console, igpu-desktop, or nouveau-experimental
  --allow-nouveau                Explicitly authorize the experimental Nouveau profile
  -y, --yes                      Noninteractive confirmation
EOF
}

if [[ "$COMMAND" == -h || "$COMMAND" == --help ]]; then
    usage
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json) JSON=1; shift ;;
        --profile) [[ $# -ge 2 ]] || die "--profile requires a value."; PROFILE="$2"; shift 2 ;;
        --allow-nouveau) ALLOW_NOUVEAU=1; shift ;;
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

STATUS_TOOL="$SUPPORT_ROOT/lib/recovery_status.py"
if [[ "${PROJECT_TEST_MODE:-0}" == 1 && -n "${PROJECT_TEST_STATUS_TOOL:-}" ]]; then
    STATUS_TOOL="$PROJECT_TEST_STATUS_TOOL"
fi
POLICY_TOOL="$SUPPORT_ROOT/lib/recovery_policy.py"
POLICY_ROOT="$SUPPORT_ROOT"
CANDIDATE_STAGE=0
if python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" stage-check --root "$ROOT" 2>/dev/null; then
    CANDIDATE_STAGE=1
    POLICY_ROOT=/run/policy
fi
if [[ "${PROJECT_TEST_MODE:-0}" == 1 && -n "${PROJECT_TEST_POLICY_ROOT:-}" ]]; then
    POLICY_ROOT="$PROJECT_TEST_POLICY_ROOT"
fi
FALLBACK_STATE_TOOL="$SUPPORT_ROOT/lib/recovery_fallback_state.py"
STATE_ROOT="$(project_system_path /var/lib/$PROJECT_ID/recovery)"
TRANSACTION="$SUPPORT_ROOT/transaction.json"
RELEASE_PLAN="$SUPPORT_ROOT/release-plan.json"
CACHED_PRODUCT="$SUPPORT_ROOT/cached-repair"
ONLINE_INSTALL="$SUPPORT_ROOT/bootstrap/online_install.sh"
if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
    TRANSACTION="$STATE_ROOT/transaction.json"
    RELEASE_PLAN="$STATE_ROOT/release-plan.json"
    [[ -z "${PROJECT_TEST_CACHED_PRODUCT:-}" ]] || CACHED_PRODUCT="$PROJECT_TEST_CACHED_PRODUCT"
    [[ -z "${PROJECT_TEST_ONLINE_INSTALL:-}" ]] || ONLINE_INSTALL="$PROJECT_TEST_ONLINE_INSTALL"
fi
RECOVERY_CONFIG="$(project_system_path /etc/modprobe.d/98-opemos-recovery.conf)"
NVIDIA_CONFIG="$(project_system_path /etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf)"
NVIDIA_INITRAMFS="$(project_system_path /etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf)"
RECOVERY_PROGRESS_STATE="${OPEMOS_RECOVERY_PROGRESS_STATE:-}"
RECOVERY_PROGRESS_WRITER="$SUPPORT_ROOT/lib/interstitial_progress.py"

publish_recovery_progress()
{
    local operation="$1"
    shift
    [[ -n "$RECOVERY_PROGRESS_STATE" ]] || return 0
    [[ "$RECOVERY_PROGRESS_STATE" == /run/opemos/interstitial/progress.json ||
       ( "${PROJECT_TEST_MODE:-0}" == 1 && "$RECOVERY_PROGRESS_STATE" == /* ) ]] ||
        die "Recovery progress state path is unsupported."
    python3 "$RECOVERY_PROGRESS_WRITER" "$operation" \
        --state "$RECOVERY_PROGRESS_STATE" "$@" >/dev/null
}

status_json()
{
    local base nvidia policy policy_args transaction installed status_code
    if [[ $# -eq 1 ]]; then
        policy="$1"
    else
        policy_args=(--root "$POLICY_ROOT")
        [[ "${PROJECT_TEST_MODE:-0}" != 1 ]] || policy_args+=(--test-owner)
        policy="$(python3 "$POLICY_TOOL" "${policy_args[@]}")"
    fi
    nvidia="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["nvidiaVersion"])' "$policy")"
    status_code=0
    base="$(python3 "$STATUS_TOOL" --root "$ROOT" --kernel "$(get_kernel_version)" \
        --expected-nvidia "$nvidia" --expected-support-revision \
        "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supportRevision"])' "$policy")" \
        --require-payload-receipt)" || status_code=$?
    if [[ "$status_code" != 0 ]]; then
        printf '%s\n' "$base"
        return "$status_code"
    fi
    if [[ "$CANDIDATE_STAGE" == 1 ]]; then
        # The candidate uses real file receipts, never the outer controller's
        # live-root identity, retry journal or lifecycle lock as overlay data.
        printf '%s\n' "$base"
        return 0
    fi
    transaction="$(transaction_tool show)"
    installed='{"state":"not-inspected"}'
    app_state='not-inspected'
    if [[ "$ROOT" == / ]]; then
        installed="$(python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" inspect \
            --root / --policy-root "$POLICY_ROOT")" || return 1
        app_state="$(python3 - "$SUPPORT_ROOT/lib" <<'PYAPPSTATUS'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from install_guardian_generation import registered
from installed_boot_guard import engine
record=registered()['app']
if record is None:print('no-app-intent')
else:
    module,store=engine(record)
    value=module.load_intent(Path('/'),store)
    print(value['phase'])
PYAPPSTATUS
)" || return 1
    fi
    python3 - "$base" "$transaction" "$installed" "$app_state" <<'PYSTATUS'
import json,sys
result=json.loads(sys.argv[1]);result['transaction']=json.loads(sys.argv[2])
result['installedGeneration']=json.loads(sys.argv[3])
if (result['installedGeneration'].get('state') in ('prepared','activating','pending-health','rolling-back')
        or result['installedGeneration'].get('rebootRequired') or sys.argv[4] in ('prepared','activating','pending-health','rolling-back')):
    # Keep schema-1 module/file status values; boot health is an additive reason.
    result['reason']='installed_generation_awaits_boot_health';result['actions']=[]
print(json.dumps(result,sort_keys=True,separators=(',',':')))
PYSTATUS
}

transaction_tool()
{
    python3 "$SUPPORT_ROOT/lib/recovery_transaction.py" "$@" --state "$TRANSACTION"
}

plan_tool()
{
    python3 "$SUPPORT_ROOT/lib/recovery_release_plan.py" "$@" --plan "$RELEASE_PLAN"
}

reconcile_verified_transaction()
{
    local existing installed kernel="$1" nvidia="$2" revision="$3" acknowledged="${4:-0}"
    if [[ "$ROOT" == / ]]; then
    # File-only status/fallback is never a replacement boot acknowledgement.
    [[ "$acknowledged" == 1 ]] || return 0
    installed="$(python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" inspect \
        --root "$ROOT" --policy-root "$POLICY_ROOT")"
    if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("state") in ("prepared","activating","pending-health","rolling-back") else 1)' "$installed"; then
        return 0
    fi
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("state")=="healthy" and d.get("action") in ("update","disable-fallback") and d.get("currentRuntimeMatches") is True else 1)' "$installed" || return 0
    fi
    [[ -f "$TRANSACTION" ]] || return 0
    existing="$(transaction_tool show)"
    case "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["phase"])' "$existing")" in
        cancelled) return 0 ;;
        restored)
            if ! python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d["target"] == {"kernelVersion":sys.argv[2],"nvidiaVersion":sys.argv[3]} and d["supportRevision"] == sys.argv[4] else 1)' \
                "$existing" "$kernel" "$nvidia" "$revision"; then
                plan_tool remove
                transaction_tool remove-terminal
            fi
            return 0
            ;;
    esac
    if ! python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d["target"] == {"kernelVersion":sys.argv[2],"nvidiaVersion":sys.argv[3]} and d["supportRevision"] == sys.argv[4] else 1)' \
        "$existing" "$kernel" "$nvidia" "$revision"; then
        plan_tool remove
        transaction_tool retarget --kernel "$kernel" --nvidia "$nvidia" \
            --support-revision "$revision" >/dev/null
    fi
    transaction_tool reconcile-restored --kernel "$kernel" --nvidia "$nvidia" \
        --support-revision "$revision" >/dev/null
}

acquire_recovery_operation_lock()
{
    [[ "$RECOVERY_OPERATION_LOCK_HELD" == 0 ]] || return 0
    need_cmd flock

    local lock_file
    lock_file="$(project_system_path "/run/lock/${PROJECT_ID}-recovery.lock")"
    if [[ "$ROOT" == / ]]; then
        "${ROOT_COMMAND[@]}" install -d -o root -g root -m 0755 "$(dirname "$lock_file")"
        "${ROOT_COMMAND[@]}" touch "$lock_file"
        "${ROOT_COMMAND[@]}" chown root:root "$lock_file"
        "${ROOT_COMMAND[@]}" chmod 0600 "$lock_file"
    else
        install -d -m 0755 "$(dirname "$lock_file")"
        touch "$lock_file"
        chmod 0600 "$lock_file"
    fi
    exec 8>"$lock_file"
    flock -n 8 || die "Another ${PROJECT_NAME} recovery operation is already running."
    RECOVERY_OPERATION_LOCK_HELD=1
}

acquire_recovery_mutation_locks()
{
    acquire_recovery_operation_lock
    if [[ "$RECOVERY_LIFECYCLE_LOCK_HELD" == 0 ]]; then
        acquire_lifecycle_lock
        RECOVERY_LIFECYCLE_LOCK_HELD=1
    fi
}

emit_result()
{
    local status="$1" reason="$2" action="$3"
    if [[ "$JSON" == 1 ]]; then
        python3 -c 'import json,sys; print(json.dumps({"schemaVersion":1,"status":sys.argv[1],"reason":sys.argv[2],"action":sys.argv[3]},sort_keys=True,separators=(",",":")))' \
            "$status" "$reason" "$action"
    else
        printf '[%s] %s: %s\n' "$PROJECT_NAME" "$status" "$reason"
    fi
}

with_writable_root()
{
    RO_WAS_ENABLED=0
    if [[ "$CANDIDATE_STAGE" != 1 && "$ROOT" == / ]] && command -v steamos-readonly >/dev/null 2>&1 &&
       steamos-readonly status 2>/dev/null | grep -qi enabled; then
        "${ROOT_COMMAND[@]}" steamos-readonly disable
        RO_WAS_ENABLED=1
    fi
}

restore_readonly()
{
    if [[ "${RO_WAS_ENABLED:-0}" == 1 ]]; then
        "${ROOT_COMMAND[@]}" steamos-readonly enable || warn "Could not restore SteamOS read-only mode."
        RO_WAS_ENABLED=0
    fi
}

terminate_active_process_group()
{
    local attempt
    [[ -n "$ACTIVE_PROCESS_GROUP" ]] || return 0
    kill -TERM -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null ||
        kill -TERM "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null &&
           ! kill -0 "$ACTIVE_PROCESS_GROUP" 2>/dev/null; then
            wait "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
            return 0
        fi
        sleep 0.1
    done
    kill -KILL -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null || true
    kill -KILL "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
    wait "$ACTIVE_PROCESS_GROUP" 2>/dev/null || true
}

run_cancellable()
{
    local rc
    python3 "$SUPPORT_ROOT/lib/run_in_process_group.py" "$@" &
    ACTIVE_PROCESS_GROUP=$!
    set +e
    wait "$ACTIVE_PROCESS_GROUP"
    rc=$?
    set -e
    ACTIVE_PROCESS_GROUP=""
    return "$rc"
}

cancel_recovery()
{
    trap - HUP INT TERM
    terminate_active_process_group
    restore_readonly
    exit 130
}

trap restore_readonly EXIT
trap cancel_recovery HUP INT TERM

has_valid_igpu()
{
    local vendor boot drm_class_root=/sys/class/drm
    if [[ "${PROJECT_TEST_MODE:-0}" == 1 ]]; then
        [[ -n "${PROJECT_TEST_DRM_CLASS_ROOT:-}" ]] || return 1
        drm_class_root="$PROJECT_TEST_DRM_CLASS_ROOT"
    fi
    for vendor_file in "$drm_class_root"/card*/device/vendor; do
        [[ -r "$vendor_file" ]] || continue
        vendor="$(tr -d '[:space:]' < "$vendor_file")"
        boot="$(cat "${vendor_file%/vendor}/boot_vga" 2>/dev/null || true)"
        case "$vendor:$boot" in
            0x8086:1|0x1002:1) return 0 ;;
        esac
    done
    return 1
}

confirm()
{
    local prompt="$1" reply
    [[ "$YES" == 1 ]] && return 0
    read -r -p "[$PROJECT_NAME] $prompt [y/N]: " reply
    case "$reply" in y|Y|yes|YES|Yes) return 0 ;; *) die "Cancelled." ;; esac
}

enable_fallback()
{
    case "$PROFILE" in
        console) ;;
        igpu-desktop) has_valid_igpu || die "No boot-VGA Intel or AMD iGPU was validated." ;;
        nouveau-experimental)
            [[ "$ALLOW_NOUVEAU" == 1 ]] || die "Nouveau requires --allow-nouveau and is never selected automatically."
            ;;
        *) die "Unsupported fallback profile: $PROFILE" ;;
    esac
    confirm "Enable the ${PROFILE} recovery profile?"
    if [[ "$ROOT" == / && "$EUID" != 0 ]]; then sudo -v; fi
    acquire_recovery_mutation_locks
    # Fallback/config/initramfs/GRUB writers must join the same durable plan;
    # a retry-phase journal is not a rollback image or boot-safe transaction.
    if [[ "$CANDIDATE_STAGE" != 1 ]]; then
        [[ "$ROOT" == / ]] || die "Fallback requires the owning installed controller."
        python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" update --root / \
            --support "$SUPPORT_ROOT" --policy-root "$POLICY_ROOT" \
            --kernel "$(get_kernel_version)" --nvidia "$(get_nvidia_version)" \
            --action enable-fallback --profile "$PROFILE"
        return
    fi
    python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" require-plan --root "$ROOT"
    with_writable_root
    local install_cmd=(install) move_cmd=(mv)
    if [[ "$ROOT" == / ]]; then install_cmd=("${ROOT_COMMAND[@]}" install); move_cmd=("${ROOT_COMMAND[@]}" mv); fi
    "${install_cmd[@]}" -d -m 0755 "$STATE_ROOT" "$(dirname "$RECOVERY_CONFIG")"
    local temporary
    temporary="$(mktemp)"
    {
        printf '# Managed by OPEMOS recovery; NVIDIA and Nouveau are mutually exclusive.\n'
        printf 'blacklist nvidia\nblacklist nvidia_drm\nblacklist nvidia_modeset\nblacklist nvidia_uvm\nblacklist nvidia_peermem\n'
        if [[ "$PROFILE" == nouveau-experimental ]]; then
            printf 'options nouveau modeset=1\n'
        else
            printf 'blacklist nouveau\noptions nouveau modeset=0\n'
        fi
    } > "$temporary"
    "${install_cmd[@]}" -m 0644 "$temporary" "$RECOVERY_CONFIG"
    rm -f "$temporary"

    # Explicitly forced NVIDIA modules bypass ordinary blacklist behavior, so
    # every fallback profile must preserve and remove that initramfs fragment.
    if [[ -e "$NVIDIA_INITRAMFS" && ! -e "${NVIDIA_INITRAMFS}.opemos-disabled" ]]; then
        "${move_cmd[@]}" "$NVIDIA_INITRAMFS" "${NVIDIA_INITRAMFS}.opemos-disabled"
    fi
    # Nouveau cannot coexist with the normal project blacklist/configuration.
    if [[ "$PROFILE" == nouveau-experimental && -e "$NVIDIA_CONFIG" &&
          ! -e "${NVIDIA_CONFIG}.opemos-disabled" ]]; then
        "${move_cmd[@]}" "$NVIDIA_CONFIG" "${NVIDIA_CONFIG}.opemos-disabled"
    fi
    if [[ "$PROFILE" == nouveau-experimental && "$ROOT" == / && -f /etc/default/grub ]]; then
        [[ -e "$STATE_ROOT/grub.before-fallback" ]] ||
            "${ROOT_COMMAND[@]}" install -o root -g root -m 0600 /etc/default/grub "$STATE_ROOT/grub.before-fallback"
        [[ -e "$STATE_ROOT/grub.cfg.before-fallback" ]] ||
            "${ROOT_COMMAND[@]}" install -o root -g root -m 0600 /boot/grub/grub.cfg "$STATE_ROOT/grub.cfg.before-fallback"
        "${ROOT_COMMAND[@]}" python3 "$SUPPORT_ROOT/lib/update_recovery_grub_args.py" --config /etc/default/grub \
            --boot-config /boot/grub/grub.cfg
    fi
    python3 "$FALLBACK_STATE_TOOL" write \
        --state "$STATE_ROOT/state.json" --profile "$PROFILE"
    if [[ "$ROOT" == / ]]; then
        "${ROOT_COMMAND[@]}" systemctl --root / set-default "$([[ "$PROFILE" == console ]] && echo multi-user.target || echo graphical.target)"
        command -v mkinitcpio >/dev/null 2>&1 && run_cancellable "${ROOT_COMMAND[@]}" mkinitcpio -P
        # Candidate construction never stops the current display manager.
    fi
    restore_readonly
    emit_result fallback-active fallback_enabled "$PROFILE"
}

disable_fallback()
{
    local document
    document="$(status_json)"
    require_verified_fallback "$document"
    confirm "Disable recovery fallback and restore NVIDIA graphical boot?"
    if [[ "$ROOT" == / && "$EUID" != 0 ]]; then sudo -v; fi
    acquire_recovery_mutation_locks
    # The first check avoids prompting for an invalid operation. This second
    # check is authoritative: target/module/receipt state may change before
    # both mutation locks are held.
    document="$(status_json)"
    require_verified_fallback "$document"
    # Fallback/config/initramfs/GRUB writers must join the same durable plan;
    # a retry-phase journal is not a rollback image or boot-safe transaction.
    if [[ "$CANDIDATE_STAGE" != 1 ]]; then
        [[ "$ROOT" == / ]] || die "Fallback requires the owning installed controller."
        python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" update --root / \
            --support "$SUPPORT_ROOT" --policy-root "$POLICY_ROOT" \
            --kernel "$(get_kernel_version)" --nvidia "$(get_nvidia_version)" \
            --action disable-fallback --profile "$PROFILE"
        return
    fi
    python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" require-plan --root "$ROOT"
    with_writable_root
    local remove_cmd=(rm -f) move_cmd=(mv)
    if [[ "$ROOT" == / ]]; then remove_cmd=("${ROOT_COMMAND[@]}" rm -f); move_cmd=("${ROOT_COMMAND[@]}" mv); fi
    "${remove_cmd[@]}" "$RECOVERY_CONFIG"
    for path in "$NVIDIA_CONFIG" "$NVIDIA_INITRAMFS"; do
        [[ ! -f "${path}.opemos-disabled" ]] || "${move_cmd[@]}" "${path}.opemos-disabled" "$path"
    done
    python3 "$FALLBACK_STATE_TOOL" remove --state "$STATE_ROOT/state.json"
    if [[ "$ROOT" == / ]]; then
        if [[ -f "$STATE_ROOT/grub.before-fallback" ]]; then
            "${ROOT_COMMAND[@]}" install -o root -g root -m 0644 "$STATE_ROOT/grub.before-fallback" /etc/default/grub
            "${ROOT_COMMAND[@]}" rm -f "$STATE_ROOT/grub.before-fallback"
            [[ -f "$STATE_ROOT/grub.cfg.before-fallback" ]] || die "Exact prior GRUB configuration is unavailable."
            "${ROOT_COMMAND[@]}" install -o root -g root -m 0644 "$STATE_ROOT/grub.cfg.before-fallback" /boot/grub/grub.cfg
            "${ROOT_COMMAND[@]}" rm -f "$STATE_ROOT/grub.cfg.before-fallback"
        fi
        command -v mkinitcpio >/dev/null 2>&1 && run_cancellable "${ROOT_COMMAND[@]}" mkinitcpio -P
        "${ROOT_COMMAND[@]}" systemctl --root / set-default graphical.target
    fi
    restore_readonly
    emit_result healthy fallback_disabled graphical.target
}

require_verified_fallback()
{
    python3 -c 'import json,sys
d=json.loads(sys.argv[1])
valid=(d.get("status") == "fallback-active"
       and d.get("moduleVerification", {}).get("status") == "verified"
       and d.get("fallback", {}).get("active") is True
       and d.get("fallback", {}).get("profile") in
           ("console", "igpu-desktop", "nouveau-experimental"))
raise SystemExit(0 if valid else 1)' "$1" ||
        die "An active fallback and exact receipt-bound NVIDIA verification are required."
}

guard_enable_fallback()
{
    local document
    acquire_recovery_mutation_locks
    # The pre-lock observation only decides whether the guardian must enter
    # this path. A transient failure must not activate fallback after another
    # serialized operation restores the exact receipt-bound payload.
    if document="$(status_json)" &&
       python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("moduleVerification", {}).get("status") == "verified" else 1)' \
           "$document"; then
        return 0
    fi
    PROFILE=console
    if has_valid_igpu; then
        PROFILE=igpu-desktop
    fi
    YES=1
    enable_fallback
}

case "$COMMAND" in
    status)
        status_code=0
        document="$(status_json)" || status_code=$?
        if [[ "$ROOT" == / && "$CANDIDATE_STAGE" == 0 && "$status_code" == 0 ]]; then
            printf '%s\n' "$document" | python3 "$SUPPORT_ROOT/lib/recovery_public_status.py" publish
        fi
        if [[ "$JSON" == 1 ]]; then printf '%s\n' "$document"; else
            python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("OPEMOS NVIDIA recovery: %s (%s)" % (d["status"],d["reason"]))' "$document"
        fi
        exit "$status_code"
        ;;
    installed-health)
        [[ "$ROOT" == / ]] || die "Installed health requires the actual running guest root."
        acquire_recovery_mutation_locks
        health_result="$(python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" health \
            --root / --policy-root "$POLICY_ROOT")"
        printf '%s\n' "$health_result"
        python3 - "$SUPPORT_ROOT/lib" <<'PYHEALTH'
import sys,json
sys.path.insert(0,sys.argv[1])
from install_guardian_generation import acknowledge_app
print(json.dumps(acknowledge_app(),sort_keys=True))
PYHEALTH
        # App acknowledgement is source/control health only. It is independent
        # of whether a first NVIDIA generation has ever been installed.
        if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("state")=="no-pending-intent" else 1)' "$health_result"; then
            printf '%s\n' '{"state":"app-acknowledged","driverHealth":"not-established","rebootHealthVerified":false}'
            exit 0
        fi
        status_json | python3 "$SUPPORT_ROOT/lib/recovery_public_status.py" publish
        reconcile_verified_transaction "$(get_kernel_version)" "$(get_nvidia_version)" \
            "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supportRevision"])' "$(python3 "$POLICY_TOOL" --root "$POLICY_ROOT")")" 1
        health_status="$(status_json)"
        if [[ -f /run/opemos/interstitial/progress.json ]] &&
           python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("status")=="healthy" and d.get("reason")!="installed_generation_awaits_boot_health" else 1)' "$health_status"; then
            python3 "$RECOVERY_PROGRESS_WRITER" succeed --state /run/opemos/interstitial/progress.json >/dev/null
            systemctl stop opemos-interstitial.service
        fi
        ;;
    guard)
        # Restart reconciliation precedes ordinary status/fallback decisions.
        # Its explicit rebootRequired result must never become a healthy status.
        if [[ "$ROOT" == / ]]; then
            acquire_recovery_mutation_locks
            installed_state="$(python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" startup \
                --root / --policy-root "$POLICY_ROOT")" || exit 1
            if python3 - "$installed_state" "$(get_kernel_version)" <<'PYFIRST'
import json,sys
from pathlib import Path
d=json.loads(sys.argv[1]);directory=Path('/usr/lib/modules')/sys.argv[2]/'updates/open-gpu-kernel-modules-steamos'
fresh=(d.get('state') in ('no-pending-intent','restored') and not Path('/sys/module/nvidia').exists()
       and (not directory.exists() or directory.is_dir() and not any(directory.iterdir())))
raise SystemExit(0 if fresh else 1)
PYFIRST
            then
                emit_result recovery-required first_driver_not_installed authenticated_product_required
                exit 0
            fi
            if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("rebootRequired") else 1)' "$installed_state"; then
                printf '%s\n' "$installed_state"
                exit 0
            fi
        fi
        if ! document="$(status_json)"; then
            guard_enable_fallback
            exit 0
        fi
        if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1])["moduleVerification"]["status"] == "verified" else 1)' "$document"; then
            exit 0
        fi
        guard_enable_fallback
        ;;
    enable-fallback) enable_fallback ;;
    disable-fallback) disable_fallback ;;
    repair-online|repair-auto)
        [[ "$ROOT" == / || "${PROJECT_TEST_MODE:-0}" == 1 ]] ||
            die "Online repair is supported only on the running SteamOS system."
        acquire_recovery_operation_lock
        if [[ "$ROOT" == / ]]; then
            generation="$(python3 "$SUPPORT_ROOT/lib/installed_update_transaction.py" inspect --root / --policy-root "$POLICY_ROOT")"
            if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("state") in ("prepared","activating","pending-health","rolling-back") or json.loads(sys.argv[1]).get("rebootRequired") else 1)' "$generation"; then
                emit_result pending-health reboot_required installed_health
                exit 75
            fi
        fi
        pending_status="$(status_json)"
        if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("reason")=="installed_generation_awaits_boot_health" else 1)' "$pending_status"; then
            emit_result pending-health reboot_required installed_health
            exit 75
        fi
        publish_recovery_progress set --phase inspecting
        policy_args=(--root "$POLICY_ROOT")
        [[ "${PROJECT_TEST_MODE:-0}" != 1 ]] || policy_args+=(--test-owner)
        policy="$(python3 "$POLICY_TOOL" "${policy_args[@]}")"
        revision="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supportRevision"])' "$policy")"
        policy_nvidia="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["nvidiaVersion"])' "$policy")"
        document="$(status_json "$policy")"
        recovery_status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["status"])' "$document")"
        modules_status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["moduleVerification"]["status"])' "$document")"
        kernel="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["target"]["kernelVersion"])' "$document")"
        nvidia="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["target"]["nvidiaVersion"] or "")' "$document")"
        [[ -n "$nvidia" ]] || die "Cannot queue repair without an exact NVIDIA userspace identity."
        [[ "$nvidia" == "$policy_nvidia" ]] || die "Recovery status differs from persistent NVIDIA policy."
        if [[ "$COMMAND" == repair-auto && -f "$TRANSACTION" ]]; then
            existing_retry="$(transaction_tool show)"
            if python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("phase")=="failed" and d.get("reason")=="unchanged_rolled_back_product" and d.get("supportRevision")==sys.argv[2] and d.get("target")=={"kernelVersion":sys.argv[3],"nvidiaVersion":sys.argv[4]} else 1)' \
                "$existing_retry" "$revision" "$kernel" "$nvidia"; then
                emit_result failed unchanged_rolled_back_product corrected_product_required
                exit 76
            fi
        fi
        if [[ "$COMMAND" == repair-auto && -f "$TRANSACTION" ]]; then
            existing="$(transaction_tool show)"
            existing_phase="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["phase"])' "$existing")"
            [[ "$existing_phase" != cancelled ]] || die "Automatic recovery retries were cancelled."
        fi
        if [[ "$recovery_status" == healthy ]]; then
            if [[ "$ROOT" == / ]]; then
                # Reuse the actual acknowledgement consumer, not a file-only
                # shortcut. Drop this operation lock before its normal locks.
                flock -u 8
                exec 8>&-
                exec /bin/bash "$0" installed-health --json
            fi
            reconcile_verified_transaction "$kernel" "$nvidia" "$revision"
            publish_recovery_progress set --phase verifying
            emit_result pending-health actual_boot_health_required installed_health
            exit 0
        fi
        if [[ "$recovery_status" == fallback-active && "$modules_status" == verified ]]; then
            publish_recovery_progress set --phase updating_boot
            YES=1 disable_fallback
            publish_recovery_progress set --phase verifying
            emit_result pending-health reboot_required installed_health
            exit 75
        fi
        if [[ -f "$TRANSACTION" ]]; then
            existing="$(transaction_tool show)"
            existing_phase="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["phase"])' "$existing")"
            existing_active="$(python3 -c 'import json,sys; print("1" if json.loads(sys.argv[1]).get("active") else "0")' "$existing")"
            if [[ "$existing_active" == 0 && "$existing_phase" == restored ]]; then
                plan_tool remove
                transaction_tool remove-terminal
            elif [[ "$existing_phase" == cancelled ]]; then
                [[ "$COMMAND" == repair-online ]] || die "Automatic recovery retries were cancelled."
                confirm "Start a new exact-kernel repair transaction?"
                plan_tool remove
                transaction_tool remove-terminal
            elif ! python3 -c 'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d["target"] == {"kernelVersion":sys.argv[2],"nvidiaVersion":sys.argv[3]} and d["supportRevision"] == sys.argv[4] else 1)' \
                "$existing" "$kernel" "$nvidia" "$revision"; then
                plan_tool remove
                transaction_tool retarget --kernel "$kernel" --nvidia "$nvidia" \
                    --support-revision "$revision" >/dev/null
            fi
        fi
        skip_unchanged_build=0
        if [[ ! -f "$TRANSACTION" ]]; then
            plan_tool remove
            transaction_tool begin --kernel "$kernel" --nvidia "$nvidia" \
                --support-revision "$revision" --phase offline_waiting --reason network_not_verified >/dev/null
        else
            existing="$(transaction_tool show)"
            python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["target"] == {"kernelVersion":sys.argv[2],"nvidiaVersion":sys.argv[3]}; assert d["supportRevision"] == sys.argv[4]; assert d.get("automaticRetry") is not False' \
                "$existing" "$kernel" "$nvidia" "$revision"
            if [[ "$COMMAND" == repair-auto ]] && python3 -c \
                'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d["phase"] == "failed" and d["reason"] == "exact_reviewed_build_incompatible" else 1)' "$existing"; then
                skip_unchanged_build=1
                # Continue cheap publication/cache discovery; a newly verified
                # product can repair this target without repeating compilation.
                transaction_tool set --phase downloading --reason checking_new_exact_product >/dev/null
            fi
        fi
        if [[ "${PROJECT_TEST_MODE:-0}" != 1 || -z "${PROJECT_TEST_CACHED_PRODUCT:-}" ]]; then
            CACHED_PRODUCT="$(python3 "$SUPPORT_ROOT/lib/recovery_cached_product.py" select \
                --home "$SUPPORT_ROOT" --steamos "$(get_steamos_version)" --kernel "$kernel" \
                --nvidia "$nvidia" --support-revision "$revision")" ||
                die "Retained recovery product pointer failed verification."
        fi
        cached_result=""
        if [[ -e "$CACHED_PRODUCT" || -L "$CACHED_PRODUCT" ]]; then
            cached_result="$(python3 "$SUPPORT_ROOT/lib/recovery_cached_product.py" show \
                --directory "$CACHED_PRODUCT" --steamos "$(get_steamos_version)" \
                --kernel "$kernel" --nvidia "$nvidia" --support-revision "$revision")" ||
                die "The staged recovery product is invalid; fallback remains active."
        fi
        if [[ -n "$cached_result" ]]; then
            cached_archive="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["paths"]["archive"])' "$cached_result")"
            cached_checksum="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["paths"]["checksum"])' "$cached_result")"
            transaction_tool set --phase installing --reason canonical_exact_cached_install >/dev/null
            publish_recovery_progress set --phase installing_modules
            install_rc=0
            OPEMOS_PINNED_NVIDIA_VERSION="$nvidia" \
                run_cancellable "$SUPPORT_ROOT/bootstrap/install.sh" \
                    --archive "$cached_archive" --checksum "$cached_checksum" --cache "$CACHED_PRODUCT" -y || install_rc=$?
            if [[ "$install_rc" == 78 ]]; then
                transaction_tool set --phase failed --reason unchanged_rolled_back_product >/dev/null
                emit_result failed unchanged_rolled_back_product corrected_product_required
                exit 76
            elif [[ "$install_rc" != 0 ]]; then
                transaction_tool set --phase retry_scheduled --reason exact_cached_repair_failed >/dev/null
                emit_result retry_scheduled exact_cached_repair_failed timer_and_connectivity
                exit 75
            fi

        else
        publish_recovery_progress set --phase waiting_for_network
        if ! curl -fsS --connect-timeout 10 --max-time 20 https://api.github.com/meta \
            | python3 "$SUPPORT_ROOT/lib/validate_github_meta.py"; then
            if [[ "$skip_unchanged_build" == 1 ]]; then
                transaction_tool set --phase failed --reason exact_reviewed_build_incompatible >/dev/null
                emit_result failed exact_reviewed_build_incompatible unchanged_exact_build_not_repeated
                exit 76
            fi
            transaction_tool set --phase retry_scheduled --reason network_unavailable_or_untrusted >/dev/null
            emit_result offline_waiting network_unavailable retry_scheduled
            exit 75
        fi
        if [[ "$skip_unchanged_build" == 0 ]]; then
            transaction_tool set --phase downloading --reason exact_artifact_resolution >/dev/null
        fi
        publish_recovery_progress set --phase downloading
        transaction_tool set --phase installing --reason canonical_exact_kernel_install >/dev/null
        publish_recovery_progress set --phase installing_modules
        online_rc=0
        SUPPORT_REVISION="$revision" OPEMOS_PINNED_NVIDIA_VERSION="$nvidia" \
             OPEMOS_SKIP_UNCHANGED_BUILD="$skip_unchanged_build" \
             OPEMOS_RECOVERY_PLAN_FILE="$RELEASE_PLAN" \
             run_cancellable "$ONLINE_INSTALL" --build-as-fallback -y || online_rc=$?
        if [[ "$online_rc" == 76 ]]; then
            transaction_tool set --phase failed --reason exact_reviewed_build_incompatible >/dev/null
            emit_result failed exact_reviewed_build_incompatible unchanged_exact_build_not_repeated
            exit 76
        elif [[ "$online_rc" == 78 ]]; then
            transaction_tool set --phase failed --reason unchanged_rolled_back_product >/dev/null
            emit_result failed unchanged_rolled_back_product corrected_product_required
            exit 76
        elif [[ "$online_rc" != 0 && "$skip_unchanged_build" == 1 ]]; then
            transaction_tool set --phase failed --reason exact_reviewed_build_incompatible >/dev/null
            emit_result failed exact_reviewed_build_incompatible unchanged_exact_build_not_repeated
            exit 76
        elif [[ "$online_rc" != 0 ]]; then
            transaction_tool set --phase retry_scheduled --reason exact_repair_failed >/dev/null
            emit_result retry_scheduled exact_repair_failed timer_and_connectivity
            exit 75
        fi
        fi
        transaction_tool set --phase verifying --reason exact_module_verification >/dev/null
        publish_recovery_progress set --phase verifying
        repaired="$(status_json)"
        python3 -c 'import json,sys; assert json.loads(sys.argv[1])["moduleVerification"]["status"] == "verified"' "$repaired" || {
            transaction_tool set --phase failed --reason post_install_verification_failed >/dev/null
            die "Installed repair did not pass exact module verification; fallback remains active."
        }
        reconcile_verified_transaction "$kernel" "$nvidia" "$revision"
        # Fallback restoration, boot/config and receipt were part of the same
        # candidate. Startup health, not a second writer, completes the retry.
        emit_result pending-health reboot_required installed_health
        publish_recovery_progress set --phase verifying
        exit 75
        ;;
    cancel-repair)
        acquire_recovery_operation_lock
        transaction_tool cancel
        plan_tool remove
        ;;
    rollback-plan)
        emit_result coordination-required ab_slot_selection "validate exact disk, slot, kernel, and module set before changing boot selection"
        ;;
    *) usage >&2; exit 2 ;;
esac
