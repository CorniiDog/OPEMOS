#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

fail()
{
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

printf 'Checking shell syntax...\n'
python3 tests/shell_nounset.py
python3 tests/host_temp_storage.py
python3 tests/setup_nvidia_prerequisites.py
python3 tests/setup_nvidia_modes.py
python3 tests/boundary_policy.py
for script_file in bootstrap/*.sh lib/*.sh commit_myself.sh test_update_macos.sh tests/*.sh; do
    bash -n "$script_file"
done
bash -n tests/vm/run-steamos-recovery.sh tests/vm/inspect-steamos-recovery.sh \
    tests/vm/steamos-recovery-fixture.sh tests/vm/run-offline-cache-matrix.sh \
    tests/vm/offline-cache-guest.sh tests/vm/desktop-gui-guest.sh \
    tests/fixtures/recoveryctl-healthy.sh
bash -n tests/fixtures/no-sudo/bin/sudo
for mock_file in tests/fixtures/transaction/bin/*; do
    bash -n "$mock_file"
done
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/select_release.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/resolve_target.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/write_build_result.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    bootstrap/prepare_valve_keyring.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/run_in_process_group.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_target_headers.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_built_modules.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/write_build_provenance.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_valve_signer.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_install_inputs.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/write_install_result.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/payload_receipt.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/recovery_status.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/recovery_policy.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/update_recovery_grub_args.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/recovery_transaction.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/recovery_release_plan.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/snapshot_install_input.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/prune_backup_generations.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/prune_build_sessions.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/authenticated_cache_bundle.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/resolve_authenticated_install_bundle.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/prune_authenticated_cache.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/desktop_update_generations.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/desktop_update_release.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/device_generation_lifecycle.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/device_generation_transport_watchdog.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/device_generation_contract.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_device_generation_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_openpgp_status_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/userspace_lock_bootstrap_contract.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_userspace_lock_bootstrap_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/userspace_lock_request_plan.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_userspace_lock_request_plan_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/userspace_lock_verifier_evidence.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_userspace_lock_verifier_evidence_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/consume_appliance_generation.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_development_appliance_generation.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/source_intent_contract.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/generate_source_intent_fixtures.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/interstitial_progress.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_interstitial_binary.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/installer_bundle_manifest.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    tests/interstitial_demo_server.py
printf 'Checking crash-safe desktop update generations...\n'
python3 tests/desktop_update_generations.py
printf 'Checking canonical desktop update publisher...\n'
python3 tests/desktop_update_publisher.py
printf 'Checking no-input boot interstitial...\n'
python3 tests/interstitial.py
cargo test --locked --manifest-path interstitial/Cargo.toml
cargo clippy --locked --manifest-path interstitial/Cargo.toml --all-targets -- -D warnings
cargo fmt --manifest-path interstitial/Cargo.toml -- --check
printf 'Checking canonical progress semantics...\n'
python3 tests/progress_semantics.py
printf 'Checking canonical cross-frontend contracts...\n'
python3 tests/consumer_contracts.py
printf 'Checking reviewed userspace-lock generation contracts...\n'
python3 tests/userspace_lock_generation_contract.py
printf 'Checking OpenPGP status compatibility contracts...\n'
python3 tests/openpgp_status_contract.py
printf 'Checking userspace-lock bootstrap compatibility contracts...\n'
python3 tests/userspace_lock_bootstrap_contract.py
printf 'Checking immutable userspace-lock request planning...\n'
python3 tests/userspace_lock_request_plan.py
printf 'Checking snapshot-bound verifier evidence...\n'
python3 tests/userspace_lock_verifier_evidence.py
printf 'Checking development appliance generation consumption...\n'
python3 tests/appliance_generation_consumer.py
printf 'Checking explicit source-intent authorization...\n'
python3 tests/source_intent_contract.py
printf 'Checking inactive installed-device generation lifecycle...\n'
python3 tests/device_generation_lifecycle.py
printf 'Checking installed-device transport containment...\n'
python3 tests/device_generation_transport_watchdog.py
printf 'Checking installed-device generation compatibility contracts...\n'
python3 tests/device_generation_contract.py
printf 'Checking immutable installer-bundle publisher...\n'
python3 tests/installer_bundle_publisher.py

printf 'Checking immutable installer input snapshots...\n'
SNAPSHOT_FIXTURE="$(mktemp -d /tmp/installer-input-snapshot.XXXXXX)"
printf 'authenticated fixture\n' > "$SNAPSHOT_FIXTURE/source"
python3 lib/snapshot_install_input.py \
    --source "$SNAPSHOT_FIXTURE/source" --destination "$SNAPSHOT_FIXTURE/copy" \
    --max-bytes 1024
cmp "$SNAPSHOT_FIXTURE/source" "$SNAPSHOT_FIXTURE/copy" || \
    fail "installer input snapshot content differs"
python3 - "$SNAPSHOT_FIXTURE/copy" <<'PY' || fail "installer input snapshot mode is unsafe"
import os, stat, sys
assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o600
PY
ln -s "$SNAPSHOT_FIXTURE/source" "$SNAPSHOT_FIXTURE/link"
if python3 lib/snapshot_install_input.py \
    --source "$SNAPSHOT_FIXTURE/link" --destination "$SNAPSHOT_FIXTURE/link-copy" \
    --max-bytes 1024 >/dev/null 2>&1
then
    fail "installer input snapshot accepted a symlink source"
fi
[[ ! -e "$SNAPSHOT_FIXTURE/link-copy" ]] || \
    fail "failed installer input snapshot left partial output"
if python3 lib/snapshot_install_input.py \
    --source "$SNAPSHOT_FIXTURE/source" --destination "$SNAPSHOT_FIXTURE/oversize" \
    --max-bytes 4 >/dev/null 2>&1
then
    fail "installer input snapshot exceeded its byte limit"
fi
[[ ! -e "$SNAPSHOT_FIXTURE/oversize" ]] || \
    fail "oversized installer input snapshot left partial output"
python3 tests/install_input_snapshot.py
rm -rf "$SNAPSHOT_FIXTURE"
printf 'Checking bounded backup retention...\n'
python3 tests/backup_retention.py
printf 'Checking abandoned build-session cleanup...\n'
python3 tests/build_session_retention.py
printf 'Checking authenticated offline cache transfer...\n'
python3 tests/authenticated_cache_bundle.py
python3 tests/authenticated_install_bundle.py
python3 tests/authenticated_cache_retention.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/verify_installed_userspace.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/verify_installed_modules.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    bootstrap/prepare_nvidia_package_keyring.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/validate_publish_inputs.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/gaming_payload_profiles.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/repack_gaming_userspace.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/repack_module_artifact.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/bsdtar_safety.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/atomic_output.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/prepare_pacman_config.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/write_compile_provenance.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/update_grub_nvidia_args.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/check_initramfs_workspace.py
python3 -c 'compile(open(__import__("sys").argv[1], encoding="utf-8").read(), __import__("sys").argv[1], "exec")' \
    lib/run_pacman_transaction.py

printf 'Checking exact target-header validation...\n'
python3 tests/header_validation.py

printf 'Checking offline-target build failure cleanup...\n'
python3 tests/build_for_target_failures.py

printf 'Checking headless VM harness contract...\n'
python3 tests/vm_harness.py

printf 'Checking GitHub Pages documentation contract...\n'
python3 tests/documentation.py

printf 'Checking bind-mount source topology...\n'
python3 tests/bind_mount.py

printf 'Checking installer consumer compatibility...\n'
python3 tests/install_contract.py
printf 'Checking rootfs payload receipt contract...\n'
python3 tests/payload_receipt.py
printf 'Checking installed-system recovery contract...\n'
python3 tests/recovery_status.py
python3 tests/recovery_state_stress.py

printf 'Checking target-owned execution trust...\n'
python3 tests/target_execution_trust.py

printf 'Checking initramfs workspace admission...\n'
python3 tests/initramfs_workspace.py

printf 'Checking exact initramfs verification...\n'
python3 tests/initramfs_verification.py
python3 tests/bounded_capture.py

printf 'Checking optional SteamOS recovery provenance...\n'
python3 tests/steamos_recovery_input.py

printf 'Checking built-module metadata validation...\n'
python3 tests/module_validation.py

printf 'Checking structured build provenance...\n'
python3 tests/provenance.py

printf 'Checking canonical artifact publisher...\n'
python3 tests/publisher.py
python3 - <<'PY' || fail "compile cache bypasses canonical artifact validation"
from pathlib import Path
script = Path("bootstrap/compile.sh").read_text(encoding="utf-8")
gate = script.index('CACHED_TRUST" == "locally-built-verified"')
validator = script.index('lib/validate_publish_inputs.py', gate)
acceptance = script.index('CACHE_CONTRACT_VALID" == "1"', validator)
cache_hit = script.index('CACHE_HIT=1', acceptance)
assert gate < validator < acceptance < cache_hit
PY

printf 'Checking reviewed gaming payload profile contract...\n'
python3 tests/gaming_payload_profiles.py
python3 tests/gaming_userspace_repack.py

printf 'Checking bounded archive confinement...\n'
python3 tests/archive_safety.py

printf 'Checking atomic output confinement...\n'
python3 tests/atomic_output.py

printf 'Checking measured-admission pacman configuration...\n'
python3 tests/pacman_config.py

printf 'Checking deterministic module repack contract...\n'
python3 tests/repack_artifacts.py

python3 - "$PROJECT_ROOT/lib" <<'PY' || fail "NVIDIA archive limits are inconsistent"
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import validate_install_inputs as installer
import validate_publish_inputs as publisher
gib = 1024 * 1024 * 1024
assert installer.MAX_MODULE_ARCHIVE_BYTES == gib
assert installer.MAX_MODULE_MEMBER_BYTES == gib
assert installer.MAX_TOTAL_MEMBER_BYTES == 2 * gib
assert publisher.MAX_ARCHIVE_BYTES == gib
assert publisher.MAX_MODULE_BYTES == gib
assert publisher.MAX_TOTAL_MEMBER_BYTES == 2 * gib
PY

printf 'Checking reviewed Valve signer policy...\n'
python3 tests/trust_policy.py

printf 'Checking cancellable process-group launcher...\n'
python3 - "$PROJECT_ROOT/lib/run_in_process_group.py" <<'PY' || \
    fail "cancellable process-group launcher did not terminate cleanly"
import os
import signal
import subprocess
import sys
import time

process = subprocess.Popen([sys.executable, sys.argv[1], "sh", "-c", "sleep 30 & wait"])
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if os.getpgid(process.pid) == process.pid:
        break
    time.sleep(0.05)
else:
    process.terminate()
    raise AssertionError("process group was not created")
os.killpg(process.pid, signal.SIGTERM)
assert process.wait(timeout=2) != 0
PY

python3 - "$PROJECT_ROOT/trust/valve-package-signers.json" <<'PY' || \
    fail "Valve package trust manifest is invalid"
import json
import re
import sys
with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
assert manifest["schemaVersion"] == 1
assert manifest["source"]["url"].startswith(
    "https://steamdeck-packages.steamos.cloud/archlinux-mirror/"
)
assert re.fullmatch(r"[0-9a-f]{64}", manifest["source"]["sha256"])
assert re.fullmatch(r"[0-9a-f]{64}", manifest["keyring"]["sha256"])
assert manifest["signers"]
for signer in manifest["signers"]:
    assert re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", signer["fingerprint"])
    assert signer["status"] in ("active", "revoked")
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", signer["reviewedAt"])
PY

printf 'Checking whitespace errors...\n'
git diff --check

printf 'Checking local help paths...\n'
for script_file in bootstrap/*.sh; do
    "$script_file" --help >/dev/null
done
./commit_myself.sh --help >/dev/null
./tests/non_sudo.sh --help >/dev/null
./tests/transaction.sh --help >/dev/null

printf 'Checking entry points outside the repository working directory...\n'
INVOCATION_DIR="$(mktemp -d /tmp/open-gpu-entrypoints.XXXXXX)"
cleanup_invocation_dir()
{
    rm -rf "$INVOCATION_DIR"
}
trap cleanup_invocation_dir EXIT INT TERM
for script_file in bootstrap/*.sh; do
    (
        cd "$INVOCATION_DIR"
        "$PROJECT_ROOT/$script_file" --help >/dev/null
    )
done
(
    cd "$INVOCATION_DIR"
    "$PROJECT_ROOT/commit_myself.sh" --help >/dev/null
    "$PROJECT_ROOT/tests/non_sudo.sh" --help >/dev/null
    "$PROJECT_ROOT/tests/transaction.sh" --help >/dev/null
)
cleanup_invocation_dir
trap - EXIT INT TERM

printf 'Checking mutually exclusive resolver modes...\n'
if ./bootstrap/setup_nvidia.sh \
    --development 580 \
    --use-upstream 580 \
    --resolve-only \
    >/dev/null 2>&1
then
    fail "setup_nvidia.sh accepted mutually exclusive modes"
fi

printf 'Checking development-mode terminology...\n'
if grep -RniE --exclude='TODO.md' --exclude-dir='.git' \
    -- '--driver|explicit:|DRIVER_SPEC|pristine-upstream|upstream-control' \
    bootstrap lib README.md >/dev/null
then
    fail "stale explicit/driver terminology remains"
fi

printf 'Checking online bootstrap failure cleanup...\n'
python3 tests/online_bootstrap_failures.py

printf 'Checking changed-install reboot ownership...\n'
python3 tests/online_reboot.py

printf 'Checking NVIDIA userspace interrupt cleanup...\n'
python3 tests/setup_nvidia_signal.py

printf 'Checking compile dependency-install interrupt cleanup...\n'
python3 tests/compile_readonly_signal.py

printf 'Checking upstream build-only isolation...\n'
python3 tests/upstream_build_only.py

printf 'Checking pre-bootstrap temp helper ordering...\n'
for bootstrap_entry in \
    bootstrap/online_install.sh \
    bootstrap/compile_online.sh \
    bootstrap/online_commit.sh \
    bootstrap/online_dev.sh \
    bootstrap/online_setup_nvidia.sh
do
    if grep -qE '^[[:space:]]*[A-Z_]+=.*project_mktemp_' "$bootstrap_entry"; then
        fail "$bootstrap_entry calls common temp helpers before common.sh is available"
    fi
done

printf 'Checking exact NVIDIA module-set validation...\n'
source "$PROJECT_ROOT/lib/common.sh"

"$PROJECT_ROOT/tests/module_content_hash.sh"

RAW_MODULES=(
    /fixture/nvidia.ko
    /fixture/nvidia-drm.ko
    /fixture/nvidia-modeset.ko
    /fixture/nvidia-peermem.ko
    /fixture/nvidia-uvm.ko
)
COMPRESSED_MODULES=(
    /fixture/nvidia-uvm.ko.zst
    /fixture/nvidia-peermem.ko.zst
    /fixture/nvidia-modeset.ko.zst
    /fixture/nvidia-drm.ko.zst
    /fixture/nvidia.ko.zst
)

validate_nvidia_module_set "${RAW_MODULES[@]}" ||
    fail "valid raw module set was rejected"
validate_nvidia_module_set "${COMPRESSED_MODULES[@]}" ||
    fail "valid compressed module set was rejected"

if validate_nvidia_module_set "${RAW_MODULES[@]:0:4}"; then
    fail "incomplete module set was accepted"
fi

if validate_nvidia_module_set \
    /fixture/nvidia.ko \
    /fixture/nvidia-drm.ko \
    /fixture/nvidia-modeset.ko \
    /fixture/nvidia-peermem.ko \
    /fixture/nvidia-peermem.ko
then
    fail "duplicate module set was accepted"
fi

if validate_nvidia_module_set \
    /fixture/nvidia.ko \
    /fixture/nvidia-drm.ko \
    /fixture/nvidia-modeset.ko \
    /fixture/nvidia-peermem.ko \
    /fixture/unexpected.ko
then
    fail "unexpected module name was accepted"
fi

printf 'Checking detached-HEAD branch semantics...\n'
source_branch_matches_expected HEAD "" ||
    fail "detached HEAD was rejected for an upstream build"
if source_branch_matches_expected HEAD main; then
    fail "named branch was accepted where detached HEAD was required"
fi
source_branch_matches_expected nvidia/580.119.02 nvidia/580.119.02 ||
    fail "matching project source branch was rejected"
if source_branch_matches_expected nvidia/580.119.02 ""; then
    fail "detached HEAD was accepted for a named project branch"
fi

printf 'Checking Valve repository discovery confinement...\n'
VALVE_REPOSITORIES="$(cat <<'EOF' | valve_repository_names_from_html
<a href="jupiter-main/">main</a>
<a href="jupiter-rel-20260830.1/">release</a>
<a href="jupiter-rel-20260830.1/">duplicate</a>
<a href="jupiter-beta_1/">beta</a>
<a href="jupiter-ci-test/">ci</a>
<a href="jupiter-../../escape/">escape</a>
<a href="jupiter-bad%2fescape/">encoded escape</a>
<a href="jupiter-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/">oversized</a>
<a href="other-repository/">other</a>
EOF
)"
[[ "$VALVE_REPOSITORIES" == $'jupiter-rel-20260830.1\njupiter-beta_1' ]] ||
    fail "Valve repository discovery accepted an unsafe or duplicate name"

printf 'Checking Valve kernel compiler metadata parsing...\n'
VALVE_COMPILER_DEFINITION=$'#define LINUX_COMPILER\t\t"gcc (GCC) 15.1.1 20250425, GNU ld (GNU Binutils) 2.45"'
[[ "$(kernel_compiler_version_from_definition "$VALVE_COMPILER_DEFINITION")" == "15.1.1" ]] ||
    fail "Valve tab-separated compiler metadata was not parsed"

printf 'Checking published release-selection policy...\n'
POLICY_FIXTURE="${PROJECT_ROOT}/tests/fixtures/releases/policy.json"

SELECTED="$(python3 "$PROJECT_ROOT/lib/select_release.py" \
    3.8.16 kernel-a "$POLICY_FIXTURE")"
[[ "$SELECTED" == $'3.8.16\t575.64.05\tkernel-a\tsteamos-3.8.16-nvidia-575.64.05-kkernel-a' ]] ||
    fail "exact SteamOS release was not preferred with exact kernel"

SELECTED="$(python3 "$PROJECT_ROOT/lib/select_release.py" \
    3.8.18 kernel-a "$POLICY_FIXTURE")"
[[ "$SELECTED" == $'3.8.17\t600.2.3\tkernel-a\tsteamos-3.8.17-nvidia-600.2.3-kkernel-a' ]] ||
    fail "bounded same-series fallback selected the wrong release"

SELECTED="$(python3 "$PROJECT_ROOT/lib/select_release.py" \
    3.8.16 absent-kernel "$POLICY_FIXTURE")"
[[ -z "$SELECTED" ]] || fail "release selector accepted the wrong kernel"

SELECTED="$(python3 "$PROJECT_ROOT/lib/select_release.py" \
    3.9.0 kernel-a "$POLICY_FIXTURE")"
[[ -z "$SELECTED" ]] || fail "release selector crossed SteamOS major/minor"

printf 'Checking offline-target JSON resolver contract...\n'
RESOLVED="$(python3 "$PROJECT_ROOT/lib/resolve_target.py" \
    --steamos 3.8.16 --kernel kernel-a --architecture x86_64 \
    --releases "$POLICY_FIXTURE")"
python3 - "$RESOLVED" <<'PY' || fail "compatible target JSON contract is invalid"
import json
import sys
result = json.loads(sys.argv[1])
assert result["schemaVersion"] == 2
assert result["status"] == "compatible"
assert result["compatibility"] == "exact"
assert result["target"]["kernelVersion"] == "kernel-a"
assert result["publication"]["nvidiaVersion"] == "575.64.05"
assert result["artifact"]["checksum"]["algorithm"] == "sha256"
assert result["artifact"]["provenance"]["name"].endswith(".provenance.json")
assert result["artifact"]["trust"] == {
    "classification": "pending-provenance-verification",
    "source": result["artifact"]["provenance"]["name"],
    "requiredVerification": "external-and-embedded-provenance-byte-match",
}
PY

python3 - "$PROJECT_ROOT/lib" "$POLICY_FIXTURE" <<'PY' || \
    fail "resolver accepted a publication without provenance"
import json
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from resolve_target import resolve_target

with open(sys.argv[2], encoding="utf-8") as release_file:
    releases = json.load(release_file)
release = releases[0]
release["assets"] = [
    asset for asset in release["assets"]
    if not asset["name"].endswith(".provenance.json")
]
result = resolve_target("3.8.16", "kernel-a", "x86_64", releases, "owner/repo")
assert result["status"] == "no_compatible_artifact"
assert result["reason"] == "release_assets_missing"
assert result["missingAssets"] == [
    "nvidia-open-steamos-3.8.16-nvidia-575.64.05-kkernel-a-x86_64.provenance.json"
]
PY

python3 - "$PROJECT_ROOT/lib" <<'PY' || fail "resolver accepted malformed metadata"
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from resolve_target import resolve_target

for releases, repository, reason in (
    (["not-an-object"], "CorniiDog/OPEMOS", "release_metadata_invalid"),
    ([], "invalid/repository/path", "invalid_repository"),
):
    result = resolve_target("3.8.16", "kernel-a", "x86_64", releases, repository)
    assert result["schemaVersion"] == 2
    assert result["status"] in ("invalid_target", "resolver_error")
    assert result["reason"] == reason
    assert "artifact" not in result

release = {
    "tag_name": "steamos-3.8.16-nvidia-575.64.05-kkernel-a",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-01-01T00:00:00Z",
    "assets": [],
}
result = resolve_target(
    "3.8.16", "kernel-a", "x86_64", [release, dict(release)], "owner/repo"
)
assert result["status"] == "resolver_error"
assert result["reason"] == "release_metadata_ambiguous"
PY

RESOLVED="$(python3 "$PROJECT_ROOT/lib/resolve_target.py" \
    --steamos 3.8.14 \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --architecture x86_64 \
    --releases "$POLICY_FIXTURE")"
python3 - "$RESOLVED" <<'PY' || fail "no-artifact target JSON contract is invalid"
import json
import sys
result = json.loads(sys.argv[1])
assert result["status"] == "no_compatible_artifact"
assert result["reason"] == "no_compatible_release"
assert "artifact" not in result
action = result["nextAction"]
assert {key: action[key] for key in (
    "schemaVersion", "kind", "entrypoint", "executionArchitecture", "kernelPolicy"
)} == {
    "schemaVersion": 1,
    "kind": "build_exact_target",
    "entrypoint": "bootstrap/build_for_target.sh",
    "executionArchitecture": "x86_64",
    "kernelPolicy": "exact",
}
plan = action["buildPlan"]
assert plan["schemaVersion"] == 1
assert plan["target"] == {
    "steamosVersion": "3.8.14",
    "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
    "nvidiaVersion": "575.64.05",
    "architecture": "x86_64",
}
assert plan["source"] == {
    "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
    "ref": "refs/heads/nvidia/575.64.05",
    "commit": "40bd1b5d6d39ae4e4180b7a665df144b08854d14",
}
assert len(plan["policy"]["sha256"]) == 64
assert len(plan["baseline"]["archiveSha256"]) == 64
assert len(plan["baseline"]["provenanceSha256"]) == 64
PY

RESOLVED="$(python3 "$PROJECT_ROOT/lib/resolve_target.py" \
    --steamos 3.8.16 --kernel absent-kernel --architecture x86_64 \
    --releases "$POLICY_FIXTURE")"
python3 - "$RESOLVED" <<'PY' || fail "unreviewed build target was authorized"
import json
import sys
result = json.loads(sys.argv[1])
assert result["status"] == "no_compatible_artifact"
assert result["reason"] == "no_reviewed_exact_target_build_plan"
assert "nextAction" not in result
PY

RESOLVED="$(python3 "$PROJECT_ROOT/lib/resolve_target.py" \
    --steamos 3.8.16 --kernel kernel-a --architecture aarch64 \
    --releases "$POLICY_FIXTURE")"
python3 - "$RESOLVED" <<'PY' || fail "unsupported-architecture JSON contract is invalid"
import json
import sys
result = json.loads(sys.argv[1])
assert result["status"] == "unsupported_target"
assert result["reason"] == "unsupported_architecture"
PY

printf 'Checking Fedora offline-target build plan...\n'
TARGET_BUILD_PLAN="$(./bootstrap/build_for_target.sh \
    --steamos 3.8.14 \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 \
    --resolve-only)"
python3 - "$TARGET_BUILD_PLAN" <<'PY' || fail "offline-target build plan is invalid"
import json
import sys
result = json.loads(sys.argv[1])
target = result["target"]
assert result["schemaVersion"] == 1
assert result["status"] == "ready"
assert target["architecture"] == "x86_64"
assert target["headersFilename"] == (
    "linux-neptune-616-headers-6.16.12.valve24.4-1-x86_64.pkg.tar.zst"
)
assert target["assetName"].endswith(
    "k6.16.12-valve24.4-1-neptune-616-gfe145653a794-x86_64.tar.gz"
)
PY

if ./bootstrap/build_for_target.sh \
    --steamos 3.8.14 --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 --header-keyring /tmp/untrusted-keyring \
    --resolve-only >/dev/null 2>&1
then
    fail "offline-target build accepted a keyring without a pinned signer"
fi
if ./bootstrap/build_for_target.sh \
    --steamos 3.8.14 \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 \
    --header-keyring "$PROJECT_ROOT/trust/valve-package-signers.json" \
    --header-signer 0000000000000000000000000000000000000000 \
    --resolve-only >/dev/null 2>&1
then
    fail "offline-target build accepted a signer absent from reviewed trust"
fi

printf 'Checking early target-validation result contract...\n'
INVALID_TARGET_RESULT="$(mktemp /tmp/offline-target-invalid.XXXXXX)"
UNSUPPORTED_ARCH_RESULT="$(mktemp /tmp/offline-target-arch.XXXXXX)"
PARSER_RESULT="$(mktemp /tmp/offline-target-parser.XXXXXX)"
if ./bootstrap/build_for_target.sh \
    --steamos invalid \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 --resolve-only \
    --result-json "$INVALID_TARGET_RESULT" >/dev/null 2>&1
then
    fail "offline-target build accepted an invalid SteamOS target"
fi
if ./bootstrap/build_for_target.sh \
    --steamos 3.8.14 \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 --architecture aarch64 --resolve-only \
    --result-json "$UNSUPPORTED_ARCH_RESULT" >/dev/null 2>&1
then
    fail "offline-target build accepted an unsupported architecture"
fi
if ./bootstrap/build_for_target.sh \
    --unknown-option --result-json "$PARSER_RESULT" >/dev/null 2>&1
then
    fail "offline-target build accepted an unknown option"
fi
python3 - "$INVALID_TARGET_RESULT" "$UNSUPPORTED_ARCH_RESULT" "$PARSER_RESULT" <<'PY' || \
    fail "early target-validation JSON contract is invalid"
import json
import sys

expected = ("invalid_target", "unsupported_architecture", "invalid_target")
for path, reason in zip(sys.argv[1:], expected):
    with open(path, encoding="utf-8") as result_file:
        result = json.load(result_file)
    assert result["schemaVersion"] == 1
    assert result["status"] == "failed"
    assert result["reason"] == reason
    assert result["trust"] == "development-unverified"
    assert "artifact" not in result
PY
rm -f "$INVALID_TARGET_RESULT" "$UNSUPPORTED_ARCH_RESULT" "$PARSER_RESULT"

printf 'Checking final offline-target build-result contract...\n'
RESULT_FIXTURE="$(mktemp /tmp/offline-target-result.XXXXXX)"
python3 "$PROJECT_ROOT/lib/write_build_result.py" \
    --output "$RESULT_FIXTURE" --status success --reason build_complete \
    --message "fixture passed" --trust development-unverified \
    --steamos 3.8.14 --kernel kernel-a --nvidia 575.64.05 \
    --architecture x86_64 --archive artifact.tar.gz \
    --checksum artifact.tar.gz.sha256 --build-info artifact.build-info.txt \
    --provenance artifact.provenance.json \
    --archive-sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
python3 - "$RESULT_FIXTURE" <<'PY' || fail "final build-result contract is invalid"
import json
import sys
with open(sys.argv[1], encoding="utf-8") as result_file:
    result = json.load(result_file)
assert result["schemaVersion"] == 1
assert result["status"] == "success"
assert result["reason"] == "build_complete"
assert result["trust"] == "development-unverified"
assert result["artifact"]["archive"] == "artifact.tar.gz"
assert result["artifact"]["provenance"] == "artifact.provenance.json"
assert result["target"]["kernelVersion"] == "kernel-a"
PY
if python3 "$PROJECT_ROOT/lib/write_build_result.py" \
    --output "${RESULT_FIXTURE}.invalid" --status success --reason build_complete \
    --message "invalid fixture" --steamos 3.8.14 --kernel kernel-a \
    --nvidia 575.64.05 --architecture x86_64 --archive /private/artifact.tar.gz \
    --checksum artifact.tar.gz.sha256 --build-info artifact.build-info.txt \
    --archive-sha256 short >/dev/null 2>&1
then
    fail "build-result writer accepted an incomplete/path-valued success artifact"
fi
rm -f "$RESULT_FIXTURE" "${RESULT_FIXTURE}.invalid"

printf 'Checking offline-root installer contract...\n'
./bootstrap/install_to_root.sh --help >/dev/null
./lib/verify_installed_userspace.py --help >/dev/null
./lib/verify_installed_modules.py --help >/dev/null
./lib/check_initramfs_workspace.py --help >/dev/null
./lib/run_pacman_transaction.py --help >/dev/null
python3 bootstrap/audit_userspace_closure.py --help >/dev/null
python3 bootstrap/finalize_userspace_lock.py --help >/dev/null
python3 - "$PROJECT_ROOT/trust/arch-full-keyring-provenance.json" <<'PY' || \
    fail "Arch full-keyring provenance manifest is invalid"
import json, re, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["schemaVersion"] == 1
assert re.fullmatch(r"[0-9]{4}/[0-9]{2}/[0-9]{2}", manifest["snapshot"])
assert re.fullmatch(r"[0-9a-f]{64}", manifest["source"]["sha256"])
assert re.fullmatch(r"[0-9a-f]{64}", manifest["source"]["signatureSha256"])
assert re.fullmatch(r"[0-9a-f]{64}", manifest["keyring"]["sha256"])
PY
python3 bootstrap/prepare_nvidia_package_keyring.py --help >/dev/null
python3 - "$PROJECT_ROOT/trust/nvidia-userspace-package-signers.json" <<'PY' || \
    fail "NVIDIA userspace package trust manifest is invalid"
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
assert manifest["schemaVersion"] == 1
active = {
    signer["fingerprint"]: tuple(signer["packages"])
    for signer in manifest["signers"]
    if signer["status"] == "active"
}
assert active == {
    "05C7775A9E8B977407FE08E69D4C5AA15426DA0A": ("nvidia-utils",),
    "D2E95FEC015CF1F911AAAB0C3D4C5008BB5C8D29": ("lib32-nvidia-utils", "egl-wayland", "egl-x11"),
    "83BC8889351B5DEBBB68416EB8AC08600F108CDF": ("eglexternalplatform", "egl-wayland"),
    "8FC15A064950A99DD1BD14DD39E4B877E62EB915": ("egl-gbm",),
}
for signer in manifest["signers"]:
    assert re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", signer["fingerprint"])
    assert signer["status"] in ("active", "revoked")
    assert signer["packages"]
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", signer["reviewedAt"])
PY
python3 - \
    "$PROJECT_ROOT/locks/userspace/steamos-3.8.14-nvidia-575.64.05.json" \
    "$PROJECT_ROOT/trust/keyrings/archlinux-nvidia-userspace-2025-08-01.gpg" \
    "$PROJECT_ROOT/trust/nvidia-userspace-package-signers.json" <<'PY' || \
    fail "published reviewed userspace lock is inconsistent"
import hashlib
import json
import pathlib
import sys

lock_path, keyring_path, policy_path = map(pathlib.Path, sys.argv[1:])
lock = json.loads(lock_path.read_text(encoding="utf-8"))
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert lock["schemaVersion"] == 1
assert lock["status"] == "reviewed"
assert lock["missingReview"] == []
assert lock["target"] == {
    "architecture": "x86_64",
    "nvidiaVersion": "575.64.05",
    "steamosVersion": "3.8.14",
}
assert [package["name"] for package in lock["packages"]] == [
    "egl-gbm", "egl-wayland", "egl-x11", "eglexternalplatform",
    "lib32-nvidia-utils", "nvidia-utils",
]
assert lock["keyring"]["filename"] == keyring_path.name
assert lock["keyring"]["sha256"] == digest(keyring_path)
assert lock["keyring"]["provenance"]["policySha256"] == digest(policy_path)
assert lock["snapshot"]["identity"] == "2025/08/01"
assert lock["snapshot"]["url"] == "https://archive.archlinux.org/repos/2025/08/01/"
PY
python3 tests/btrfs_measurement.py
python3 tests/measurement_launcher.py
python3 tests/offline_root_validation.py
python3 tests/userspace_audit.py
INSTALL_RESULT_FIXTURE="$(mktemp /tmp/offline-install-result.XXXXXX)"
python3 "$PROJECT_ROOT/lib/write_install_result.py" \
    --output "$INSTALL_RESULT_FIXTURE" --status validated \
    --reason validation_complete --message "fixture validated" --phase validated \
    --root /target-root --steamos 3.8.16 --kernel kernel-a \
    --nvidia 575.64.05 --trust locally-built-verified \
    --archive artifact.tar.gz --provenance artifact.provenance.json \
    --nvidia-utils nvidia-utils.pkg.tar.zst \
    --lib32-nvidia-utils lib32-nvidia-utils.pkg.tar.zst
python3 - "$INSTALL_RESULT_FIXTURE" <<'PY' || fail "offline install-result contract is invalid"
import json
import sys
with open(sys.argv[1], encoding="utf-8") as result_file:
    result = json.load(result_file)
assert result["schemaVersion"] == 1
assert result["status"] == "validated"
assert result["reason"] == "validation_complete"
assert result["cleanup"]["mountsReleased"] is True
assert result["target"]["root"] == "/target-root"
assert result["target"]["kernelVersion"] == "kernel-a"
assert result["inputs"]["provenance"] == "artifact.provenance.json"
PY
if python3 "$PROJECT_ROOT/lib/write_install_result.py" \
    --output "${INSTALL_RESULT_FIXTURE}.invalid" --status success \
    --reason install_complete --message invalid --phase complete \
    --root /target-root --kernel kernel-a --mounts-released false >/dev/null 2>&1
then
    fail "install-result writer accepted success with active mounts"
fi
if python3 "$PROJECT_ROOT/lib/write_install_result.py" \
    --output "${INSTALL_RESULT_FIXTURE}.invalid" --status validated \
    --reason wrong_reason --message invalid --phase validated \
    --root /target-root --kernel kernel-a >/dev/null 2>&1
then
    fail "install-result writer accepted an inconsistent terminal status"
fi
if python3 "$PROJECT_ROOT/lib/write_install_result.py" \
    --output "${INSTALL_RESULT_FIXTURE}.invalid" --status failed \
    --reason 'bad reason' --message invalid --phase validation \
    --root /target-root --kernel kernel-a >/dev/null 2>&1
then
    fail "install-result writer accepted a noncanonical reason"
fi
rm -f "$INSTALL_RESULT_FIXTURE" "${INSTALL_RESULT_FIXTURE}.invalid"

printf 'Checking fake-root install/uninstall transactions...\n'
if (( BASH_VERSINFO[0] >= 4 )); then
    ./tests/transaction.sh
else
    printf 'Skipping transaction tests: Bash 4+ is required (found %s).\n' \
        "$BASH_VERSION"
fi

printf 'Checking fake-root path confinement...\n'
EXPECTED_TEST_PATH="$(canonicalize_path /tmp/project-test-root/usr/lib/modules)"
[[ "$(PROJECT_TEST_MODE=1 PROJECT_TEST_ROOT=/tmp/project-test-root \
    project_system_path /usr/lib/modules)" == "$EXPECTED_TEST_PATH" ]] ||
    fail "test system path was not redirected"
if (
    PROJECT_TEST_MODE=1
    PROJECT_TEST_ROOT=/etc
    project_system_path /usr/lib/modules >/dev/null 2>&1
); then
    fail "test system path escaped /tmp or HOME confinement"
fi

printf 'All local checks passed.\n'
