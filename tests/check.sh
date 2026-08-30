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
for script_file in bootstrap/*.sh lib/*.sh commit_myself.sh tests/*.sh; do
    bash -n "$script_file"
done
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

printf 'Checking exact target-header validation...\n'
python3 tests/header_validation.py

printf 'Checking built-module metadata validation...\n'
python3 tests/module_validation.py

printf 'Checking structured build provenance...\n'
python3 tests/provenance.py

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
    -- '--driver|explicit:|DRIVER_SPEC' \
    bootstrap lib README.md >/dev/null
then
    fail "stale explicit/driver terminology remains"
fi

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

printf 'Checking Valve kernel compiler metadata parsing...\n'
VALVE_COMPILER_DEFINITION=$'#define LINUX_COMPILER\t\t"gcc (GCC) 15.1.1 20250425, GNU ld (GNU Binutils) 2.45"'
[[ "$(kernel_compiler_version_from_definition "$VALVE_COMPILER_DEFINITION")" == "15.1.1" ]] ||
    fail "Valve tab-separated compiler metadata was not parsed"

printf 'Checking certified release-selection policy...\n'
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
assert result["schemaVersion"] == 1
assert result["status"] == "compatible"
assert result["compatibility"] == "exact"
assert result["target"]["kernelVersion"] == "kernel-a"
assert result["certification"]["nvidiaVersion"] == "575.64.05"
assert result["artifact"]["checksum"]["algorithm"] == "sha256"
PY

RESOLVED="$(python3 "$PROJECT_ROOT/lib/resolve_target.py" \
    --steamos 3.8.16 --kernel absent-kernel --architecture x86_64 \
    --releases "$POLICY_FIXTURE")"
python3 - "$RESOLVED" <<'PY' || fail "no-artifact target JSON contract is invalid"
import json
import sys
result = json.loads(sys.argv[1])
assert result["status"] == "no_compatible_artifact"
assert result["reason"] == "no_certified_release"
assert "artifact" not in result
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
