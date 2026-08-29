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

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || die "Source branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."

SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "${SOURCE_DIR}/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$EXPECTED_NVIDIA" ]] || die "Source version ${SOURCE_VERSION} does not match ${EXPECTED_NVIDIA}."

KERNEL_BASE="${KERNEL_VERSION%%-neptune-*}"
KERNEL_PKGREL="$(printf '%s\n' "$KERNEL_BASE" | sed -n 's/.*-\([0-9][0-9]*\)$/\1/p')"
KERNEL_PKGVER="${KERNEL_BASE%-${KERNEL_PKGREL}}"
KERNEL_PKGVER="${KERNEL_PKGVER/-valve/.valve}"

[[ "$KERNEL_PKGREL" =~ ^[0-9]+$ ]] || die "Could not derive SteamOS kernel package release from ${KERNEL_VERSION}."
[[ "$KERNEL_PKGVER" =~ ^[0-9] ]] || die "Could not derive SteamOS kernel package version from ${KERNEL_VERSION}."

HEADERS_FILENAME="linux-neptune-${NEPTUNE_SERIES}-headers-${KERNEL_PKGVER}-${KERNEL_PKGREL}-x86_64.pkg.tar.zst"

log "Build environment: ${NVIDIA_BUILD_IMAGE}"
log "SteamOS kernel:    ${KERNEL_VERSION}"
log "NVIDIA version:    ${EXPECTED_NVIDIA}"
log "Source branch:     ${EXPECTED_BRANCH}"
log "Headers package:   ${HEADERS_FILENAME}"
echo

podman run \
    --rm \
    --security-opt label=disable \
    -e "TARGET_KERNEL=${KERNEL_VERSION}" \
    -e "HEADERS_FILENAME=${HEADERS_FILENAME}" \
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
            openssl-devel \
            bc \
            kmod \
            curl \
            libarchive \
            bsdtar \
            findutils \
            perl \
            python3 \
            diffutils \
            pahole \
            zstd

        MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror"

        printf "Searching Valve SteamOS repositories for:\n"
        printf "  %s\n" "$HEADERS_FILENAME"

        DISCOVERED="$(
            curl -fsSL "$MIRROR/" \
                | grep -oE "href=\\\"jupiter-[^\\\"/]*/\\\"" \
                | sed -e "s|^href=\\\"||" -e "s|/\\\"$||" \
                | grep -vxE "jupiter-(main|ci-test)" \
                | sort -rV \
                | tr "\n" " " \
                || true
        )"

        REPOS="jupiter-main $DISCOVERED"
        HEADER_URL=""
        HEADER_REPO=""

        for repo in $REPOS; do
            candidate="$MIRROR/$repo/os/x86_64/$HEADERS_FILENAME"

            printf "  probing %s...\n" "$repo"

            if curl -fsIL -o /dev/null "$candidate"; then
                HEADER_URL="$candidate"
                HEADER_REPO="$repo"
                break
            fi
        done

        [[ -n "$HEADER_URL" ]] || {
            printf "Could not find exact SteamOS headers package:\n" >&2
            printf "  %s\n" "$HEADERS_FILENAME" >&2
            printf "Repositories probed:\n  %s\n" "$REPOS" >&2
            exit 1
        }

        printf "Found exact headers in %s\n" "$HEADER_REPO"
        printf "Downloading headers...\n"

        mkdir -p /kernel-root
        curl -fL "$HEADER_URL" -o "/tmp/$HEADERS_FILENAME"
        bsdtar -xf "/tmp/$HEADERS_FILENAME" -C /kernel-root

        KERNEL_TREE="/kernel-root/usr/lib/modules/$TARGET_KERNEL/build"

        if [[ ! -d "$KERNEL_TREE" ]]; then
            KERNEL_TREE="$(find /kernel-root/usr/lib/modules -mindepth 2 -maxdepth 2 -type d -name build | head -n1)"
        fi

        [[ -n "$KERNEL_TREE" ]] || {
            printf "Extracted package does not contain a kernel build tree.\n" >&2
            exit 1
        }

        [[ -f "$KERNEL_TREE/Makefile" ]] || {
            printf "Kernel Makefile missing from %s\n" "$KERNEL_TREE" >&2
            exit 1
        }

        [[ -f "$KERNEL_TREE/include/generated/autoconf.h" ]] || {
            printf "Kernel headers are not prepared: %s\n" "$KERNEL_TREE" >&2
            exit 1
        }

        printf "\nUsing isolated SteamOS kernel tree:\n"
        printf "  %s\n\n" "$KERNEL_TREE"

        make clean || true

        make modules \
            -j"$(nproc)" \
            SYSSRC="$KERNEL_TREE" \
            SYSOUT="$KERNEL_TREE"

        mapfile -t modules < <(
            find kernel-open -maxdepth 1 -type f -name "*.ko" | sort
        )

        (( ${#modules[@]} > 0 )) || {
            printf "NVIDIA build produced no kernel modules.\n" >&2
            exit 1
        }

        printf "\nBuilt modules:\n"

        for module in "${modules[@]}"; do
            vermagic="$(modinfo -F vermagic "$module" | awk "{print \\\$1}")"

            printf "  %s -> %s\n" "$(basename "$module")" "$vermagic"

            [[ "$vermagic" == "$TARGET_KERNEL" ]] || {
                printf "Vermagic mismatch for %s\n" "$(basename "$module")" >&2
                printf "Expected: %s\n" "$TARGET_KERNEL" >&2
                printf "Actual:   %s\n" "$vermagic" >&2
                exit 1
            }
        done
    '

mapfile -t MODULES < <(
    find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort
)

(( ${#MODULES[@]} > 0 )) || die "Container build completed but no modules were returned."

echo
for module in "${MODULES[@]}"; do
    ok "Built $(basename "$module")"
done

ok "Contained NVIDIA kernel-module build completed successfully."
