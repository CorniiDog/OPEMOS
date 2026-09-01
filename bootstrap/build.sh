#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

usage()
{
    printf 'Usage: %s\n' "$0"
    printf 'Build the source recorded in the development state using Fedora/Podman.\n'
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
fi

need_cmd git
need_cmd podman

require_steamos

[[ -f "$STATE_FILE" ]] || die "Development/build state is missing."

SOURCE_DIR="$(state_value source_repo)"
EXPECTED_NVIDIA="$(state_value installed_nvidia)"
EXPECTED_BRANCH="$(state_value source_branch)"
KERNEL_VERSION="$(get_kernel_version)"
NEPTUNE_SERIES="$(get_neptune_series "$KERNEL_VERSION")"

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"

if ! source_branch_matches_expected "$EXPECTED_BRANCH" "$CURRENT_BRANCH"; then
    if [[ "$EXPECTED_BRANCH" == "HEAD" ]]; then
        die "Upstream source must be detached HEAD; currently on branch ${CURRENT_BRANCH}."
    fi

    die "Source branch is ${CURRENT_BRANCH}; expected ${EXPECTED_BRANCH}."
fi

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

mkdir -p "$STATE_DIR"
BUILD_ENV_FILE="${STATE_DIR}/last-build-environment"
# Never let a failed build make a later package inherit metadata from an older
# successful build.
rm -f "$BUILD_ENV_FILE"

podman run \
    --rm \
    --security-opt label=disable \
    -e "TARGET_KERNEL=${KERNEL_VERSION}" \
    -e "HEADERS_FILENAME=${HEADERS_FILENAME}" \
    -v "${SOURCE_DIR}:/src" \
    -v "${STATE_DIR}:/build-state" \
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
                | LC_ALL=C grep -oE "href=\"jupiter-[A-Za-z0-9._-]+/\"" \
                | sed -e "s|^href=\"||" -e "s|/\"$||" \
                | grep -vxE "jupiter-(main|ci-test)" \
                | awk "length(\$0) <= 128" \
                | sort -u -rV \
                | awk "NR <= 128" \
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
        # This is Fedora container-local /tmp. Rootless Podman stores it under
        # its graph root on /home, so it does not consume the SteamOS rootfs.
        curl -fL "$HEADER_URL" -o "/tmp/$HEADERS_FILENAME"

        BUILD_JOBS="$(nproc)"

        {
            printf "header_package=%s\n" "$HEADERS_FILENAME"
            printf "header_repository=%s\n" "$HEADER_REPO"
            printf "header_url=%s\n" "$HEADER_URL"
            printf "header_sha256=%s\n" "$(sha256sum "/tmp/$HEADERS_FILENAME" | awk "{print \$1}")"
            printf "compiler_version=%s\n" "$(gcc -dumpfullversion -dumpversion)"
            printf "binutils_version=%s\n" "$(ld --version | sed -n "1p")"
            printf "make_version=%s\n" "$(make --version | sed -n "1p")"
        } > /build-state/last-build-environment

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

        {
            printf "build_jobs=%s\n" "$BUILD_JOBS"
            printf "build_target=modules\n"
            printf "build_syssrc=%s\n" "$KERNEL_TREE"
            printf "build_sysout=%s\n" "$KERNEL_TREE"
            printf "kernel_compiler_definition=%s\n" \
                "$(grep -m1 "^#define LINUX_COMPILER " "$KERNEL_TREE/include/generated/compile.h" || true)"
        } >> /build-state/last-build-environment

        printf "\nUsing isolated SteamOS kernel tree:\n"
        printf "  %s\n\n" "$KERNEL_TREE"

        make clean || true

        make modules \
            -j"$BUILD_JOBS" \
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
            vermagic="$(modinfo -F vermagic "$module")"
            vermagic_kernel="${vermagic%% *}"

            printf "  %s -> %s\n" "$(basename "$module")" "$vermagic"

            [[ "$vermagic_kernel" == "$TARGET_KERNEL" ]] || {
                printf "Vermagic mismatch for %s\n" "$(basename "$module")" >&2
                printf "Expected: %s\n" "$TARGET_KERNEL" >&2
                printf "Actual:   %s\n" "$vermagic" >&2
                exit 1
            }
        done
    '

CONTAINER_DIGEST="$(
    podman image inspect "$NVIDIA_BUILD_IMAGE" --format '{{.Digest}}' 2>/dev/null ||
        true
)"
printf 'container_digest=%s\n' "${CONTAINER_DIGEST:-unknown}" >> "$BUILD_ENV_FILE"

mapfile -t MODULES < <(
    find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort
)

(( ${#MODULES[@]} > 0 )) || die "Container build completed but no modules were returned."

echo
for module in "${MODULES[@]}"; do
    ok "Built $(basename "$module")"
done

ok "Contained NVIDIA kernel-module build completed successfully."
