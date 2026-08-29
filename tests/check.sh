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

printf 'Checking whitespace errors...\n'
git diff --check

printf 'Checking local help paths...\n'
for script_file in bootstrap/*.sh; do
    "$script_file" --help >/dev/null
done
./commit_myself.sh --help >/dev/null

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
    bootstrap/online_dev.sh
do
    if grep -qE '^[[:space:]]*[A-Z_]+=.*project_mktemp_' "$bootstrap_entry"; then
        fail "$bootstrap_entry calls common temp helpers before common.sh is available"
    fi
done

printf 'All local checks passed.\n'
