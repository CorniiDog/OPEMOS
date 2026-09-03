#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

COMMAND="${1:-status}"
[[ $# -eq 0 ]] || shift
PROFILE=console
ALLOW_NOUVEAU=0
JSON=0
YES=0
ROOT="${PROJECT_TEST_ROOT:-/}"
RECOVERY_OPERATION_LOCK_HELD=0
RO_WAS_ENABLED=0
ACTIVE_PROCESS_GROUP=""

usage()
{
    cat <<'EOF'
Usage: recoveryctl.sh COMMAND [options]

Commands:
  status                         Print installed-system recovery status
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
POLICY_TOOL="$SUPPORT_ROOT/lib/recovery_policy.py"
POLICY_ROOT="$SUPPORT_ROOT"
if [[ "${PROJECT_TEST_MODE:-0}" == 1 && -n "${PROJECT_TEST_POLICY_ROOT:-}" ]]; then
    POLICY_ROOT="$PROJECT_TEST_POLICY_ROOT"
fi
FALLBACK_STATE_TOOL="$SUPPORT_ROOT/lib/recovery_fallback_state.py"
STATE_ROOT="$(project_system_path /var/lib/$PROJECT_ID/recovery)"
TRANSACTION="$SUPPORT_ROOT/transaction.json"
RELEASE_PLAN="$SUPPORT_ROOT/release-plan.json"
RECOVERY_CONFIG="$(project_system_path /etc/modprobe.d/98-opemos-recovery.conf)"
NVIDIA_CONFIG="$(project_system_path /etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf)"
NVIDIA_INITRAMFS="$(project_system_path /etc/mkinitcpio.conf.d/90-open-gpu-kernel-modules-steamos.conf)"

status_json()
{
    local base nvidia policy policy_args transaction status_code
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
        --expected-nvidia "$nvidia" --require-payload-receipt)" || status_code=$?
    if [[ "$status_code" != 0 ]]; then
        printf '%s\n' "$base"
        return "$status_code"
    fi
    transaction="$(transaction_tool show)"
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d["transaction"]=json.loads(sys.argv[2]); print(json.dumps(d,sort_keys=True,separators=(",",":")))' \
        "$base" "$transaction"
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
    local existing kernel="$1" nvidia="$2" revision="$3"
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
        sudo install -d -o root -g root -m 0755 "$(dirname "$lock_file")"
        sudo touch "$lock_file"
        sudo chown root:root "$lock_file"
        sudo chmod 0600 "$lock_file"
    else
        install -d -m 0755 "$(dirname "$lock_file")"
        touch "$lock_file"
        chmod 0600 "$lock_file"
    fi
    exec 8>"$lock_file"
    flock -n 8 || die "Another ${PROJECT_NAME} recovery operation is already running."
    RECOVERY_OPERATION_LOCK_HELD=1
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
    if [[ "$ROOT" == / ]] && command -v steamos-readonly >/dev/null 2>&1 &&
       steamos-readonly status 2>/dev/null | grep -qi enabled; then
        sudo steamos-readonly disable
        RO_WAS_ENABLED=1
    fi
}

restore_readonly()
{
    if [[ "${RO_WAS_ENABLED:-0}" == 1 ]]; then
        sudo steamos-readonly enable || warn "Could not restore SteamOS read-only mode."
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
    local vendor boot
    for vendor_file in /sys/class/drm/card*/device/vendor; do
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
    [[ "$ROOT" != / ]] || sudo -v
    acquire_recovery_operation_lock
    acquire_lifecycle_lock
    with_writable_root
    local install_cmd=(install) move_cmd=(mv)
    if [[ "$ROOT" == / ]]; then install_cmd=(sudo install); move_cmd=(sudo mv); fi
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
            sudo install -o root -g root -m 0600 /etc/default/grub "$STATE_ROOT/grub.before-fallback"
        sudo python3 "$SUPPORT_ROOT/lib/update_recovery_grub_args.py" --config /etc/default/grub
        command -v update-grub >/dev/null 2>&1 && run_cancellable sudo update-grub
    fi
    python3 "$FALLBACK_STATE_TOOL" write \
        --state "$STATE_ROOT/state.json" --profile "$PROFILE"
    if [[ "$ROOT" == / ]]; then
        sudo systemctl set-default "$([[ "$PROFILE" == console ]] && echo multi-user.target || echo graphical.target)"
        command -v mkinitcpio >/dev/null 2>&1 && run_cancellable sudo mkinitcpio -P
        sudo systemctl stop display-manager.service >/dev/null 2>&1 || true
    fi
    restore_readonly
    emit_result fallback-active fallback_enabled "$PROFILE"
}

disable_fallback()
{
    local document
    document="$(status_json)"
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["moduleVerification"]["status"] == "verified"' "$document" ||
        die "Exact NVIDIA verification has not succeeded; fallback remains enabled."
    confirm "Disable recovery fallback and restore NVIDIA graphical boot?"
    [[ "$ROOT" != / ]] || sudo -v
    acquire_recovery_operation_lock
    acquire_lifecycle_lock
    with_writable_root
    local remove_cmd=(rm -f) move_cmd=(mv)
    if [[ "$ROOT" == / ]]; then remove_cmd=(sudo rm -f); move_cmd=(sudo mv); fi
    "${remove_cmd[@]}" "$RECOVERY_CONFIG"
    for path in "$NVIDIA_CONFIG" "$NVIDIA_INITRAMFS"; do
        [[ ! -f "${path}.opemos-disabled" ]] || "${move_cmd[@]}" "${path}.opemos-disabled" "$path"
    done
    python3 "$FALLBACK_STATE_TOOL" remove --state "$STATE_ROOT/state.json"
    if [[ "$ROOT" == / ]]; then
        if [[ -f "$STATE_ROOT/grub.before-fallback" ]]; then
            sudo install -o root -g root -m 0644 "$STATE_ROOT/grub.before-fallback" /etc/default/grub
            sudo rm -f "$STATE_ROOT/grub.before-fallback"
            command -v update-grub >/dev/null 2>&1 && run_cancellable sudo update-grub
        fi
        command -v mkinitcpio >/dev/null 2>&1 && run_cancellable sudo mkinitcpio -P
        sudo systemctl set-default graphical.target
    fi
    restore_readonly
    emit_result healthy fallback_disabled graphical.target
}

case "$COMMAND" in
    status)
        status_code=0
        document="$(status_json)" || status_code=$?
        if [[ "$JSON" == 1 ]]; then printf '%s\n' "$document"; else
            python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print("OPEMOS NVIDIA recovery: %s (%s)" % (d["status"],d["reason"]))' "$document"
        fi
        exit "$status_code"
        ;;
    guard)
        if ! document="$(status_json)"; then
            PROFILE=console YES=1 enable_fallback
            exit 0
        fi
        if python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1])["moduleVerification"]["status"] == "verified" else 1)' "$document"; then
            exit 0
        fi
        PROFILE=console YES=1 enable_fallback
        ;;
    enable-fallback) enable_fallback ;;
    disable-fallback) disable_fallback ;;
    repair-online|repair-auto)
        [[ "$ROOT" == / ]] || die "Online repair is supported only on the running SteamOS system."
        acquire_recovery_operation_lock
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
            existing="$(transaction_tool show)"
            existing_phase="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["phase"])' "$existing")"
            [[ "$existing_phase" != cancelled ]] || die "Automatic recovery retries were cancelled."
        fi
        if [[ "$recovery_status" == healthy ]]; then
            reconcile_verified_transaction "$kernel" "$nvidia" "$revision"
            emit_result restored exact_nvidia_already_healthy no_action
            exit 0
        fi
        if [[ "$recovery_status" == fallback-active && "$modules_status" == verified ]]; then
            reconcile_verified_transaction "$kernel" "$nvidia" "$revision"
            YES=1 disable_fallback
            emit_result restored exact_nvidia_restored fallback_disabled
            exit 0
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
        if [[ ! -f "$TRANSACTION" ]]; then
            plan_tool remove
            transaction_tool begin --kernel "$kernel" --nvidia "$nvidia" \
                --support-revision "$revision" --phase offline_waiting --reason network_not_verified >/dev/null
        else
            existing="$(transaction_tool show)"
            python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["target"] == {"kernelVersion":sys.argv[2],"nvidiaVersion":sys.argv[3]}; assert d["supportRevision"] == sys.argv[4]; assert d.get("automaticRetry") is not False' \
                "$existing" "$kernel" "$nvidia" "$revision"
        fi
        if ! curl -fsS --connect-timeout 10 --max-time 20 https://api.github.com/meta >/dev/null; then
            transaction_tool set --phase retry_scheduled --reason network_unavailable_or_untrusted >/dev/null
            emit_result offline_waiting network_unavailable retry_scheduled
            exit 75
        fi
        transaction_tool set --phase downloading --reason exact_artifact_resolution >/dev/null
        transaction_tool set --phase installing --reason canonical_exact_kernel_install >/dev/null
        if ! SUPPORT_REVISION="$revision" OPEMOS_PINNED_NVIDIA_VERSION="$nvidia" \
             OPEMOS_RECOVERY_PLAN_FILE="$RELEASE_PLAN" \
             run_cancellable "$SUPPORT_ROOT/bootstrap/online_install.sh" -y; then
            transaction_tool set --phase retry_scheduled --reason exact_repair_failed >/dev/null
            emit_result retry_scheduled exact_repair_failed timer_and_connectivity
            exit 75
        fi
        transaction_tool set --phase verifying --reason exact_module_verification >/dev/null
        repaired="$(status_json)"
        python3 -c 'import json,sys; assert json.loads(sys.argv[1])["moduleVerification"]["status"] == "verified"' "$repaired" || {
            transaction_tool set --phase failed --reason post_install_verification_failed >/dev/null
            die "Installed repair did not pass exact module verification; fallback remains active."
        }
        reconcile_verified_transaction "$kernel" "$nvidia" "$revision"
        YES=1 disable_fallback
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
