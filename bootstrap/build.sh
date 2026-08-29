#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

need_cmd git
need_cmd podman
need_cmd nvidia-smi

require_steamos

[[ -f "$STATE_FILE" ]] || die "Development/build state is missing."

SOURCE_DIR="$(state_value source_repo)"
EXPECTED_NVIDIA="$(state_value installed_nvidia)"
EXPECTED_BRANCH="$(state_value source_branch)"
KERNEL_VERSION="$(get_kernel_version)"
NEPTUNE_SERIES="$(get_neptune_series "$KERNEL_VERSION")"
KERNEL_TREE="/usr/src/linux-neptune-${NEPTUNE_SERIES}"

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"
[[ -d "$KERNEL_TREE" ]] || die "SteamOS kernel tree missing: ${KERNEL_TREE}"
[[ -f "$KERNEL_TREE/Makefile" ]] || die "Kernel tree has no Makefile: ${KERNEL_TREE}"
[[ -f "$KERNEL_TREE/.config" ]] || die "Kernel tree has no .config: ${KERNEL_TREE}"
[[ -f "$KERNEL_TREE/include/generated/autoconf.h" ]] || die "Kernel tree is not prepared: ${KERNEL_TREE}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || die "Source branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."

SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "${SOURCE_DIR}/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$EXPECTED_NVIDIA" ]] || die "Source version ${SOURCE_VERSION} does not match ${EXPECTED_NVIDIA}."

log "Build environment: ${NVIDIA_BUILD_IMAGE}"
log "SteamOS kernel:    ${KERNEL_VERSION}"
log "Kernel tree:       ${KERNEL_TREE}"
log "NVIDIA version:    ${EXPECTED_NVIDIA}"
log "Source branch:     ${EXPECTED_BRANCH}"
echo

podman run \
    --rm \
    --security-opt label=disable \
    -e "TARGET_KERNEL=${KERNEL_VERSION}" \
    -v "${SOURCE_DIR}:/src" \
    -v "${KERNEL_TREE}:/kernel:ro" \
    -w /src \
    "$NVIDIA_BUILD_IMAGE" \
    bash -euxo pipefail -c '
        dnf install -y \
            gcc \
            gcc-c++ \
            make \
            binutils \
            elfutils-libelf-devel \
            kmod \
            findutils \
            perl \
            python3 \
            diffutils \
            pahole

        [[ -f /kernel/Makefile ]]
        [[ -f /kernel/.config ]]
        [[ -f /kernel/include/generated/autoconf.h ]]

        printf "Building against mounted SteamOS kernel tree:\n"
        printf "  /kernel\n\n"

        make clean || true

        make modules \
            -j"$(nproc)" \
            SYSSRC=/kernel \
            SYSOUT=/kernel

        mapfile -t modules < <(find kernel-open -maxdepth 1 -type f -name "*.ko" | sort)
        (( ${#modules[@]} > 0 ))

        for module in "${modules[@]}"; do
            vermagic="$(modinfo -F vermagic "$module" | awk "{print \\$1}")"
            printf "%s -> %s\n" "$(basename "$module")" "$vermagic"
            [[ "$vermagic" == "$TARGET_KERNEL" ]]
        done
    '

mapfile -t MODULES < <(find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort)
(( ${#MODULES[@]} > 0 )) || die "Container build completed but no modules were returned."

echo
for module in "${MODULES[@]}"; do
    ok "Built $(basename "$module")"
done

ok "Contained NVIDIA kernel-module build completed successfully."
