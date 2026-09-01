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
HEADERS_SIGNATURE=""
HEADER_KEYRING=""
HEADER_SIGNER=""
OUTPUT_DIR=""
RESULT_JSON=""
RESOLVE_ONLY=0
INSTALL_DEPENDENCIES=0
REQUIRE_COMPILER_MAJOR_MATCH=0
ORIGINAL_ARGS=("$@")

# Locate the result path before normal parsing so even an earlier malformed
# option can return the machine-readable failure contract.
for ((argument_index = 0; argument_index < ${#ORIGINAL_ARGS[@]}; argument_index++)); do
    if [[ "${ORIGINAL_ARGS[$argument_index]}" == "--result-json" &&
          $((argument_index + 1)) -lt ${#ORIGINAL_ARGS[@]} ]]; then
        RESULT_JSON="${ORIGINAL_ARGS[$((argument_index + 1))]}"
        break
    fi
done

fail_argument()
{
    local reason="$1"
    local message="$2"
    if [[ -n "$RESULT_JSON" ]]; then
        python3 "$SUPPORT_ROOT/lib/write_build_result.py" \
            --output "$RESULT_JSON" --status failed --reason "$reason" \
            --message "$message" --trust development-unverified \
            --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
            --nvidia "$NVIDIA_VERSION" --architecture "$ARCHITECTURE" || true
    fi
    die "$message"
}

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
      --headers-signature FILE
                              Detached signature for a local headers package
      --header-keyring FILE   Pinned GPG keyring used only by gpgv
      --header-signer FPR     Exact expected signing-key fingerprint

Other:
      --architecture ARCH     Target architecture; currently x86_64 only
      --install-dependencies  Install required packages with sudo dnf
      --require-compiler-major-match
                              Fail unless the module compiler major matches the
                              compiler recorded by the target kernel
      --result-json FILE      Write a versioned final success/failure result
      --resolve-only          Validate/describe inputs without downloading/building
  -h, --help                  Show this help

When neither headers option is supplied, the script derives the exact Valve
headers filename and searches Valve's SteamOS package repositories.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --steamos) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a value."; STEAMOS_VERSION="$2"; shift 2 ;;
        --kernel) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a value."; KERNEL_VERSION="$2"; shift 2 ;;
        --nvidia) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a value."; NVIDIA_VERSION="$2"; shift 2 ;;
        --architecture) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a value."; ARCHITECTURE="$2"; shift 2 ;;
        --source) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a directory."; SOURCE_DIR="$2"; shift 2 ;;
        --headers-package) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a file."; HEADERS_PACKAGE="$2"; shift 2 ;;
        --headers-url) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a URL."; HEADERS_URL="$2"; shift 2 ;;
        --headers-signature) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a file."; HEADERS_SIGNATURE="$2"; shift 2 ;;
        --header-keyring) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a file."; HEADER_KEYRING="$2"; shift 2 ;;
        --header-signer) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a fingerprint."; HEADER_SIGNER="$2"; shift 2 ;;
        -o|--output) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a directory."; OUTPUT_DIR="$2"; shift 2 ;;
        --install-dependencies) INSTALL_DEPENDENCIES=1; shift ;;
        --require-compiler-major-match) REQUIRE_COMPILER_MAJOR_MATCH=1; shift ;;
        --result-json) [[ $# -ge 2 ]] || fail_argument invalid_target "$1 requires a file."; RESULT_JSON="$2"; shift 2 ;;
        --resolve-only) RESOLVE_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail_argument invalid_target "Unknown argument: $1" ;;
    esac
done

[[ "$STEAMOS_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    fail_argument invalid_target "--steamos must contain three numeric components."
[[ "$KERNEL_VERSION" =~ ^[A-Za-z0-9._+~-]+$ ]] ||
    fail_argument invalid_target "--kernel contains unsupported characters."
[[ "$NVIDIA_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] ||
    fail_argument invalid_target "--nvidia is not a valid NVIDIA version."
[[ "$ARCHITECTURE" == "x86_64" ]] ||
    fail_argument unsupported_architecture "Only x86_64 target builds are currently supported."
[[ -n "$OUTPUT_DIR" || "$RESOLVE_ONLY" == "1" ]] ||
    fail_argument invalid_target "--output is required."
[[ -z "$HEADERS_PACKAGE" || -z "$HEADERS_URL" ]] ||
    fail_argument invalid_target \
        "--headers-package and --headers-url are mutually exclusive."
if [[ -n "$HEADERS_SIGNATURE$HEADER_KEYRING$HEADER_SIGNER" ]]; then
    [[ -n "$HEADER_KEYRING" && -n "$HEADER_SIGNER" ]] ||
        fail_argument invalid_target \
            "Signature verification requires --header-keyring and --header-signer."
    [[ "$HEADER_SIGNER" =~ ^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$ ]] ||
        fail_argument invalid_target \
            "--header-signer must be a full 40- or 64-character fingerprint."
    HEADER_SIGNER="$(printf '%s' "$HEADER_SIGNER" | tr '[:lower:]' '[:upper:]')"
    python3 "$SUPPORT_ROOT/lib/validate_valve_signer.py" \
        --manifest "$SUPPORT_ROOT/trust/valve-package-signers.json" \
        --fingerprint "$HEADER_SIGNER" >/dev/null ||
        fail_argument invalid_target \
            "Header signer is not active in the reviewed trust manifest."
    [[ -f "$HEADER_KEYRING" ]] ||
        fail_argument invalid_target "Pinned header keyring was not found."
fi

NEPTUNE_SERIES="$(printf '%s\n' "$KERNEL_VERSION" |
    sed -n 's/.*-neptune-\([0-9][0-9]*\).*/\1/p')"
[[ -n "$NEPTUNE_SERIES" ]] ||
    fail_argument invalid_target "Could not derive the Neptune series from --kernel."
KERNEL_BASE="${KERNEL_VERSION%%-neptune-*}"
KERNEL_PKGREL="$(printf '%s\n' "$KERNEL_BASE" | sed -n 's/.*-\([0-9][0-9]*\)$/\1/p')"
KERNEL_PKGVER="${KERNEL_BASE%-${KERNEL_PKGREL}}"
KERNEL_PKGVER="${KERNEL_PKGVER/-valve/.valve}"
[[ "$KERNEL_PKGREL" =~ ^[0-9]+$ ]] ||
    fail_argument invalid_target \
        "Could not derive the Valve package release from --kernel."

HEADERS_FILENAME="linux-neptune-${NEPTUNE_SERIES}-headers-${KERNEL_PKGVER}-${KERNEL_PKGREL}-x86_64.pkg.tar.zst"
KERNEL_TAG="$(sanitize_release_component "$KERNEL_VERSION")"
RELEASE_TAG="steamos-${STEAMOS_VERSION}-nvidia-${NVIDIA_VERSION}-k${KERNEL_TAG}"
ASSET_NAME="nvidia-open-${RELEASE_TAG}-${ARCHITECTURE}.tar.gz"

if [[ -n "$HEADERS_PACKAGE" ]]; then
    [[ -f "$HEADERS_PACKAGE" ]] ||
        fail_argument headers_not_found "The requested local headers package was not found."
    HEADERS_PACKAGE="$(cd "$(dirname "$HEADERS_PACKAGE")" && pwd)/$(basename "$HEADERS_PACKAGE")"
    [[ "$(basename "$HEADERS_PACKAGE")" == "$HEADERS_FILENAME" ]] ||
        fail_argument header_identity_mismatch \
            "The local headers filename does not match the exact target."
fi

if [[ -n "$HEADERS_URL" ]]; then
    case "$HEADERS_URL" in
        https://steamdeck-packages.steamos.cloud/archlinux-mirror/*/os/x86_64/"$HEADERS_FILENAME") ;;
        *) fail_argument invalid_target \
            "--headers-url must name the exact package on Valve's SteamOS package host." ;;
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

BUILD_PHASE=dependency_install_failed
BUILD_RESULT_WRITTEN=0
BUILD_COMPLETED=0
WORK_DIR=""
ACTIVE_PROCESS_GROUP=""
BUILD_STARTED_AT="$(date --iso-8601=seconds)"
FINAL_BUILD_INFO=""
FINAL_PROVENANCE=""
FINAL_ARCHIVE=""
FINAL_CHECKSUM=""
FINAL_OUTPUTS_OWNED=0

write_final_result()
{
    local status="$1"
    local reason="$2"
    local message="$3"
    shift 3
    [[ -n "$RESULT_JSON" ]] || return 0
    python3 "$SUPPORT_ROOT/lib/write_build_result.py" \
        --output "$RESULT_JSON" --status "$status" --reason "$reason" \
        --message "$message" --trust "${TRUST_CLASSIFICATION:-development-unverified}" \
        --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --nvidia "$NVIDIA_VERSION" --architecture "$ARCHITECTURE" "$@"
    BUILD_RESULT_WRITTEN=1
}

cleanup_build()
{
    local rc=$?
    [[ -z "$WORK_DIR" ]] || rm -rf "$WORK_DIR"
    if [[ "$BUILD_COMPLETED" == "0" && "$FINAL_OUTPUTS_OWNED" == "1" ]]; then
        [[ -z "$FINAL_BUILD_INFO" ]] || rm -f "$FINAL_BUILD_INFO"
        [[ -z "$FINAL_PROVENANCE" ]] || rm -f "$FINAL_PROVENANCE"
        [[ -z "$FINAL_ARCHIVE" ]] || rm -f "$FINAL_ARCHIVE"
        [[ -z "$FINAL_CHECKSUM" ]] || rm -f "$FINAL_CHECKSUM"
    fi
    if [[ "$BUILD_RESULT_WRITTEN" == "0" && -n "$RESULT_JSON" ]]; then
        if [[ "$rc" == "130" || "$rc" == "143" ]]; then
            write_final_result cancelled cancelled "The offline-target build was cancelled." || true
        elif [[ "$BUILD_COMPLETED" == "0" ]]; then
            write_final_result failed "$BUILD_PHASE" \
                "The offline-target build failed during ${BUILD_PHASE}." || true
        fi
    fi
    return "$rc"
}

terminate_active_process_group()
{
    local attempt
    [[ -n "$ACTIVE_PROCESS_GROUP" ]] || return 0
    kill -TERM -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null || true
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null || return 0
        sleep 0.1
    done
    kill -KILL -- "-${ACTIVE_PROCESS_GROUP}" 2>/dev/null || true
}

cancel_build()
{
    BUILD_PHASE=cancelled
    terminate_active_process_group
    exit 130
}

run_cancellable()
{
    local rc
    python3 "$SUPPORT_ROOT/lib/run_in_process_group.py" "$@" &
    ACTIVE_PROCESS_GROUP=$!
    set +e
    wait "$ACTIVE_PROCESS_GROUP"
    rc=$?
    set -e
    ACTIVE_PROCESS_GROUP=""
    return "$rc"
}

trap cleanup_build EXIT
trap cancel_build INT TERM

if [[ "$INSTALL_DEPENDENCIES" == "1" ]]; then
    need_cmd sudo
    need_cmd dnf
    log "Installing Fedora offline-target build dependencies..."
    sudo dnf install -y \
        bc binutils bsdtar curl diffutils elfutils-libelf-devel findutils \
        gcc gcc-c++ git gnupg2 kmod make openssl-devel pahole perl python3 zstd
fi

for command in bash curl find gcc git ld make modinfo nproc python3 readelf sha256sum tar zstd; do
    need_cmd "$command"
done
command -v bsdtar >/dev/null 2>&1 || need_cmd bsdtar
[[ "$(uname -m)" == "x86_64" ]] ||
    die "Native target builds require an x86_64 Fedora appliance; found $(uname -m)."

BUILD_PHASE=output_preparation_failed
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
WORK_DIR="$(project_mktemp_dir target-build)"
FINAL_BUILD_INFO="$OUTPUT_DIR/${ASSET_NAME%.tar.gz}.build-info.txt"
FINAL_PROVENANCE="$OUTPUT_DIR/${ASSET_NAME%.tar.gz}.provenance.json"
FINAL_ARCHIVE="$OUTPUT_DIR/$ASSET_NAME"
FINAL_CHECKSUM="$OUTPUT_DIR/$ASSET_NAME.sha256"
for final_output in \
    "$FINAL_BUILD_INFO" "$FINAL_PROVENANCE" "$FINAL_ARCHIVE" "$FINAL_CHECKSUM"
do
    [[ ! -e "$final_output" ]] ||
        die "Refusing to overwrite existing output: $(basename "$final_output")"
done
FINAL_OUTPUTS_OWNED=1

BUILD_PHASE=source_branch_missing
if [[ -z "$SOURCE_DIR" ]]; then
    SOURCE_DIR="$WORK_DIR/source"
    log "Cloning project NVIDIA source branch nvidia/${NVIDIA_VERSION}..."
    run_cancellable git clone --quiet --depth 1 --branch "nvidia/${NVIDIA_VERSION}" \
        "$SOURCE_REPO_URL" "$SOURCE_DIR" ||
        die "Project source branch nvidia/${NVIDIA_VERSION} is unavailable."
else
    [[ -d "$SOURCE_DIR" ]] || die "Source directory not found: $SOURCE_DIR"
    SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
fi

[[ -f "$SOURCE_DIR/version.mk" && -f "$SOURCE_DIR/Makefile" && -d "$SOURCE_DIR/kernel-open" ]] ||
    die "Source directory is not an NVIDIA open kernel-module checkout."
BUILD_PHASE=source_version_mismatch
SOURCE_VERSION="$(sed -n 's/^NVIDIA_VERSION[[:space:]]*=[[:space:]]*//p' "$SOURCE_DIR/version.mk" | head -n1 | tr -d '[:space:]')"
[[ "$SOURCE_VERSION" == "$NVIDIA_VERSION" ]] ||
    die "Source version ${SOURCE_VERSION:-unknown} does not match ${NVIDIA_VERSION}."

BUILD_PHASE=headers_not_found
if [[ -z "$HEADERS_PACKAGE" ]]; then
    if [[ -z "$HEADERS_URL" ]]; then
        MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror"
        log "Discovering exact Valve headers package ${HEADERS_FILENAME}..."
        DISCOVERED="$(curl -fsSL "$MIRROR/" |
            valve_repository_names_from_html | tr '\n' ' ' || true)"
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
    BUILD_PHASE=header_download_failed
    run_cancellable curl -fL "$HEADERS_URL" -o "$HEADERS_PACKAGE"
fi

HEADER_SIGNATURE_STATUS=not-verified
HEADER_SIGNER_VALIDATED=""
HEADER_PRIMARY_SIGNER_VALIDATED=""
if [[ -n "$HEADER_KEYRING" ]]; then
    need_cmd gpgv
    if [[ -z "$HEADERS_SIGNATURE" ]]; then
        [[ -n "$HEADERS_URL" ]] || {
            BUILD_PHASE=header_signature_missing
            die "A detached signature is required for a local headers package."
        }
        HEADERS_SIGNATURE="$WORK_DIR/$HEADERS_FILENAME.sig"
        BUILD_PHASE=header_signature_missing
        log "Downloading Valve headers-package signature..."
        run_cancellable curl -fL "${HEADERS_URL}.sig" -o "$HEADERS_SIGNATURE" ||
            die "Detached Valve headers signature was not available."
    else
        [[ -f "$HEADERS_SIGNATURE" ]] || {
            BUILD_PHASE=header_signature_missing
            die "Headers signature not found: $HEADERS_SIGNATURE"
        }
    fi

    BUILD_PHASE=header_signature_invalid
    GPG_STATUS="$(gpgv --status-fd 1 --keyring "$HEADER_KEYRING" \
        "$HEADERS_SIGNATURE" "$HEADERS_PACKAGE")" ||
        die "Valve headers-package signature verification failed."
    HEADER_SIGNER_VALIDATED="$(printf '%s\n' "$GPG_STATUS" |
        sed -n 's/^\[GNUPG:\] VALIDSIG \([0-9A-Fa-f]*\) .*/\1/p' | head -n1 |
        tr '[:lower:]' '[:upper:]')"
    HEADER_PRIMARY_SIGNER_VALIDATED="$(printf '%s\n' "$GPG_STATUS" |
        awk '/^\[GNUPG:\] VALIDSIG / && $NF ~ /^[0-9A-Fa-f]{40,64}$/ {print $NF; exit}' |
        tr '[:lower:]' '[:upper:]')"
    [[ "$HEADER_SIGNER_VALIDATED" == "$HEADER_SIGNER" ||
       "$HEADER_PRIMARY_SIGNER_VALIDATED" == "$HEADER_SIGNER" ]] ||
        die "Headers signature is valid but signer fingerprint does not match the pinned signer."
    HEADER_SIGNATURE_STATUS=verified
fi

BUILD_PHASE=header_identity_mismatch
HEADER_SHA256="$(sha256_file "$HEADERS_PACKAGE")"
PACKAGE_NAME="linux-neptune-${NEPTUNE_SERIES}-headers"
PACKAGE_VERSION="${KERNEL_PKGVER}-${KERNEL_PKGREL}"
PACKAGE_ARCH="$ARCHITECTURE"
python3 "$SUPPORT_ROOT/lib/validate_target_headers.py" package \
    --package "$HEADERS_PACKAGE" --name "$PACKAGE_NAME" \
    --version "$PACKAGE_VERSION" --architecture "$PACKAGE_ARCH" ||
    die "Headers package identity or archive paths failed validation."

KERNEL_ROOT="$WORK_DIR/kernel-root"
mkdir -p "$KERNEL_ROOT"
run_cancellable bsdtar -xf "$HEADERS_PACKAGE" -C "$KERNEL_ROOT" \
    --safe-writes --no-same-owner --no-same-permissions
BUILD_PHASE=header_tree_incomplete
KERNEL_TREE="$(python3 "$SUPPORT_ROOT/lib/validate_target_headers.py" tree \
    --root "$KERNEL_ROOT" --kernel "$KERNEL_VERSION")" ||
    die "Headers package does not contain a safe, prepared exact-kernel build tree."

BUILD_PHASE=compiler_policy_mismatch
KERNEL_COMPILER_DEFINITION="$(grep -m1 \
    '^#define[[:space:]][[:space:]]*LINUX_COMPILER[[:space:]][[:space:]]*' \
    "$KERNEL_TREE/include/generated/compile.h" 2>/dev/null || true)"
KERNEL_COMPILER_VERSION="$(kernel_compiler_version_from_definition \
    "$KERNEL_COMPILER_DEFINITION")"
KERNEL_COMPILER_MAJOR="${KERNEL_COMPILER_VERSION%%.*}"
BUILD_CC="${CC:-gcc}"
BUILD_COMPILER_VERSION="$($BUILD_CC -dumpfullversion -dumpversion)"
BUILD_COMPILER_MAJOR="${BUILD_COMPILER_VERSION%%.*}"

if [[ "$KERNEL_COMPILER_MAJOR" =~ ^[0-9]+$ &&
      "$BUILD_COMPILER_MAJOR" != "$KERNEL_COMPILER_MAJOR" &&
      -x "$(command -v "gcc-${KERNEL_COMPILER_MAJOR}" 2>/dev/null || true)" ]]; then
    BUILD_CC="gcc-${KERNEL_COMPILER_MAJOR}"
    BUILD_COMPILER_VERSION="$($BUILD_CC -dumpfullversion -dumpversion)"
    BUILD_COMPILER_MAJOR="${BUILD_COMPILER_VERSION%%.*}"
    log "Using installed GCC ${BUILD_COMPILER_MAJOR} compatibility compiler."
fi

COMPILER_MAJOR_MATCH=unknown
if [[ "$KERNEL_COMPILER_MAJOR" =~ ^[0-9]+$ ]]; then
    COMPILER_MAJOR_MATCH=0
    [[ "$BUILD_COMPILER_MAJOR" == "$KERNEL_COMPILER_MAJOR" ]] && COMPILER_MAJOR_MATCH=1
fi
if [[ "$REQUIRE_COMPILER_MAJOR_MATCH" == "1" && "$COMPILER_MAJOR_MATCH" != "1" ]]; then
    die "Build compiler ${BUILD_COMPILER_VERSION} does not match target kernel compiler major ${KERNEL_COMPILER_MAJOR:-unknown}."
fi
if [[ "$COMPILER_MAJOR_MATCH" == "0" ]]; then
    warn "Target kernel used GCC ${KERNEL_COMPILER_VERSION:-unknown}; modules will use ${BUILD_CC} ${BUILD_COMPILER_VERSION}."
    warn "The artifact will remain development-unverified."
fi

log "Building NVIDIA ${NVIDIA_VERSION} for ${KERNEL_VERSION}..."
BUILD_PHASE=compilation_failed
make -C "$SOURCE_DIR" clean >/dev/null 2>&1 || true
run_cancellable make -C "$SOURCE_DIR" modules -j"$(nproc)" CC="$BUILD_CC" \
    SYSSRC="$KERNEL_TREE" SYSOUT="$KERNEL_TREE"

MODULES=()
while IFS= read -r module; do
    MODULES+=("$module")
done < <(find "$SOURCE_DIR/kernel-open" -maxdepth 1 -type f -name '*.ko' | sort)
MODULE_VALIDATION_JSON="$WORK_DIR/module-validation.json"
set +e
python3 "$SUPPORT_ROOT/lib/validate_built_modules.py" \
    --kernel "$KERNEL_VERSION" --nvidia "$NVIDIA_VERSION" \
    --architecture "$ARCHITECTURE" --output "$MODULE_VALIDATION_JSON" \
    "${MODULES[@]}"
MODULE_VALIDATION_RC=$?
set -e
if [[ "$MODULE_VALIDATION_RC" != "0" ]]; then
    BUILD_PHASE="$(python3 -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["reason"])' \
        "$MODULE_VALIDATION_JSON" 2>/dev/null || printf module_metadata_invalid)"
    die "Built NVIDIA modules failed structural validation."
fi

BUILD_PHASE=packaging_failed
PACKAGE_DIR="$WORK_DIR/package"
mkdir -p "$PACKAGE_DIR/modules"
for module in "${MODULES[@]}"; do
    install -m 0644 "$module" "$PACKAGE_DIR/modules/$(basename "$module")"
done

SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
SOURCE_BRANCH="$(git -C "$SOURCE_DIR" branch --show-current 2>/dev/null || true)"
SOURCE_DIRTY=unknown
if git -C "$SOURCE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    SOURCE_DIRTY=0
    [[ -z "$(git -C "$SOURCE_DIR" status --porcelain)" ]] || SOURCE_DIRTY=1
fi
SUPPORT_COMMIT="$(git -C "$SUPPORT_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
SUPPORT_DIRTY=unknown
if git -C "$SUPPORT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    SUPPORT_DIRTY=0
    [[ -z "$(git -C "$SUPPORT_ROOT" status --porcelain)" ]] || SUPPORT_DIRTY=1
fi
BUILD_OS="$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | head -n1 | tr -d '"')"
TRUST_CLASSIFICATION=development-unverified
if [[ "$HEADER_SIGNATURE_STATUS" == "verified" &&
      "$COMPILER_MAJOR_MATCH" == "1" &&
      "$SOURCE_DIRTY" == "0" && "$SUPPORT_DIRTY" == "0" &&
      "$SOURCE_COMMIT" != "unknown" && "$SUPPORT_COMMIT" != "unknown" ]]; then
    TRUST_CLASSIFICATION=locally-built-verified
fi
BUILD_INFO_NAME="${ASSET_NAME%.tar.gz}.build-info.txt"
PROVENANCE_NAME="${ASSET_NAME%.tar.gz}.provenance.json"
STAGED_OUTPUT="$WORK_DIR/final-output"
mkdir -p "$STAGED_OUTPUT"
BUILD_INFO="$STAGED_OUTPUT/$BUILD_INFO_NAME"
PROVENANCE="$STAGED_OUTPUT/$PROVENANCE_NAME"
ARCHIVE="$STAGED_OUTPUT/$ASSET_NAME"
CHECKSUM="$STAGED_OUTPUT/$ASSET_NAME.sha256"
{
    printf 'open-gpu-kernel-modules-steamos build information\n\n'
    printf 'schema_version=1\n'
    printf 'build_started_at=%s\n' "$BUILD_STARTED_AT"
    printf 'build_completed_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'steamos_version=%s\n' "$STEAMOS_VERSION"
    printf 'kernel_version=%s\n' "$KERNEL_VERSION"
    printf 'nvidia_version=%s\n' "$NVIDIA_VERSION"
    printf 'release_tag=%s\n' "$RELEASE_TAG"
    printf 'release_asset=%s\n' "$ASSET_NAME"
    printf 'source_repository=%s\n' "$SOURCE_REPO"
    printf 'source_branch=%s\n' "${SOURCE_BRANCH:-detached}"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'source_dirty=%s\n' "$SOURCE_DIRTY"
    printf 'support_repository=%s\n' "$SUPPORT_REPO"
    printf 'support_commit=%s\n' "$SUPPORT_COMMIT"
    printf 'support_dirty=%s\n' "$SUPPORT_DIRTY"
    printf 'build_mode=offline-target-fedora\n'
    printf 'build_architecture=%s\n' "$ARCHITECTURE"
    printf 'build_os=%s\n' "${BUILD_OS:-unknown}"
    printf 'trust_classification=%s\n' "$TRUST_CLASSIFICATION"
    printf 'compiler_command=%s\n' "$BUILD_CC"
    printf 'compiler_version=%s\n' "$BUILD_COMPILER_VERSION"
    printf 'kernel_compiler_version=%s\n' "${KERNEL_COMPILER_VERSION:-unknown}"
    printf 'compiler_major_match=%s\n' "$COMPILER_MAJOR_MATCH"
    printf 'kernel_compiler_definition=%s\n' "${KERNEL_COMPILER_DEFINITION:-unknown}"
    printf 'binutils_version=%s\n' "$(ld --version | sed -n '1p')"
    printf 'make_version=%s\n' "$(make --version | sed -n '1p')"
    printf 'kmod_version=%s\n' "$(modinfo --version 2>&1 | sed -n '1p')"
    printf 'header_package=%s\n' "$HEADERS_FILENAME"
    printf 'header_url=%s\n' "${HEADERS_URL:-local-file}"
    printf 'header_sha256=%s\n' "$HEADER_SHA256"
    printf 'header_package_name=%s\n' "$PACKAGE_NAME"
    printf 'header_package_version=%s\n' "$PACKAGE_VERSION"
    printf 'header_package_architecture=%s\n' "$PACKAGE_ARCH"
    printf 'header_signature_status=%s\n' "$HEADER_SIGNATURE_STATUS"
    printf 'header_signing_key_fingerprint=%s\n' "${HEADER_SIGNER_VALIDATED:-not-verified}"
    printf 'header_primary_key_fingerprint=%s\n' "${HEADER_PRIMARY_SIGNER_VALIDATED:-not-reported}"
    if [[ "$HEADER_SIGNATURE_STATUS" == "verified" ]]; then
        printf 'header_authentication=detached-signature-verified-with-pinned-keyring\n'
    else
        printf 'header_authentication=https-transport-or-local-input-not-signature-verified\n'
    fi
    printf '\nmodules:\n'
    for module in "$PACKAGE_DIR/modules/"*.ko; do
        printf '  %s  %s  version=%s  architecture=x86_64  vermagic=%s\n' \
            "$(sha256_file "$module")" "$(basename "$module")" \
            "$(modinfo -F version "$module")" "$(modinfo -F vermagic "$module")"
    done
} > "$BUILD_INFO"
cp "$BUILD_INFO" "$PACKAGE_DIR/BUILD-INFO.txt"
python3 "$SUPPORT_ROOT/lib/write_build_provenance.py" \
    --build-info "$BUILD_INFO" --modules "$MODULE_VALIDATION_JSON" \
    --output "$PROVENANCE"
cp "$PROVENANCE" "$PACKAGE_DIR/PROVENANCE.json"
tar -C "$PACKAGE_DIR" -czf "$ARCHIVE" modules BUILD-INFO.txt PROVENANCE.json
(cd "$STAGED_OUTPUT" && sha256sum "$ASSET_NAME" > "$(basename "$CHECKSUM")")

ARCHIVE_SHA256="$(sha256_file "$ARCHIVE")"
mv "$BUILD_INFO" "$FINAL_BUILD_INFO"
mv "$PROVENANCE" "$FINAL_PROVENANCE"
mv "$ARCHIVE" "$FINAL_ARCHIVE"
mv "$CHECKSUM" "$FINAL_CHECKSUM"
BUILD_INFO="$FINAL_BUILD_INFO"
PROVENANCE="$FINAL_PROVENANCE"
ARCHIVE="$FINAL_ARCHIVE"
CHECKSUM="$FINAL_CHECKSUM"
write_final_result success build_complete "The offline-target artifact passed structural validation." \
    --archive "$(basename "$ARCHIVE")" --checksum "$(basename "$CHECKSUM")" \
    --build-info "$(basename "$BUILD_INFO")" \
    --provenance "$(basename "$PROVENANCE")" --archive-sha256 "$ARCHIVE_SHA256"
BUILD_COMPLETED=1

ok "Offline-target NVIDIA artifact created."
printf 'Archive:    %s\nChecksum:   %s\nBuild info: %s\nProvenance: %s\n' \
    "$ARCHIVE" "$CHECKSUM" "$BUILD_INFO" "$PROVENANCE"
