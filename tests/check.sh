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

printf 'Checking fake-root install/uninstall transactions...\n'
./tests/transaction.sh

printf 'Checking fake-root path confinement...\n'
[[ "$(PROJECT_TEST_MODE=1 PROJECT_TEST_ROOT=/tmp/project-test-root \
    project_system_path /usr/lib/modules)" == "/tmp/project-test-root/usr/lib/modules" ]] ||
    fail "test system path was not redirected"
if (
    PROJECT_TEST_MODE=1
    PROJECT_TEST_ROOT=/etc
    project_system_path /usr/lib/modules >/dev/null 2>&1
); then
    fail "test system path escaped /tmp or HOME confinement"
fi

printf 'All local checks passed.\n'
