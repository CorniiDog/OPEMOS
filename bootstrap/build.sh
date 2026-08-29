#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

need_cmd git
need_cmd make
need_cmd nproc
need_cmd modinfo

require_steamos

if [[ ! -f "$STATE_FILE" ]]; then
    log "Development state not found; preparing source repository..."
    "${SCRIPT_DIR}/setup_dev.sh"
fi

SOURCE_DIR="$(state_value source_repo)"
EXPECTED_NVIDIA="$(state_value installed_nvidia)"
EXPECTED_BRANCH="$(state_value source_branch)"
KERNEL_VERSION="$(get_kernel_version)"

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || die "Source branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."

SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "${SOURCE_DIR}/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$EXPECTED_NVIDIA" ]] || die "Source version ${SOURCE_VERSION} does not match installed NVIDIA ${EXPECTED_NVIDIA}."

KERNEL_BUILD="/lib/modules/${KERNEL_VERSION}/build"
[[ -e "$KERNEL_BUILD" ]] || die "Kernel build directory missing: ${KERNEL_BUILD}"

log "Building NVIDIA ${EXPECTED_NVIDIA} modules..."
log "Kernel: ${KERNEL_VERSION}"
log "Source: ${SOURCE_DIR}"

make -C "$SOURCE_DIR" modules -j"$(nproc)"

mapfile -t MODULES < <(find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort)
(( ${#MODULES[@]} > 0 )) || die "Build completed but no kernel modules were found."

for module in "${MODULES[@]}"; do
    VERMAGIC="$(modinfo -F vermagic "$module" | awk '{print $1}')"
    [[ "$VERMAGIC" == "$KERNEL_VERSION" ]] || die "$(basename "$module") vermagic ${VERMAGIC} does not match ${KERNEL_VERSION}."
    ok "Verified $(basename "$module") -> ${VERMAGIC}"
done

ok "NVIDIA kernel module build completed successfully."
