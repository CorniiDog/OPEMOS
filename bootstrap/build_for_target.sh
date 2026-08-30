#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

STEAMOS_VERSION=""
KERNEL_VERSION=""
NVIDIA_VERSION=""
ARCHITECTURE="x86_64"
SOURCE_DIR=""
HEADERS_PACKAGE=""
HEADERS_URL=""
OUTPUT_DIR=""
RESOLVE_ONLY=0
INSTALL_DEPENDENCIES=0

usage()
{
    cat <<EOF
Usage: build_for_target.sh [options]

Build NVIDIA open kernel modules natively in an x86_64 Fedora appliance for an
offline SteamOS target. The appliance's running kernel is never used.

Required:
      --steamos VERSION       Target SteamOS VERSION_ID
      --kernel VERSION        Exact target kernel release
      --nvidia VERSION        NVIDIA source/userspace version
  -o, --output DIR            Output directory for archive and metadata

Source and headers:
      --source DIR            Existing NVIDIA source checkout. If omitted, clone
                              project branch nvidia/VERSION.
      --headers-package FILE  Exact local Valve headers package
      --headers-url URL       Exact Valve HTTPS headers-package URL

Other:
      --architecture ARCH     Target architecture; currently x86_64 only
      --install-dependencies  Install required packages with sudo dnf
      --resolve-only          Validate/describe inputs without downloading/building
  -h, --help                  Show this help

When neither headers option is supplied, the script derives the exact Valve
headers filename and searches Valve's SteamOS package repositories.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --steamos) [[ $# -ge 2 ]] || die "$1 requires a value."; STEAMOS_VERSION="$2"; shift 2 ;;
        --kernel) [[ $# -ge 2 ]] || die "$1 requires a value."; KERNEL_VERSION="$2"; shift 2 ;;
        --nvidia) [[ $# -ge 2 ]] || die "$1 requires a value."; NVIDIA_VERSION="$2"; shift 2 ;;
        --architecture) [[ $# -ge 2 ]] || die "$1 requires a value."; ARCHITECTURE="$2"; shift 2 ;;
        --source) [[ $# -ge 2 ]] || die "$1 requires a directory."; SOURCE_DIR="$2"; shift 2 ;;
        --headers-package) [[ $# -ge 2 ]] || die "$1 requires a file."; HEADERS_PACKAGE="$2"; shift 2 ;;
        --headers-url) [[ $# -ge 2 ]] || die "$1 requires a URL."; HEADERS_URL="$2"; shift 2 ;;
        -o|--output) [[ $# -ge 2 ]] || die "$1 requires a directory."; OUTPUT_DIR="$2"; shift 2 ;;
        --install-dependencies) INSTALL_DEPENDENCIES=1; shift ;;
        --resolve-only) RESOLVE_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ "$STEAMOS_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    die "--steamos must contain three numeric components."
[[ "$KERNEL_VERSION" =~ ^[A-Za-z0-9._+~-]+$ ]] ||
    die "--kernel contains unsupported characters."
[[ "$NVIDIA_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] ||
    die "--nvidia is not a valid NVIDIA version."
[[ "$ARCHITECTURE" == "x86_64" ]] ||
    die "Only x86_64 target builds are currently supported."
[[ -n "$OUTPUT_DIR" || "$RESOLVE_ONLY" == "1" ]] || die "--output is required."
[[ -z "$HEADERS_PACKAGE" || -z "$HEADERS_URL" ]] ||
    die "--headers-package and --headers-url are mutually exclusive."

NEPTUNE_SERIES="$(get_neptune_series "$KERNEL_VERSION")"
KERNEL_BASE="${KERNEL_VERSION%%-neptune-*}"
KERNEL_PKGREL="$(printf '%s\n' "$KERNEL_BASE" | sed -n 's/.*-\([0-9][0-9]*\)$/\1/p')"
KERNEL_PKGVER="${KERNEL_BASE%-${KERNEL_PKGREL}}"
KERNEL_PKGVER="${KERNEL_PKGVER/-valve/.valve}"
[[ "$KERNEL_PKGREL" =~ ^[0-9]+$ ]] ||
    die "Could not derive the Valve package release from ${KERNEL_VERSION}."

HEADERS_FILENAME="linux-neptune-${NEPTUNE_SERIES}-headers-${KERNEL_PKGVER}-${KERNEL_PKGREL}-x86_64.pkg.tar.zst"
KERNEL_TAG="$(sanitize_release_component "$KERNEL_VERSION")"
RELEASE_TAG="steamos-${STEAMOS_VERSION}-nvidia-${NVIDIA_VERSION}-k${KERNEL_TAG}"
ASSET_NAME="nvidia-open-${RELEASE_TAG}-${ARCHITECTURE}.tar.gz"

if [[ -n "$HEADERS_PACKAGE" ]]; then
    [[ -f "$HEADERS_PACKAGE" ]] || die "Headers package not found: $HEADERS_PACKAGE"
    HEADERS_PACKAGE="$(cd "$(dirname "$HEADERS_PACKAGE")" && pwd)/$(basename "$HEADERS_PACKAGE")"
    [[ "$(basename "$HEADERS_PACKAGE")" == "$HEADERS_FILENAME" ]] ||
        die "Headers filename must be exactly ${HEADERS_FILENAME}."
fi

if [[ -n "$HEADERS_URL" ]]; then
    case "$HEADERS_URL" in
        https://steamdeck-packages.steamos.cloud/archlinux-mirror/*/os/x86_64/"$HEADERS_FILENAME") ;;
        *) die "--headers-url must name the exact package on Valve's SteamOS package host." ;;
    esac
fi

if [[ "$RESOLVE_ONLY" == "1" ]]; then
    python3 - "$STEAMOS_VERSION" "$KERNEL_VERSION" "$NVIDIA_VERSION" \
        "$ARCHITECTURE" "$HEADERS_FILENAME" "$RELEASE_TAG" "$ASSET_NAME" <<'PY'
import json
import sys
keys = ("steamosVersion", "kernelVersion", "nvidiaVersion", "architecture",
        "headersFilename", "releaseTag", "assetName")
print(json.dumps({"schemaVersion": 1, "status": "ready", "target": dict(zip(keys, sys.argv[1:]))},
                 sort_keys=True, separators=(",", ":")))
PY
    exit 0
fi

if [[ "$INSTALL_DEPENDENCIES" == "1" ]]; then
    need_cmd sudo
    need_cmd dnf
    log "Installing Fedora offline-target build dependencies..."
    sudo dnf install -y \
        bc binutils bsdtar curl diffutils elfutils-libelf-devel findutils \
        gcc gcc-c++ git kmod make openssl-devel pahole perl python3 zstd
fi

for command in bash curl find gcc git ld make modinfo nproc python3 readelf sha256sum tar zstd; do
    need_cmd "$command"
done
command -v bsdtar >/dev/null 2>&1 || need_cmd bsdtar
[[ "$(uname -m)" == "x86_64" ]] ||
    die "Native target builds require an x86_64 Fedora appliance; found $(uname -m)."

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
WORK_DIR="$(project_mktemp_dir target-build)"
trap 'rm -rf "$WORK_DIR"' EXIT

if [[ -z "$SOURCE_DIR" ]]; then
    SOURCE_DIR="$WORK_DIR/source"
    log "Cloning project NVIDIA source branch nvidia/${NVIDIA_VERSION}..."
    git clone --quiet --depth 1 --branch "nvidia/${NVIDIA_VERSION}" \
        "$SOURCE_REPO_URL" "$SOURCE_DIR" ||
        die "Project source branch nvidia/${NVIDIA_VERSION} is unavailable."
else
    [[ -d "$SOURCE_DIR" ]] || die "Source directory not found: $SOURCE_DIR"
    SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
fi

[[ -f "$SOURCE_DIR/version.mk" && -f "$SOURCE_DIR/Makefile" && -d "$SOURCE_DIR/kernel-open" ]] ||
    die "Source directory is not an NVIDIA open kernel-module checkout."
SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "$SOURCE_DIR/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$NVIDIA_VERSION" ]] ||
    die "Source version ${SOURCE_VERSION:-unknown} does not match ${NVIDIA_VERSION}."

if [[ -z "$HEADERS_PACKAGE" ]]; then
    if [[ -z "$HEADERS_URL" ]]; then
        MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror"
        log "Discovering exact Valve headers package ${HEADERS_FILENAME}..."
        DISCOVERED="$(curl -fsSL "$MIRROR/" | grep -oE 'href="jupiter-[^"/]*/"' |
            sed -e 's|^href="||' -e 's|/"$||' | grep -vxE 'jupiter-(main|ci-test)' |
            sort -rV | tr '\n' ' ' || true)"
        for repository in jupiter-main $DISCOVERED; do
            candidate="$MIRROR/$repository/os/x86_64/$HEADERS_FILENAME"
            if curl -fsIL -o /dev/null "$candidate"; then
                HEADERS_URL="$candidate"
                break
            fi
        done
        [[ -n "$HEADERS_URL" ]] || die "Exact Valve headers package was not found."
    fi
    HEADERS_PACKAGE="$WORK_DIR/$HEADERS_FILENAME"
    log "Downloading exact Valve headers..."
    curl -fL "$HEADERS_URL" -o "$HEADERS_PACKAGE"
fi

HEADER_SHA256="$(sha256_file "$HEADERS_PACKAGE")"
KERNEL_ROOT="$WORK_DIR/kernel-root"
mkdir -p "$KERNEL_ROOT"
bsdtar -xf "$HEADERS_PACKAGE" -C "$KERNEL_ROOT"
KERNEL_TREE="$KERNEL_ROOT/usr/lib/modules/$KERNEL_VERSION/build"
[[ -d "$KERNEL_TREE" ]] ||
    KERNEL_TREE="$(find "$KERNEL_ROOT/usr/lib/modules" -mindepth 2 -maxdepth 2 -type d -name build | head -n1)"
[[ -n "$KERNEL_TREE" && -f "$KERNEL_TREE/Makefile" ]] ||
    die "Headers package does not contain a kernel build tree."
[[ -f "$KERNEL_TREE/include/generated/autoconf.h" ]] ||
    die "Valve kernel headers are not prepared for external modules."
[[ -f "$KERNEL_TREE/Module.symvers" ]] ||
    die "Valve kernel headers do not contain Module.symvers."

log "Building NVIDIA ${NVIDIA_VERSION} for ${KERNEL_VERSION}..."
make -C "$SOURCE_DIR" clean >/dev/null 2>&1 || true
make -C "$SOURCE_DIR" modules -j"$(nproc)" SYSSRC="$KERNEL_TREE" SYSOUT="$KERNEL_TREE"

mapfile -t MODULES < <(find "$SOURCE_DIR/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort)
validate_nvidia_module_set "${MODULES[@]}" ||
    die "Build did not produce exactly the five expected NVIDIA modules."

PACKAGE_DIR="$WORK_DIR/package"
mkdir -p "$PACKAGE_DIR/modules"
for module in "${MODULES[@]}"; do
    vermagic="$(modinfo -F vermagic "$module")"
    [[ "${vermagic%% *}" == "$KERNEL_VERSION" ]] ||
        die "Vermagic mismatch in $(basename "$module"): ${vermagic}"
    readelf -h "$module" | grep -q 'Machine:.*Advanced Micro Devices X86-64' ||
        die "Module architecture is not x86_64: $(basename "$module")"
    install -m 0644 "$module" "$PACKAGE_DIR/modules/$(basename "$module")"
done

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
BUILD_INFO_NAME="${ASSET_NAME%.tar.gz}.build-info.txt"
BUILD_INFO="$OUTPUT_DIR/$BUILD_INFO_NAME"
ARCHIVE="$OUTPUT_DIR/$ASSET_NAME"
CHECKSUM="$ARCHIVE.sha256"
{
    printf 'open-gpu-kernel-modules-steamos build information\n\n'
    printf 'schema_version=1\n'
    printf 'built_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'steamos_version=%s\n' "$STEAMOS_VERSION"
    printf 'kernel_version=%s\n' "$KERNEL_VERSION"
    printf 'nvidia_version=%s\n' "$NVIDIA_VERSION"
    printf 'release_tag=%s\n' "$RELEASE_TAG"
    printf 'release_asset=%s\n' "$ASSET_NAME"
    printf 'source_repository=%s\n' "$SOURCE_REPO"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'build_mode=offline-target-fedora\n'
    printf 'build_architecture=%s\n' "$ARCHITECTURE"
    printf 'header_package=%s\n' "$HEADERS_FILENAME"
    printf 'header_url=%s\n' "${HEADERS_URL:-local-file}"
    printf 'header_sha256=%s\n' "$HEADER_SHA256"
    printf '\nmodules:\n'
    for module in "$PACKAGE_DIR/modules/"*.ko; do
        printf '  %s  %s  vermagic=%s\n' "$(sha256_file "$module")" \
            "$(basename "$module")" "$(modinfo -F vermagic "$module" | awk '{print $1}')"
    done
} > "$BUILD_INFO"
cp "$BUILD_INFO" "$PACKAGE_DIR/BUILD-INFO.txt"
tar -C "$PACKAGE_DIR" -czf "$ARCHIVE" modules BUILD-INFO.txt
(cd "$OUTPUT_DIR" && sha256sum "$ASSET_NAME" > "$(basename "$CHECKSUM")")

ok "Offline-target NVIDIA artifact created."
printf 'Archive:    %s\nChecksum:   %s\nBuild info: %s\n' "$ARCHIVE" "$CHECKSUM" "$BUILD_INFO"
