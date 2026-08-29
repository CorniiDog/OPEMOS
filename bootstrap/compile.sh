#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

OUTPUT_DIR="$HOME"
AUTO_UPLOAD=0
FORCE_REBUILD=0
YES=0

usage()
{
    printf 'Usage: %s [options]\n\n' "$0"
    printf '  -o, --output DIR    Output directory\n'
    printf '      --auto-upload   Upload/update GitHub release\n'
    printf '      --force-rebuild Ignore matching local bundle\n'
    printf '  -y, --yes           Automatically confirm prompts\n'
    printf '  -h, --help          Show this help\n'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output) [[ $# -ge 2 ]] || die "$1 requires a directory."; OUTPUT_DIR="$2"; shift 2 ;;
        --auto-upload) AUTO_UPLOAD=1; shift ;;
        --force-rebuild) FORCE_REBUILD=1; shift ;;
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

need_cmd git
need_cmd podman
need_cmd tar
need_cmd zip
need_cmd unzip
need_cmd sha256sum
need_cmd modinfo

require_steamos

#
# Upload preflight. Do this before setup/build work so an authentication or
# repository-state problem does not waste a kernel-module compilation.
#
if [[ "$AUTO_UPLOAD" == "1" ]]; then
    if ! command -v gh >/dev/null 2>&1; then
        if [[ "$YES" == "1" ]]; then
            INSTALL_GH_REPLY="y"
        else
            echo
            read -r -p "[${PROJECT_NAME}] GitHub CLI (gh) is not installed. Install it now? [y/N]: " INSTALL_GH_REPLY
        fi

        case "$INSTALL_GH_REPLY" in
            y|Y|yes|YES|Yes)
                need_cmd sudo
                need_cmd pacman

                GH_READONLY_WAS_ENABLED=0

                if command -v steamos-readonly >/dev/null 2>&1 &&
                   steamos-readonly status 2>/dev/null | grep -qi enabled; then
                    log "Disabling SteamOS read-only mode temporarily..."
                    sudo steamos-readonly disable
                    GH_READONLY_WAS_ENABLED=1
                fi

                log "Installing GitHub CLI..."

                if ! sudo pacman -Sy --needed --noconfirm github-cli; then
                    if [[ "$GH_READONLY_WAS_ENABLED" == "1" ]]; then
                        sudo steamos-readonly enable || true
                    fi
                    die "GitHub CLI installation failed."
                fi

                if [[ "$GH_READONLY_WAS_ENABLED" == "1" ]]; then
                    log "Re-enabling SteamOS read-only mode..."
                    sudo steamos-readonly enable
                fi

                command -v gh >/dev/null 2>&1 ||
                    die "GitHub CLI installation completed but gh was not found."
                ;;
            *)
                die "GitHub CLI is required for --auto-upload."
                ;;
        esac
    fi

    if ! gh auth status --hostname github.com >/dev/null 2>&1; then
        log "GitHub authentication is required for --auto-upload."
        echo

        if [[ "$YES" == "1" ]]; then
            log "-y accepted all automatic confirmations."
            log "GitHub account authorization cannot be completed automatically."
        fi

        log "A one-time GitHub browser/device login is required."
        log "Follow the GitHub CLI instructions shown below."
        echo

        gh auth login \
            --hostname github.com \
            --git-protocol https \
            --web

        echo
        log "Verifying GitHub authentication..."

        gh auth status --hostname github.com >/dev/null 2>&1 ||
            die "GitHub authentication was not completed."
    fi

    GH_USERNAME="$(gh api user --jq ".login" 2>/dev/null)" ||
        die "Could not determine the authenticated GitHub account."

    echo
    printf '[%s] Authenticated GitHub account: %s\n' "$PROJECT_NAME" "$GH_USERNAME"
    printf '[%s] Target repository: %s\n' "$PROJECT_NAME" "$SUPPORT_REPO"
    echo

    if [[ "$YES" == "1" ]]; then
        UPLOAD_REPLY="y"
    else
        read -r -p "[${PROJECT_NAME}] Continue with release build/upload? [y/N]: " UPLOAD_REPLY
    fi

    case "$UPLOAD_REPLY" in
        y|Y|yes|YES|Yes) ;;
        *) die "Upload cancelled." ;;
    esac

    if git -C "$SUPPORT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        [[ -z "$(git -C "$SUPPORT_ROOT" status --porcelain)" ]] ||
            die "Support repository working tree is not clean. Commit changes before --auto-upload."
    fi
fi

[[ -f "$STATE_FILE" ]] || "${SCRIPT_DIR}/setup_dev.sh"

SOURCE_DIR="$(state_value source_repo)"
NVIDIA_VERSION="$(state_value installed_nvidia)"
SOURCE_BRANCH="$(state_value source_branch)"
UPSTREAM_COMMIT="$(state_value upstream_commit)"
STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"

[[ -d "${SOURCE_DIR}/.git" ]] || die "Source repository missing: ${SOURCE_DIR}"

CURRENT_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$SOURCE_BRANCH" ]] || die "Source branch is ${CURRENT_BRANCH}; expected ${SOURCE_BRANCH}."

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
SOURCE_DIRTY=0
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || SOURCE_DIRTY=1

if [[ "$AUTO_UPLOAD" == "1" ]]; then
    [[ "$SOURCE_DIRTY" == "0" ]] ||
        die "NVIDIA source working tree is not clean. Commit changes before --auto-upload."
fi

if git -C "$SUPPORT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    SUPPORT_COMMIT="$(git -C "$SUPPORT_ROOT" rev-parse HEAD)"
else
    SUPPORT_COMMIT="unknown"
fi

if [[ "$AUTO_UPLOAD" == "1" ]]; then
    [[ "$SUPPORT_COMMIT" != "unknown" ]] ||
        die "Cannot determine support repository commit."
fi

RELEASE_TAG="$(release_tag)"
ASSET_NAME="$(release_asset)"

CONTAINER_IMAGE_REF="$(podman image inspect "$NVIDIA_BUILD_IMAGE" --format "{{.Digest}}")"
[[ "$CONTAINER_IMAGE_REF" == sha256:* ]] || die "Could not determine immutable build container digest."
CONTAINER_IMAGE_REF="${NVIDIA_BUILD_IMAGE%:*}@${CONTAINER_IMAGE_REF}"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

BUNDLE_NAME="${ASSET_NAME%.tar.gz}.zip"
BUNDLE="${OUTPUT_DIR}/${BUNDLE_NAME}"

WORK_DIR="$(project_mktemp_dir compile)"
trap 'rm -rf "$WORK_DIR"' EXIT

RELEASE_DIR="${WORK_DIR}/release"
PACKAGE_DIR="${WORK_DIR}/package"
mkdir -p "$RELEASE_DIR" "$PACKAGE_DIR/modules"

ARCHIVE="${RELEASE_DIR}/${ASSET_NAME}"
CHECKSUM="${ARCHIVE}.sha256"
BUILD_INFO="${RELEASE_DIR}/${ASSET_NAME%.tar.gz}.build-info.txt"

metadata_value()
{
    grep -m1 "^${2}=" "$1" 2>/dev/null | cut -d= -f2-
}

CACHE_HIT=0

if [[ "$FORCE_REBUILD" == "0" && -f "$BUNDLE" ]]; then
    CACHE_DIR="${WORK_DIR}/cache"
    mkdir -p "$CACHE_DIR"

    if unzip -q "$BUNDLE" -d "$CACHE_DIR"; then
        CACHED_ARCHIVE="${CACHE_DIR}/${ASSET_NAME}"
        CACHED_CHECKSUM="${CACHED_ARCHIVE}.sha256"
        CACHED_INFO="${CACHE_DIR}/${ASSET_NAME%.tar.gz}.build-info.txt"

        if [[ -f "$CACHED_ARCHIVE" && -f "$CACHED_CHECKSUM" && -f "$CACHED_INFO" ]]; then
            CACHED_SOURCE="$(metadata_value "$CACHED_INFO" source_commit)"
            CACHED_KERNEL="$(metadata_value "$CACHED_INFO" kernel_version)"
            CACHED_NVIDIA="$(metadata_value "$CACHED_INFO" nvidia_version)"
            CACHED_CONTAINER="$(metadata_value "$CACHED_INFO" container_image)"
            EXPECTED_SHA="$(awk '{print $1}' "$CACHED_CHECKSUM" | head -n1)"
            ACTUAL_SHA="$(sha256_file "$CACHED_ARCHIVE")"

            if [[ "$CACHED_SOURCE" == "$SOURCE_COMMIT" &&
                  "$CACHED_KERNEL" == "$KERNEL_VERSION" &&
                  "$CACHED_NVIDIA" == "$NVIDIA_VERSION" &&
                  "$CACHED_CONTAINER" == "$CONTAINER_IMAGE_REF" &&
                  "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ &&
                  "${EXPECTED_SHA,,}" == "${ACTUAL_SHA,,}" ]]; then
                CACHE_HIT=1
                ARCHIVE="$CACHED_ARCHIVE"
                CHECKSUM="$CACHED_CHECKSUM"
                BUILD_INFO="$CACHED_INFO"
                ok "Existing bundle matches source, kernel, NVIDIA version, and build image."
                log "Skipping compilation."
            fi
        fi
    fi
fi

if [[ "$CACHE_HIT" == "0" ]]; then
    "${SCRIPT_DIR}/build.sh"

    mapfile -t MODULES < <(find "${SOURCE_DIR}/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort)
    (( ${#MODULES[@]} > 0 )) || die "No built modules found."

    for module in "${MODULES[@]}"; do
        install -m 0644 "$module" "${PACKAGE_DIR}/modules/$(basename "$module")"
    done

    BUILD_TIMESTAMP="$(date --iso-8601=seconds)"

    {
        printf 'open-gpu-kernel-modules-steamos build information\n\n'
        printf 'built_at=%s\n' "$BUILD_TIMESTAMP"
        printf 'steamos_version=%s\n' "$STEAMOS_VERSION"
        printf 'kernel_version=%s\n' "$KERNEL_VERSION"
        printf 'nvidia_version=%s\n' "$NVIDIA_VERSION"
        printf 'release_tag=%s\n' "$RELEASE_TAG"
        printf 'release_asset=%s\n\n' "$ASSET_NAME"

        printf 'source_repository=%s\n' "$SOURCE_REPO"
        printf 'source_branch=%s\n' "$SOURCE_BRANCH"
        printf 'source_commit=%s\n' "$SOURCE_COMMIT"
        printf 'source_dirty=%s\n' "$SOURCE_DIRTY"
        printf 'nvidia_upstream_commit=%s\n' "$UPSTREAM_COMMIT"
        printf 'support_repository=%s\n' "$SUPPORT_REPO"
        printf 'support_commit=%s\n\n' "$SUPPORT_COMMIT"

        printf 'container_image=%s\n\n' "$CONTAINER_IMAGE_REF"

        printf 'modules:\n'
        for module in "${PACKAGE_DIR}/modules/"*.ko; do
            printf '  %s  %s  vermagic=%s\n' \
                "$(sha256_file "$module")" \
                "$(basename "$module")" \
                "$(modinfo -F vermagic "$module" | awk '{print $1}')"
        done
    } > "$BUILD_INFO"

    cp "$BUILD_INFO" "${PACKAGE_DIR}/BUILD-INFO.txt"

    tar -C "$PACKAGE_DIR" -czf "$ARCHIVE" modules BUILD-INFO.txt
    (cd "$RELEASE_DIR" && sha256sum "$ASSET_NAME" > "$(basename "$CHECKSUM")")

    EXPECTED_SHA="$(awk '{print $1}' "$CHECKSUM" | head -n1)"
    ACTUAL_SHA="$(sha256_file "$ARCHIVE")"

    [[ "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ ]] ||
        die "Generated release checksum is invalid."

    [[ "${EXPECTED_SHA,,}" == "${ACTUAL_SHA,,}" ]] ||
        die "Generated release archive failed checksum verification."

    printf 'archive_sha256=%s\n' "$ACTUAL_SHA" >> "$BUILD_INFO"

    rm -f "$BUNDLE"
    (
        cd "$RELEASE_DIR"
        zip -q "$BUNDLE" \
            "$(basename "$ARCHIVE")" \
            "$(basename "$CHECKSUM")" \
            "$(basename "$BUILD_INFO")"
    )

    ok "Build artifacts created."
fi

printf '\n'
printf 'Bundle:           %s\n' "$BUNDLE"
printf 'Archive:          %s\n' "$ARCHIVE"
printf 'Checksum:         %s\n' "$CHECKSUM"
printf 'Build info:       %s\n' "$BUILD_INFO"
printf '\n'
printf 'Source commit:    %s\n' "$SOURCE_COMMIT"
printf 'NVIDIA upstream:  %s\n' "$UPSTREAM_COMMIT"
printf 'Support commit:   %s\n' "$SUPPORT_COMMIT"
printf 'Build container:  %s\n' "$NVIDIA_BUILD_IMAGE"

if [[ "$AUTO_UPLOAD" != "1" ]]; then
    printf '\n'
    log "GitHub upload skipped. Use --auto-upload to publish."
    exit 0
fi

log "Uploading ${RELEASE_TAG} to GitHub..."

RELEASE_TITLE="open-gpu-kernel-modules-steamos - SteamOS ${STEAMOS_VERSION}"

RELEASE_NOTES="Precompiled open-gpu-kernel-modules-steamos build for SteamOS ${STEAMOS_VERSION}.

NVIDIA fork commit: [${SOURCE_COMMIT:0:7}](https://github.com/${SOURCE_REPO}/commit/${SOURCE_COMMIT})
NVIDIA upstream commit: [${UPSTREAM_COMMIT:0:7}](https://github.com/NVIDIA/open-gpu-kernel-modules/commit/${UPSTREAM_COMMIT})
Support commit: [${SUPPORT_COMMIT:0:7}](https://github.com/${SUPPORT_REPO}/commit/${SUPPORT_COMMIT})
Container: ${CONTAINER_IMAGE_REF}"

if gh release view "$RELEASE_TAG" --repo "$SUPPORT_REPO" >/dev/null 2>&1; then
    log "Release already exists; updating metadata and replacing matching artifacts."

    gh release edit "$RELEASE_TAG" \
        --repo "$SUPPORT_REPO" \
        --title "$RELEASE_TITLE" \
        --notes "$RELEASE_NOTES"

    gh release upload "$RELEASE_TAG" \
        "$ARCHIVE" \
        "$CHECKSUM" \
        "$BUILD_INFO" \
        --repo "$SUPPORT_REPO" \
        --clobber
else
    gh release create "$RELEASE_TAG" \
        "$ARCHIVE" \
        "$CHECKSUM" \
        "$BUILD_INFO" \
        --repo "$SUPPORT_REPO" \
        --target "$SUPPORT_COMMIT" \
        --title "$RELEASE_TITLE" \
        --notes "$RELEASE_NOTES"
fi

ok "Release uploaded successfully."
