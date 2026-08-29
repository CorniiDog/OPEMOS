#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

need_cmd git
need_cmd podman
need_cmd pacman
need_cmd nvidia-smi

require_steamos

[[ -f "$STATE_FILE" ]] || die "Development/build state is missing."

SOURCE_DIR="$(state_value source_repo)"
EXPECTED_NVIDIA="$(state_value installed_nvidia)"
EXPECTED_BRANCH="$(state_value source_branch)"
KERNEL_VERSION="$(get_kernel_version)"
HEADERS_PACKAGE="$(get_neptune_headers_package "$KERNEL_VERSION")"

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || die "Source branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."

SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "${SOURCE_DIR}/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$EXPECTED_NVIDIA" ]] || die "Source version ${SOURCE_VERSION} does not match ${EXPECTED_NVIDIA}."

log "Resolving SteamOS kernel headers without installing them..."

HEADER_URL="$(pacman -Sp --print-format '%l' "$HEADERS_PACKAGE" 2>/dev/null | head -n1)"

[[ "$HEADER_URL" =~ ^https?:// ]] || die "Could not resolve ${HEADERS_PACKAGE} from SteamOS repositories."

HEADER_FILENAME="${HEADER_URL##*/}"

log "Build environment: ${NVIDIA_BUILD_IMAGE}"
log "SteamOS kernel:    ${KERNEL_VERSION}"
log "NVIDIA version:    ${EXPECTED_NVIDIA}"
log "Source branch:     ${EXPECTED_BRANCH}"
log "Headers package:   ${HEADERS_PACKAGE}"
echo

podman run \
    --rm \
    --security-opt label=disable \
    -e "TARGET_KERNEL=${KERNEL_VERSION}" \
    -e "TARGET_NVIDIA=${EXPECTED_NVIDIA}" \
    -e "HEADER_URL=${HEADER_URL}" \
    -e "HEADER_FILENAME=${HEADER_FILENAME}" \
    -v "${SOURCE_DIR}:/src" \
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
            curl \
            libarchive \
            findutils \
            perl \
            python3 \
            diffutils

        mkdir -p /kernel-root

        curl -fL "$HEADER_URL" -o "/tmp/$HEADER_FILENAME"

        bsdtar -xf "/tmp/$HEADER_FILENAME" -C /kernel-root

        KERNEL_SOURCE="$(find /kernel-root/usr/src -mindepth 1 -maxdepth 1 -type d -name "linux*" | head -n1)"

        [[ -n "$KERNEL_SOURCE" ]]
        [[ -f "$KERNEL_SOURCE/Makefile" ]]
        [[ -f "$KERNEL_SOURCE/.config" ]]

        printf "Kernel source: %s\n" "$KERNEL_SOURCE"

        make clean || true

        make modules \
            -j"$(nproc)" \
            SYSSRC="$KERNEL_SOURCE" \
            SYSOUT="$KERNEL_SOURCE"

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

for module in "${MODULES[@]}"; do
    ok "Built $(basename "$module")"
done

ok "Contained NVIDIA kernel-module build completed successfully."
