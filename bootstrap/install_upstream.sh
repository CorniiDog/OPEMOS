#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

YES=0
BUILD_ONLY=0
NVIDIA_VERSION=""

usage()
{
    cat <<EOF
Usage: install_upstream.sh [options] NVIDIA_VERSION

Build unmodified NVIDIA open kernel modules in upstream-development mode.

Options:
      --build-only   Preserve the archive and checksum under the project cache;
                     do not install modules, run depmod, or rebuild initramfs.
  -y, --yes          Automatically confirm the requested operation.
  -h, --help         Show this help.

NVIDIA_VERSION must be exact, for example: 580.119.02

Without --build-only, the resulting modules are installed through install.sh.
Project patches are never applied by this upstream-development workflow.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            YES=1
            shift
            ;;
        --build-only)
            BUILD_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            if [[ -z "$NVIDIA_VERSION" ]]; then
                NVIDIA_VERSION="$1"
                shift
            else
                die "Unexpected argument: $1"
            fi
            ;;
    esac
done

[[ "$NVIDIA_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] ||
    die "Exact NVIDIA version required."

require_steamos
if [[ "$BUILD_ONLY" != "1" ]]; then
    need_cmd sudo
    log "Requesting administrator privileges..."
    sudo -v
fi
need_cmd git
need_cmd tar
need_cmd sha256sum
need_cmd modinfo

STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"
CURRENT_NVIDIA="$(get_nvidia_version)"

[[ "$CURRENT_NVIDIA" == "$NVIDIA_VERSION" ]] ||
    die "Installed NVIDIA userspace is ${CURRENT_NVIDIA}; expected ${NVIDIA_VERSION}."

SOURCE_ROOT="${HOME}/.cache/${PROJECT_ID}/upstream"
SOURCE_DIR="${SOURCE_ROOT}/${NVIDIA_VERSION}"

mkdir -p "$SOURCE_ROOT"

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    log "Cloning NVIDIA upstream..."
    git clone "$UPSTREAM_URL" "$SOURCE_DIR"
fi

log "Fetching NVIDIA tag ${NVIDIA_VERSION}..."
git -C "$SOURCE_DIR" fetch --quiet --force origin     "+refs/tags/${NVIDIA_VERSION}:refs/tags/${NVIDIA_VERSION}"

UPSTREAM_COMMIT="$(git -C "$SOURCE_DIR" rev-list -n1 "refs/tags/${NVIDIA_VERSION}")"

[[ -n "$UPSTREAM_COMMIT" ]] ||
    die "Could not resolve upstream tag ${NVIDIA_VERSION}."

git -C "$SOURCE_DIR" checkout --quiet --detach "$UPSTREAM_COMMIT"

SOURCE_VERSION="$(sed -n "s/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p" "${SOURCE_DIR}/version.mk" | head -n1 | tr -d "[:space:]")"

[[ "$SOURCE_VERSION" == "$NVIDIA_VERSION" ]] ||
    die "Source version ${SOURCE_VERSION}; expected ${NVIDIA_VERSION}."

printf "\n"
printf "[%s] Upstream development install\n" "$PROJECT_NAME"
printf "[%s]   SteamOS:       %s\n" "$PROJECT_NAME" "$STEAMOS_VERSION"
printf "[%s]   Kernel:        %s\n" "$PROJECT_NAME" "$KERNEL_VERSION"
printf "[%s]   NVIDIA:        %s\n" "$PROJECT_NAME" "$NVIDIA_VERSION"
printf "[%s]   Provider:      NVIDIA upstream\n" "$PROJECT_NAME"
printf "[%s]   Project fixes: NOT APPLIED\n" "$PROJECT_NAME"
if [[ "$BUILD_ONLY" == "1" ]]; then
    printf "[%s]   Action:        build and preserve artifact only (no sudo)\n" "$PROJECT_NAME"
    printf "[%s]   Kernel modules: unchanged\n" "$PROJECT_NAME"
else
    printf "[%s]   Action:        build and install pristine modules\n" "$PROJECT_NAME"
    printf "[%s]   Kernel modules: replaced after confirmation\n" "$PROJECT_NAME"
fi
printf "\n"

if [[ "$YES" != "1" ]]; then
    if [[ "$BUILD_ONLY" == "1" ]]; then
        PROMPT="Build unmodified upstream-development modules without installing them?"
    else
        PROMPT="Build and install unmodified upstream-development modules?"
    fi

    read -r -p "[$PROJECT_NAME] ${PROMPT} [y/N]: " REPLY
    case "$REPLY" in
        y|Y|yes|YES|Yes) ;;
        *) die "Cancelled." ;;
    esac
fi

mkdir -p "$STATE_DIR"

STATE_BACKUP=""
if [[ -f "$STATE_FILE" ]]; then
        STATE_BACKUP="$(project_mktemp_file upstream-state)"
    cp "$STATE_FILE" "$STATE_BACKUP"
fi

WORK_DIR="$(project_mktemp_dir upstream-install)"

cleanup()
{
    if [[ -n "$STATE_BACKUP" ]]; then
        cp "$STATE_BACKUP" "$STATE_FILE"
        rm -f "$STATE_BACKUP"
    else
        rm -f "$STATE_FILE"
    fi
    rm -rf "$WORK_DIR"
}

trap cleanup EXIT

cat > "$STATE_FILE" <<EOF
source_repo=${SOURCE_DIR}
steamos_version=${STEAMOS_VERSION}
kernel_version=${KERNEL_VERSION}
installed_nvidia=${NVIDIA_VERSION}
source_branch=HEAD
upstream_version=${NVIDIA_VERSION}
upstream_commit=${UPSTREAM_COMMIT}
source_provider=upstream
EOF

log "Building unmodified upstream-development modules..."
"${SCRIPT_DIR}/build.sh"

PACKAGE_DIR="${WORK_DIR}/package"
mkdir -p "${PACKAGE_DIR}/modules"

mapfile -t MODULES < <(
    find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name "*.ko" | sort
)

(( ${#MODULES[@]} > 0 )) ||
    die "No upstream modules were built."

for module in "${MODULES[@]}"; do
    install -m 0644 "$module" "${PACKAGE_DIR}/modules/$(basename "$module")"
done

SUPPORT_COMMIT="$(git -C "$SUPPORT_ROOT" rev-parse HEAD 2>/dev/null || printf 'unknown')"
SUPPORT_DIRTY=0
[[ -z "$(git -C "$SUPPORT_ROOT" status --porcelain 2>/dev/null)" ]] || SUPPORT_DIRTY=1

cat > "${PACKAGE_DIR}/BUILD-INFO.txt" <<EOF
open-gpu-kernel-modules-steamos build information

schema_version=1
built_at=$(date --iso-8601=seconds)
steamos_version=${STEAMOS_VERSION}
kernel_version=${KERNEL_VERSION}
nvidia_version=${NVIDIA_VERSION}
source_repository=NVIDIA/open-gpu-kernel-modules
source_branch=upstream/${NVIDIA_VERSION}
source_commit=${UPSTREAM_COMMIT}
source_dirty=0
nvidia_upstream_commit=${UPSTREAM_COMMIT}
support_repository=${SUPPORT_REPO}
support_commit=${SUPPORT_COMMIT}
support_dirty=${SUPPORT_DIRTY}
source_provider=upstream
project_patches=0
EOF

if [[ -f "${STATE_DIR}/last-build-environment" ]]; then
    printf '\n' >> "${PACKAGE_DIR}/BUILD-INFO.txt"
    cat "${STATE_DIR}/last-build-environment" >> "${PACKAGE_DIR}/BUILD-INFO.txt"
fi

{
    printf '\nmodules:\n'
    for module in "${PACKAGE_DIR}/modules/"*.ko; do
        printf '  %s  %s  vermagic=%s\n' \
            "$(sha256_file "$module")" \
            "$(basename "$module")" \
            "$(modinfo -F vermagic "$module" | awk '{print $1}')"
    done
} >> "${PACKAGE_DIR}/BUILD-INFO.txt"

ARCHIVE="${WORK_DIR}/nvidia-open-upstream-${NVIDIA_VERSION}-${KERNEL_VERSION}.tar.gz"
CHECKSUM="${ARCHIVE}.sha256"

tar -C "$PACKAGE_DIR" -czf "$ARCHIVE" modules BUILD-INFO.txt
sha256sum "$ARCHIVE" > "$CHECKSUM"

if [[ "$BUILD_ONLY" == "1" ]]; then
    OUTPUT_DIR="${HOME}/.cache/${PROJECT_ID}/upstream-builds"
    mkdir -p "$OUTPUT_DIR"

    OUTPUT_ARCHIVE="${OUTPUT_DIR}/$(basename "$ARCHIVE")"
    OUTPUT_CHECKSUM="${OUTPUT_ARCHIVE}.sha256"

    cp "$ARCHIVE" "$OUTPUT_ARCHIVE"
    OUTPUT_SHA="$(sha256sum "$OUTPUT_ARCHIVE" | awk '{print $1}')"
    printf '%s  %s\n' \
        "$OUTPUT_SHA" \
        "$(basename "$OUTPUT_ARCHIVE")" \
        > "$OUTPUT_CHECKSUM"

    ok "Unmodified upstream-development NVIDIA ${NVIDIA_VERSION} modules built."
    printf "[%s] Archive:  %s\n" "$PROJECT_NAME" "$OUTPUT_ARCHIVE"
    printf "[%s] Checksum: %s\n" "$PROJECT_NAME" "$OUTPUT_CHECKSUM"
    warn "Modules were NOT installed."
    exit 0
fi

ARGS=(--archive "$ARCHIVE" --checksum "$CHECKSUM")
[[ "$YES" == "1" ]] && ARGS+=(-y)

"${SCRIPT_DIR}/install.sh" "${ARGS[@]}"

ok "Unmodified upstream-development NVIDIA ${NVIDIA_VERSION} modules installed."
warn "Project fixes are NOT applied."
