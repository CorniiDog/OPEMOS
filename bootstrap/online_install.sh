#!/usr/bin/env bash
set -euo pipefail

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/OPEMOS}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"

FUZZY=0
IN_CODE=0
LOCAL_SOURCE=""
YES=0
RESOLVE_ONLY=0
BUILD_AS_FALLBACK=0
INTERSTITIAL_BINARY=""
INTERSTITIAL_SHA256=""
# Never inherit a caller's unrelated build-output or retained product identity.
BUILD_OUT=""
RETAINED_PRODUCT=""

usage()
{
    cat <<EOF
Usage: online_install.sh [options]

Options:
      --fuzzy        Compatibility alias; certified installs already allow
                     bounded fallback to older same-series SteamOS releases
      --local PATH   Install an explicitly supplied local bundle/archive
      --in-code      Compile the current NVIDIA source working tree, then install it
      --build-as-fallback    Build a reviewed exact target only if no product exists
      --resolve-only Describe the authorized Automatic action without installation
      --interstitial-binary FILE --interstitial-sha256 SHA256
                     Existing authenticated interstitial input for first app provisioning
  -y, --yes          Automatically confirm installer prompts
  -h, --help         Show this help

Normal and --fuzzy installs use published release artifacts only.
--build-as-fallback permits a reviewed exact-target build using Podman on absence.
--local and --in-code are explicit development/testing paths.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fuzzy) FUZZY=1; shift ;;
        --in-code) IN_CODE=1; shift ;;
        --build-as-fallback) BUILD_AS_FALLBACK=1; shift ;;
        --resolve-only) RESOLVE_ONLY=1; shift ;;
        --local) [[ $# -ge 2 ]] || { echo "--local requires a path." >&2; exit 1; }; LOCAL_SOURCE="$2"; shift 2 ;;
        --interstitial-binary) [[ $# -ge 2 ]] || exit 2; INTERSTITIAL_BINARY="$2"; shift 2 ;;
        --interstitial-sha256) [[ $# -ge 2 ]] || exit 2; INTERSTITIAL_SHA256="$2"; shift 2 ;;
        -y|--yes) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 1 ;;
    esac
done

MODE_COUNT=0
[[ "$FUZZY" == "1" ]] && MODE_COUNT=$((MODE_COUNT + 1))
[[ "$IN_CODE" == "1" ]] && MODE_COUNT=$((MODE_COUNT + 1))
[[ -n "$LOCAL_SOURCE" ]] && MODE_COUNT=$((MODE_COUNT + 1))
(( MODE_COUNT <= 1 )) || { echo "--fuzzy, --local, and --in-code are mutually exclusive." >&2; exit 1; }

(( BUILD_AS_FALLBACK == 0 || IN_CODE == 0 )) && [[ "$BUILD_AS_FALLBACK" == 0 || -z "$LOCAL_SOURCE" ]] || { echo "--build-as-fallback cannot be combined with development modes." >&2; exit 1; }
FALLBACK_ARGS=()
[[ "$BUILD_AS_FALLBACK" == 0 ]] || FALLBACK_ARGS=(--build-as-fallback)

need()
{
    command -v "$1" >/dev/null 2>&1 || { printf 'Missing command: %s\n' "$1" >&2; exit 1; }
}

need git
need curl
need python3
if [[ "$RESOLVE_ONLY" == 0 ]]; then
    need tar
    need sha256sum
    need zstd
    need modinfo
    need realpath
fi

PENDING_CORE_PIN=""
if [[ "$RESOLVE_ONLY" == 0 && "$IN_CODE" == 0 && -z "$LOCAL_SOURCE" && "$SUPPORT_REPO" == CorniiDog/OPEMOS ]]; then
    PENDING_CORE_PIN="$(sudo python3 - <<'PYPIN'
import json,os,re,stat
from pathlib import Path
home=Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')
p=home/'pending-install.json'
if os.path.lexists(p):
    info=home.lstat()
    if home.resolve()!=home or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
        raise SystemExit('Unsafe pending install parent')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600 or not 0<info.st_size<=65536):
            raise SystemExit('Unsafe pending install metadata')
        data=os.read(fd,65537)
        if len(data)!=info.st_size:raise SystemExit('Pending install read changed')
    finally:os.close(fd)
    record=json.loads(data)
    if (json.dumps(record,sort_keys=True,separators=(',',':'))+'\n').encode()!=data:
        raise SystemExit('Noncanonical pending install metadata')
    revision=record['expected']['revision']
    if re.fullmatch('[0-9a-f]{40}',revision) is None:raise SystemExit('Invalid pending Core pin')
    print(revision)
PYPIN
)" || { echo "Pending install Core identity unavailable; no discovery substitution." >&2; exit 1; }
    if [[ -n "$PENDING_CORE_PIN" ]]; then
        [[ -z "${SUPPORT_REVISION:-}" || "$SUPPORT_REVISION" == "$PENDING_CORE_PIN" ]] || {
            echo "Explicit Core pin conflicts with pending install; preserved." >&2; exit 1;
        }
        SUPPORT_REVISION="$PENDING_CORE_PIN"
    fi
fi

if [[ -n "${SUPPORT_REVISION:-}" ]]; then
    SUPPORT_REV="$SUPPORT_REVISION"
else
    SUPPORT_REV="$(git ls-remote "https://github.com/${SUPPORT_REPO}.git" "refs/heads/${SUPPORT_BRANCH}" | awk 'NR==1 {print $1}')"
fi
[[ "$SUPPORT_REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve support revision." >&2; exit 1; }
SUPPORT_REV="${SUPPORT_REV,,}"

# common.sh is not available until the support repository is cloned, so this
# bootstrap entry point must create its cache-rooted temporary directory itself.
if [[ -n "${OPEMOS_RECOVERY_PLAN_FILE:-}" ]]; then
    # The installed service's HOME may be on SteamOS's immutable root.
    RECOVERY_WORK_ROOT="$(python3 - <<'PY'
import os, stat
from pathlib import Path
prefix = os.environ.get('PROJECT_TEST_ROOT', '') if os.environ.get('PROJECT_TEST_MODE') == '1' else ''
parent = Path(prefix + '/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')
expected_owner = os.geteuid() if prefix else 0
info = parent.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_owner or info.st_mode & 0o022:
    raise SystemExit('Persistent recovery workspace parent is unsafe')
root = parent / 'workspaces'
root.mkdir(mode=0o700, exist_ok=True)
info = root.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit('Persistent recovery workspace is unsafe')
print(root)
PY
    )"
    export TMPDIR="$RECOVERY_WORK_ROOT"
    TMP="$(mktemp -d "$RECOVERY_WORK_ROOT/opemos-online-install.XXXXXX")"
else
    mkdir -p "${HOME}/.cache/open-gpu-kernel-modules-steamos-support"
    TMP="$(mktemp -d "${HOME}/.cache/open-gpu-kernel-modules-steamos-support/online-install.XXXXXX")"
fi
ACTIVE_BUILD=""
cleanup()
{
    local rc=$?
    if [[ -n "$ACTIVE_BUILD" ]]; then
        kill -TERM -- "-$ACTIVE_BUILD" 2>/dev/null || true
        for attempt in {1..20}; do
            kill -0 -- "-$ACTIVE_BUILD" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL -- "-$ACTIVE_BUILD" 2>/dev/null || true
        wait "$ACTIVE_BUILD" 2>/dev/null || true
    fi
    rm -rf "$TMP"
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CORE_BUNDLE_CACHE="${HOME}/.cache/open-gpu-kernel-modules-steamos-support/core-checkouts"
if git clone --quiet --depth 1 "https://github.com/${SUPPORT_REPO}.git" "$TMP/support" &&
   git -C "$TMP/support" fetch --quiet --depth 1 origin "$SUPPORT_REV" &&
   git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"; then
    :
else
    CORE_ACQUISITION_STATUS=$?
    [[ ( "$BUILD_AS_FALLBACK" == 1 || -n "$PENDING_CORE_PIN" ) && "$SUPPORT_REPO" == CorniiDog/OPEMOS &&
       -n "${SUPPORT_REVISION:-}" ]] || {
        echo "Could not acquire the exact Core checkout." >&2; exit "$CORE_ACQUISITION_STATUS";
    }
    # Snapshot data only, before any cached code executes. A bundle carries Git
    # objects, never repository configuration or hooks. The caller's exact pin
    # remains the authority; no cached mutable branch is selected.
    python3 - "$CORE_BUNDLE_CACHE" "$SUPPORT_REV" "$TMP/core.bundle" <<'PY'
import os, stat, sys
from pathlib import Path
parent = Path(sys.argv[1])
info = parent.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit('Core checkout cache directory is unsafe')
path = parent / (sys.argv[2] + '.bundle')
descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    before = os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
            or not 0 < before.st_size <= 128 * 1024 * 1024):
        raise SystemExit('Core checkout bundle is unsafe')
    payload = bytearray()
    while len(payload) <= before.st_size:
        chunk = os.read(descriptor, min(1024 * 1024, before.st_size + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                info.st_uid, info.st_gid, info.st_mode, info.st_nlink)
    if len(payload) != before.st_size or identity(before) != identity(os.fstat(descriptor)) or identity(before) != identity(path.lstat()):
        raise SystemExit('Core checkout bundle changed while read')
    with open(sys.argv[3], 'xb') as output:
        output.write(payload)
finally:
    os.close(descriptor)
PY
    [[ ! -e "$TMP/support" && ! -L "$TMP/support" ]] || mv "$TMP/support" "$TMP/network-checkout"
    git -c pack.threads=1 clone --quiet --no-checkout "$TMP/core.bundle" "$TMP/support"
    git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"
    echo "Restored the explicitly pinned Core checkout from retained Git objects." >&2
fi
[[ "$(git -C "$TMP/support" rev-parse HEAD)" == "$SUPPORT_REV" ]] || {
    echo "Fetched Core checkout does not match the requested revision." >&2
    exit 1
}

source "$TMP/support/lib/common.sh"

require_steamos

STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"

if [[ "$RESOLVE_ONLY" == 1 ]]; then
    (( MODE_COUNT == 0 )) || die "--resolve-only describes Automatic mode only."
    curl -fsSL --retry 2 \
        "https://api.github.com/repos/${SUPPORT_REPO}/releases?per_page=100" \
        -o "$TMP/releases.json" || die "Failed to query published releases."
    python3 "$TMP/support/lib/automatic_install_selection.py" \
        --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --releases "$TMP/releases.json" --repository "$SUPPORT_REPO" "${FALLBACK_ARGS[@]}"
    exit 0
fi

NVIDIA_VERSION=""

if NVIDIA_VERSION="$(get_nvidia_version 2>/dev/null)"; then
    log "Existing NVIDIA userspace detected: ${NVIDIA_VERSION}"
else
    log "NVIDIA userspace is not installed."
    log "Running SteamOS NVIDIA userspace setup..."

    SETUP_ARGS=("${FALLBACK_ARGS[@]}")
    [[ "$YES" == "1" ]] && SETUP_ARGS+=(-y)

    "$TMP/support/bootstrap/setup_nvidia.sh" "${SETUP_ARGS[@]}"

    NVIDIA_VERSION="$(get_nvidia_version)"
fi

if [[ -n "${OPEMOS_PINNED_NVIDIA_VERSION:-}" &&
      "$NVIDIA_VERSION" != "$OPEMOS_PINNED_NVIDIA_VERSION" ]]; then
    die "Resolved NVIDIA userspace ${NVIDIA_VERSION} differs from pinned recovery policy ${OPEMOS_PINNED_NVIDIA_VERSION}."
fi

KERNEL_TAG="$(sanitize_release_component "$KERNEL_VERSION")"

printf '[%s] SteamOS: %s\n' "$PROJECT_NAME" "$STEAMOS_VERSION"
printf '[%s] Kernel:  %s\n' "$PROJECT_NAME" "$KERNEL_VERSION"
printf '[%s] NVIDIA:  %s\n' "$PROJECT_NAME" "$NVIDIA_VERSION"

INSTALL_CHANGED=0

# Hash the actual kernel module contents, independent of on-disk compression.
# Release archives may contain raw .ko files while installed modules use .ko.zst.

already_installed()
{
    local archive="$1"
    local checksum="$2"
    local expected_sha actual_sha entry listing
    local state_root
    local installed_info target_dir
    state_root="$(project_system_path "/var/lib/open-gpu-kernel-modules-steamos-support")"
    installed_info="${state_root}/installed-build-info.txt"
    target_dir="$(project_system_path "/usr/lib/modules/${KERNEL_VERSION}/updates/open-gpu-kernel-modules-steamos")"
    local check_dir="${TMP}/installed-check"
    local resolved resolved_real target_real module module_name installed module_sha installed_sha
    local installed_module_count
    local checked_modules=0
    local checked_module_names=()

    [[ -f "$installed_info" && -d "$target_dir" ]] || return 1

    expected_sha="$(awk '{print $1}' "$checksum" | head -n1)"

    [[ "$expected_sha" =~ ^[0-9a-fA-F]{64}$ ]] ||
        return 1

    actual_sha="$(sha256sum "$archive" | awk '{print $1}')"

    strings_equal_case_insensitive "$expected_sha" "$actual_sha" ||
        return 1

    listing="${check_dir}.listing"

    tar -tzf "$archive" > "$listing" || {
        rm -f "$listing"
        return 1
    }

    while IFS= read -r entry; do
        [[ "$entry" != /* ]] || {
            rm -f "$listing"
            return 1
        }

        [[ "$entry" != ".." &&
           "$entry" != ../* &&
           "$entry" != */../* &&
           "$entry" != */.. ]] || {
            rm -f "$listing"
            return 1
        }
    done < "$listing"

    rm -f "$listing"

    rm -rf "$check_dir"
    mkdir -p "$check_dir"

    tar -xzf "$archive" -C "$check_dir"

    [[ -f "$check_dir/BUILD-INFO.txt" && -d "$check_dir/modules" ]] ||
        return 1

    cmp -s "$check_dir/BUILD-INFO.txt" "$installed_info" ||
        return 1

    resolved="$(modinfo -n nvidia 2>/dev/null || true)"
    [[ -n "$resolved" ]] || return 1

    resolved_real="$(canonicalize_path "$resolved")"
    target_real="$(canonicalize_path "$target_dir")"

    case "$resolved_real" in
        "$target_real"/*) ;;
        *) return 1 ;;
    esac

    while IFS= read -r module; do
        [[ -f "$module" ]] || return 1
        checked_modules=$((checked_modules + 1))

        module_name="$(basename "$module")"
        module_name="${module_name%.zst}"
        checked_module_names+=("$module_name")

        if [[ -f "$target_dir/${module_name}.zst" ]]; then
            installed="$target_dir/${module_name}.zst"
        elif [[ -f "$target_dir/${module_name}" ]]; then
            installed="$target_dir/${module_name}"
        else
            return 1
        fi

        module_sha="$(module_content_sha256 "$module")" || return 1
        installed_sha="$(module_content_sha256 "$installed")" || return 1

        [[ "$module_sha" == "$installed_sha" ]] ||
            return 1
    done < <(find "$check_dir/modules" -maxdepth 1 -type f \( -name '*.ko' -o -name '*.ko.zst' \) -print | sort)

    validate_nvidia_module_set "${checked_module_names[@]}" || return 1

    installed_module_count="$(
        find "$target_dir" -maxdepth 1 -type f \
            \( -name '*.ko' -o -name '*.ko.zst' \) -print |
            wc -l
    )"

    [[ "$installed_module_count" == "5" ]] || return 1

    return 0
}

offer_reboot()
{
    [[ "$INSTALL_CHANGED" == "1" ]] || return 0

    if [[ "$YES" == "1" ]]; then
        log "Restart deferred to the noninteractive caller."
        return 0
    fi

    echo
    read -r -p "[$PROJECT_NAME] Restart the system now? [y/N]: " REBOOT_REPLY

    case "$REBOOT_REPLY" in
        y|Y|yes|YES|Yes)
            log "Restarting system..."
            rm -rf "$TMP"
            trap - EXIT
            sudo reboot
            ;;
        *)
            log "Restart skipped."
            ;;
    esac
}

install_archive()
{
    local archive="$1"
    local checksum="$2"
    local fuzzy_flag="${3:-0}"
    # Intentional guardian pending75 exits this entire set-e producer. Custody
    # must cross that boundary BEFORE app activation, not in its success tail.
    if [[ -z "$RETAINED_PRODUCT" ]]; then
        [[ -n "$BUILD_OUT" && -f "$BUILD_OUT/materialization.json" ]] ||
            canonicalize_product "$archive" "$checksum" "$fuzzy_flag"
        retain_built_product
    fi
    retain_core_checkout
    local paths
    paths="$(sudo python3 "$TMP/support/lib/recovery_cached_product.py" show \
        --directory "$RETAINED_PRODUCT" --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")"
    archive="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["paths"]["archive"])' "$paths")"
    checksum="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["paths"]["checksum"])' "$paths")"
    local home
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    # A repeated already-loaded invocation must not create a new pending first
    # install. Disk equality alone cannot establish this health decision.
    local candidate_sha
    candidate_sha="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["outputs"]["archive"]["sha256"])' "$paths")"
    if ! sudo test -e "$home/pending-install.json" &&
       sudo python3 - "$TMP/support/lib" "$candidate_sha" <<'PYLOADED'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from installed_driver_candidate import reconcile_controller
home=Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')
result=reconcile_controller(Path('/'),home,inspect_only=True)
from install_guardian_generation import registered
import installed_boot_guard as guard
entry=registered()['driver']
if entry is None:raise SystemExit(1)
engine,store=guard.engine(entry)
if engine.load_intent(Path('/'),store)['authority'].get('archiveSHA256')!=sys.argv[2]:raise SystemExit(1)
if not (result.get('state')=='healthy' and result.get('action') in ('update','disable-fallback')
        and result.get('currentRuntimeMatches') is True):raise SystemExit(1)
PYLOADED
    then
        install_recovery_guardian
        INSTALL_CHANGED=0
        log "Exact product is already loaded and boot-verified."
        return 0
    fi
    retain_install_inputs
    install_recovery_guardian
    local args=(--archive "$archive" --checksum "$checksum" --cache "$RETAINED_PRODUCT")
    [[ "$fuzzy_flag" == "1" ]] && args+=(--fuzzy)
    [[ "$YES" == "1" ]] && args+=(-y)

    if [[ -f "$(project_system_path "/var/lib/open-gpu-kernel-modules-steamos-support/installed-build-info.txt")" ]]; then
        log "Existing NVIDIA open kernel module installation requires update or repair."
    fi

    # Cache and retained interstitial are root-private. Enter the actual
    # existing owning installer as UID0 before its first realpath/read, rather
    # than widening permissions or expecting a later sudo to repair the read.
    sudo env OPEMOS_PINNED_NVIDIA_VERSION="$NVIDIA_VERSION" \
        /bin/bash "$TMP/support/bootstrap/install.sh" "${args[@]}"
    INSTALL_CHANGED=1
}

retain_core_checkout()
{
    [[ "$SUPPORT_REPO" == CorniiDog/OPEMOS ]] || return 0
    # Preserve an existing entry, including an invalid one. Replacement is a
    # separate creator-ownership decision, never a side effect of installation.
    [[ ! -e "$CORE_BUNDLE_CACHE/$SUPPORT_REV.bundle" && ! -L "$CORE_BUNDLE_CACHE/$SUPPORT_REV.bundle" ]] || return 0
    git -c pack.threads=1 -C "$TMP/support" bundle create "$TMP/retained-core.bundle" HEAD
    python3 - "$TMP/support/lib" "$TMP/retained-core.bundle" "$CORE_BUNDLE_CACHE" "$SUPPORT_REV" <<'PY'
import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from atomic_output import atomic_create_bytes
from recovery_cached_product import read_regular
payload = read_regular(sys.argv[2], 128 * 1024 * 1024, 'Core checkout bundle')
parent = Path(sys.argv[3])
parent.mkdir(mode=0o700, exist_ok=True)
info = parent.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit('Core checkout cache directory is unsafe')
try:
    atomic_create_bytes(parent / (sys.argv[4] + '.bundle'), payload, mode=0o600)
except FileExistsError:
    pass  # A concurrent creator's entry is preserved, never overwritten.
PY
}

install_recovery_guardian()
{
    log "Installing the persistent exact-kernel recovery guardian..."
    local guardian_args=()
    if [[ -n "$INTERSTITIAL_BINARY" || -n "$INTERSTITIAL_SHA256" ]]; then
        guardian_args=(--interstitial-binary "$INTERSTITIAL_BINARY" --interstitial-sha256 "$INTERSTITIAL_SHA256")
    fi
    sudo env OPEMOS_PINNED_NVIDIA_VERSION="$NVIDIA_VERSION" \
        /bin/bash "$TMP/support/bootstrap/install_recovery_guardian.sh" "${guardian_args[@]}"
}

retain_built_product()
{
    local cache root
    root="$(project_system_path /)"
    local home identity
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    identity="$(python3 - "$TMP/support/lib" "$BUILD_OUT/materialization.json" <<'PYIDENTITY'
import sys
sys.path.insert(0,sys.argv[1])
from recovery_cached_product import canonical,read_document,digest
print(digest(canonical(read_document(sys.argv[2]))))
PYIDENTITY
)"
    [[ "$identity" =~ ^[0-9a-f]{64}$ ]] || die "Materialization identity unavailable."
    sudo python3 - "$TMP/support/lib" "$home" <<'PYPRIVATE'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from install_guardian_generation import private_directory
home=Path(sys.argv[2]);private_directory(home);private_directory(home/'cached-products')
PYPRIVATE
    cache="$home/cached-products/$identity"
    RETAINED_PRODUCT="$cache"
    local exact=(--steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION"
                 --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")
    if [[ -e "$cache" || -L "$cache" ]]; then
        sudo python3 "$TMP/support/lib/recovery_cached_product.py" show \
            "${exact[@]}" --directory "$cache" > "$TMP/existing-cache.json" ||
            die "Existing recovery cache conflicts with this exact built product; preserved."
        python3 - "$BUILD_OUT/materialization.json" "$TMP/existing-cache.json" <<'PY'
import json, sys
expected, existing = [json.load(open(path)) for path in sys.argv[1:]]
existing.pop("paths", None)
if not (existing == expected):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
PY
    else
        [[ "$(sudo find "$home/cached-products" -mindepth 1 -maxdepth 1 -printf . | wc -c)" -lt 10 ]] ||
            die "Retained product bound reached; preserve existing products."
        sudo python3 "$TMP/support/lib/recovery_cached_product.py" stage \
            "${exact[@]}" --materialization "$BUILD_OUT/materialization.json" \
            --input-dir "$BUILD_OUT" --destination "$cache" >/dev/null
    fi
    sudo python3 - "$TMP/support/lib" "$home" "$identity" <<'PYCACHE'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import installed_update_transaction as tx
home=Path(sys.argv[2]);cache=home/'cached-products'/sys.argv[3]
for entry in cache.iterdir():
    fd=__import__('os').open(entry,__import__('os').O_RDONLY|__import__('os').O_NOFOLLOW)
    try:__import__('os').fsync(fd)
    finally:__import__('os').close(fd)
tx.fsync_dir(cache);tx.fsync_dir(cache.parent)
tx.durable(home/'cached-current.json',{'product':sys.argv[3]})
PYCACHE
}


# Reuse the existing validated producer pipeline for a published/development
# archive. Metadata must be actual producer sidecars; never synthesize trust or
# a materialization receipt from downloaded archive hashes alone.
canonicalize_product()
{
    local archive="$1" checksum="$2" fuzzy="$3" stem
    [[ "$fuzzy" == 0 ]] || die "Canonical recovery custody requires an exact SteamOS target; older-target release preserved, not relabelled."
    stem="${archive%.tar.gz}"
    [[ -f "$stem.build-info.txt" && -f "$stem.provenance.json" ]] ||
        die "Exact product custody requires the release's existing build-info and provenance sidecars."
    local packed products tag manifest_sha representation
    representation="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact"].get("representation","raw"))' "$stem.provenance.json")"
    if [[ "$representation" != ko.zst ]]; then
        python3 "$TMP/support/lib/repack_module_artifact.py" \
            --archive "$archive" --checksum "$checksum" --build-info "$stem.build-info.txt" \
            --provenance "$stem.provenance.json" --output-dir "$TMP/canonical-repacked" --support-commit "$SUPPORT_REV"
        mapfile -t packed < <(find "$TMP/canonical-repacked" -maxdepth 1 -type f -name 'nvidia-open-*.tar.gz' | sort)
        (( ${#packed[@]} == 1 )) || die "Canonical producer output is ambiguous."
        archive="${packed[0]}"; stem="${archive%.tar.gz}"
    fi
    python3 "$TMP/support/lib/build_driver_product.py" --archive "$archive" --checksum "$archive.sha256" \
        --build-info "$stem.build-info.txt" --provenance "$stem.provenance.json" --output-dir "$TMP/canonical-product"
    mapfile -t products < <(find "$TMP/canonical-product" -maxdepth 1 -type f -name '*.tar.gz' | sort)
    (( ${#products[@]} == 1 )) || die "Driver product output is ambiguous."
    tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact"]["releaseTag"])' "$stem.provenance.json")"
    python3 "$TMP/support/lib/driver_binary_bundle.py" create --archive "${products[0]}" \
        --sidecar "${products[0]}.sha256" --output "$TMP/canonical-product/bundle.json" --release-tag "$tag"
    manifest_sha="$(sha256sum "$TMP/canonical-product/bundle.json" | cut -d ' ' -f 1)"
    BUILD_OUT="$TMP/materialized"
    # Materializer owns output creation; shell redirection must not pre-create it.
    python3 "$TMP/support/lib/materialize_driver_product.py" --manifest "$TMP/canonical-product/bundle.json" \
        --asset-dir "$TMP/canonical-product" --expected-manifest-sha256 "$manifest_sha" \
        --expected-core-commit "$SUPPORT_REV" --expected-steamos "$STEAMOS_VERSION" \
        --expected-kernel "$KERNEL_VERSION" --expected-nvidia "$NVIDIA_VERSION" --expected-architecture x86_64 \
        --output-dir "$BUILD_OUT" > "$TMP/materialization-result.json"
    cp "$TMP/materialization-result.json" "$BUILD_OUT/materialization.json"
}

retain_install_inputs()
{
    local home args result
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    args=(retain-install --home "$home" --directory "$RETAINED_PRODUCT" --steamos "$STEAMOS_VERSION" \
          --kernel "$KERNEL_VERSION" --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")
    if [[ -n "$INTERSTITIAL_BINARY" || -n "$INTERSTITIAL_SHA256" ]]; then
        args+=(--interstitial "$INTERSTITIAL_BINARY" --interstitial-sha256 "$INTERSTITIAL_SHA256")
    fi
    result="$(sudo python3 "$TMP/support/lib/recovery_cached_product.py" "${args[@]}")"
    INTERSTITIAL_BINARY="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["interstitial"])' "$result")"
    INTERSTITIAL_SHA256="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["sha256"])' "$result")"
}

resolve_local()
{
    local source="$1"
    local work="$TMP/local"
    mkdir -p "$work"

    if [[ -d "$source" ]]; then
        mapfile -t archives < <(find "$source" -maxdepth 1 -type f -name 'nvidia-open-*.tar.gz' | sort)
        (( ${#archives[@]} == 1 )) || die "Local directory must contain exactly one nvidia-open-*.tar.gz archive."
        LOCAL_ARCHIVE="${archives[0]}"
    elif [[ "$source" == *.zip ]]; then
        need unzip
        unzip -q "$source" -d "$work"
        mapfile -t archives < <(find "$work" -maxdepth 1 -type f -name 'nvidia-open-*.tar.gz' | sort)
        (( ${#archives[@]} == 1 )) || die "Local bundle must contain exactly one nvidia-open-*.tar.gz archive."
        LOCAL_ARCHIVE="${archives[0]}"
    elif [[ "$source" == *.tar.gz ]]; then
        LOCAL_ARCHIVE="$source"
    else
        die "Unsupported local package type: $source"
    fi

    LOCAL_CHECKSUM="${LOCAL_ARCHIVE}.sha256"
    [[ -f "$LOCAL_CHECKSUM" ]] || die "Matching checksum not found: $LOCAL_CHECKSUM"
}

# A pending ordinary transaction wins over discovery/build. Its immutable
# identity is not the mutable latest-product pointer, and is never rearmed.
if [[ "$IN_CODE" == 0 && -z "$LOCAL_SOURCE" ]]; then
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    if sudo test -e "$home/pending-install.json"; then
        RETAINED_PRODUCT="$(sudo python3 "$TMP/support/lib/recovery_cached_product.py" pending \
            --home "$home" --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
            --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")" || die "Pending exact install cannot be replaced by discovery."
        [[ -n "$RETAINED_PRODUCT" ]] || die "Pending product unavailable; preserve original intent."
        install_archive unused unused 0
        offer_reboot
        exit 0
    fi
fi

if [[ "$IN_CODE" == "1" ]]; then
    BUILD_OUT="$TMP/in-code-release"
    mkdir -p "$BUILD_OUT"

    log "Compiling current NVIDIA source tree for immediate deployment..."
    "$TMP/support/bootstrap/compile_online.sh" --in-code -o "$BUILD_OUT"

    mapfile -t bundles < <(find "$BUILD_OUT" -maxdepth 1 -type f -name 'nvidia-open-*.zip' | sort)
    (( ${#bundles[@]} == 1 )) || die "Expected exactly one compiled bundle from --in-code."

    resolve_local "${bundles[0]}"
    install_archive "$LOCAL_ARCHIVE" "$LOCAL_CHECKSUM" 0
    install_recovery_guardian
    offer_reboot
    exit 0
fi

if [[ -n "$LOCAL_SOURCE" ]]; then
    LOCAL_SOURCE="$(realpath "$LOCAL_SOURCE")"
    resolve_local "$LOCAL_SOURCE"
    install_archive "$LOCAL_ARCHIVE" "$LOCAL_CHECKSUM" 0
    install_recovery_guardian
    offer_reboot
    exit 0
fi

RELEASES_JSON="$TMP/releases.json"

if ! curl -fsSL --retry 2 \
    "https://api.github.com/repos/${SUPPORT_REPO}/releases?per_page=100" \
    -o "$RELEASES_JSON"; then
    # A failed query never authorizes compilation. Only an existing cache may
    # proceed through the exact current checkout's policy and byte validation.
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    cache="$(sudo python3 "$TMP/support/lib/recovery_cached_product.py" select \
        --home "$home" --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")" || \
        die "Retained exact product failed validation after release query failure."
    RETAINED_PRODUCT="$cache"
    if [[ "$BUILD_AS_FALLBACK" == 1 && -z "${OPEMOS_RECOVERY_PLAN_FILE:-}" &&
          -n "$cache" && ( -e "$cache" || -L "$cache" ) ]]; then
        printf '[]\n' > "$RELEASES_JSON"
        OPEMOS_SKIP_UNCHANGED_BUILD=1
        log "Release query unavailable; checking the exact retained cache only."
    else
    die "Failed to query published releases."
    fi
fi

RECOVERY_PLAN_FILE="${OPEMOS_RECOVERY_PLAN_FILE:-}"
if [[ -n "$RECOVERY_PLAN_FILE" && -f "$RECOVERY_PLAN_FILE" ]]; then
    IFS=$'\t' read -r SELECTED_STEAMOS SELECTED_NVIDIA SELECTED_KERNEL SELECTED_TAG SELECTED_ASSET PINNED_ARCHIVE_SHA < <(
        python3 "$TMP/support/lib/recovery_release_plan.py" show --plan "$RECOVERY_PLAN_FILE"
    )
    [[ "$SELECTED_KERNEL" == "$KERNEL_TAG" && "$SELECTED_NVIDIA" == "$NVIDIA_VERSION" ]] ||
        die "The immutable recovery plan does not match the active kernel and NVIDIA policy."
    SELECTED="$SELECTED_STEAMOS"$'\t'"$SELECTED_NVIDIA"$'\t'"$SELECTED_KERNEL"$'\t'"$SELECTED_TAG"
else
    python3 "$TMP/support/lib/automatic_install_selection.py" \
        --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --releases "$RELEASES_JSON" --repository "$SUPPORT_REPO" "${FALLBACK_ARGS[@]}" > "$TMP/automatic.json" ||
        die "No published product or reviewed Automatic build is authorized for this exact target."
    SELECTED="$(python3 - "$TMP/automatic.json" <<'PY'
import json, sys
action = json.load(open(sys.argv[1]))['action']
if action['kind'] == 'use_published_artifact':
    publication = action['resolverResult']['publication']
    print('\t'.join(publication[field] for field in ('steamosVersion', 'nvidiaVersion', 'kernelVersion', 'tag')))
elif action['kind'] != 'build_exact_target':
    raise SystemExit('Unsupported Automatic action')
PY
    )"
fi

if [[ -z "$SELECTED" ]]; then
    # Empty selection alone is not authorization. The strict shared contract
    # rejects malformed/incomplete metadata and unreviewed exact targets.
    BUILD_OUT="$TMP/automatic-build"
    home="$(project_system_path /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery)"
    cache="$(sudo python3 "$TMP/support/lib/recovery_cached_product.py" select \
        --home "$home" --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
        --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV")" || \
        die "Retained exact build cache failed verification."
    RETAINED_PRODUCT="$cache"
    if [[ -e "$cache" || -L "$cache" ]]; then
        sudo python3 "$TMP/support/lib/recovery_cached_product.py" show \
            --steamos "$STEAMOS_VERSION" --kernel "$KERNEL_VERSION" \
            --nvidia "$NVIDIA_VERSION" --support-revision "$SUPPORT_REV" \
            --directory "$cache" > "$TMP/cached-build.json" ||
            die "Existing exact build cache is invalid or mismatched; preserved."
        mkdir "$BUILD_OUT"
        python3 - "$TMP/cached-build.json" "$TMP/automatic.json" "$BUILD_OUT/materialization.json" <<'PY'
import json, sys
cached, authorized = [json.load(open(path)) for path in sys.argv[1:3]]
source = authorized['action']['buildPlan']['source']
if not (cached['source'] == {key: source[key] for key in ('repository', 'commit')}):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
cached.pop('paths')
with open(sys.argv[3], 'x') as output:
    json.dump(cached, output, sort_keys=True, separators=(',', ':'))
    output.write('\n')
PY
        while IFS=$'\t' read -r name size sha; do
            sudo python3 - "$TMP/support/lib" "$cache/$name" "$size" "$sha" > "$BUILD_OUT/$name" <<'PY'
import hashlib, sys
sys.path.insert(0, sys.argv[1])
from recovery_cached_product import read_regular
payload = read_regular(sys.argv[2], int(sys.argv[3]), 'cached build input')
if not (len(payload) == int(sys.argv[3]) and hashlib.sha256(payload).hexdigest() == sys.argv[4]):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
sys.stdout.buffer.write(payload)
PY
        done < <(python3 - "$TMP/cached-build.json" <<'PY'
import json, sys
for record in json.load(open(sys.argv[1]))['outputs'].values():
    print('\t'.join(str(record[field]) for field in ('name', 'bytes', 'sha256')))
PY
        )
        log "Reusing the verified exact built-product cache."
    else
    if [[ "${OPEMOS_SKIP_UNCHANGED_BUILD:-0}" == 1 ]]; then
        printf 'This exact reviewed build previously failed; unchanged compilation is not repeated.\n' >&2
        exit 76
    fi
    log "No exact published product exists; building the reviewed exact target..."
    python3 "$TMP/support/lib/run_in_process_group.py" \
        "$TMP/support/bootstrap/build_automatic_for_target.sh" \
        "$STEAMOS_VERSION" "$KERNEL_VERSION" "$NVIDIA_VERSION" "$RELEASES_JSON" "$BUILD_OUT" "$TMP/build-failure.json" &
    ACTIVE_BUILD=$!
    if wait "$ACTIVE_BUILD"; then BUILD_RC=0; else BUILD_RC=$?; fi
    ACTIVE_BUILD=""
    if (( BUILD_RC != 0 )); then
        if [[ -f "$TMP/build-failure.json" ]]; then
            FAILURE_REASON="$(python3 - "$TMP/build-failure.json" <<'PY'
import json, sys
document = json.load(open(sys.argv[1]))
print(document['reason'])
PY
            )"
            case "$FAILURE_REASON" in
                compilation_failed|compiler_policy_mismatch|header_identity_mismatch|header_tree_incomplete|module_metadata_invalid)
                    printf 'Exact reviewed build cannot proceed: %s. No modules installed.\n' "$FAILURE_REASON" >&2
                    exit 76 ;;
            esac
        fi
        die "The reviewed exact-target build failed; installation was not attempted."
    fi
    fi
    mapfile -t archives < <(find "$BUILD_OUT" -maxdepth 1 -type f -name 'nvidia-open-*.tar.gz' | sort)
    (( ${#archives[@]} == 1 )) || die "Expected one exact Automatic build archive."
    ARCHIVE="${archives[0]}"
    CHECKSUM="${ARCHIVE}.sha256"
    python3 "$TMP/support/lib/validate_publish_inputs.py" \
        --archive "$ARCHIVE" --checksum "$CHECKSUM" \
        --build-info "${ARCHIVE%.tar.gz}.build-info.txt" \
        --provenance "${ARCHIVE%.tar.gz}.provenance.json" \
        --repository "$SUPPORT_REPO" > "$TMP/build-validation.json"
    python3 - "$TMP/build-validation.json" "$TMP/automatic.json" \
        "${ARCHIVE%.tar.gz}.provenance.json" "$SUPPORT_REV" <<'PY'
import json, sys
validated, authorized, provenance = [json.load(open(path)) for path in sys.argv[1:4]]
plan = authorized['action']['buildPlan']
if not (authorized['action']['kind'] == 'build_exact_target'):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
if not (validated['status'] == 'ready' and validated['trust'] == 'locally-built-verified'):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
if not (validated['targetCommit'] == sys.argv[4]):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
if not (provenance['target'] == plan['target']):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
if not (provenance['source']['commit'] == plan['source']['commit']):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
if not (provenance['source']['repository'] == plan['source']['repository']):
    raise SystemExit("Exact built-product identity verification failed; existing data preserved.")
PY
    install_archive "$ARCHIVE" "$CHECKSUM" 0
    log "Actual startup health, not build or installer completion, decides update success."
    offer_reboot
    exit 0
fi

IFS=$'\t' read -r \
    SELECTED_STEAMOS \
    SELECTED_NVIDIA \
    SELECTED_KERNEL \
    SELECTED_TAG \
    <<< "$SELECTED"

[[ "$SELECTED_KERNEL" == "$KERNEL_TAG" ]] ||
    die "Internal release-selection error: selected kernel ${SELECTED_KERNEL}; expected ${KERNEL_TAG}."

[[ "$SELECTED_NVIDIA" == "$NVIDIA_VERSION" ]] ||
    die "Certified release ${SELECTED_TAG} requires NVIDIA userspace ${SELECTED_NVIDIA}, but ${NVIDIA_VERSION} is installed. Run setup_nvidia.sh to align userspace first."

SELECTED_ASSET="${SELECTED_ASSET:-nvidia-open-${SELECTED_TAG}-x86_64.tar.gz}"
if [[ -n "$RECOVERY_PLAN_FILE" && ! -f "$RECOVERY_PLAN_FILE" ]]; then
    python3 "$TMP/support/lib/recovery_release_plan.py" create \
        --plan "$RECOVERY_PLAN_FILE" --steamos "$SELECTED_STEAMOS" \
        --nvidia "$SELECTED_NVIDIA" --kernel-tag "$SELECTED_KERNEL" \
        --release-tag "$SELECTED_TAG" --asset-name "$SELECTED_ASSET" >/dev/null
fi

if [[ "$SELECTED_STEAMOS" == "$STEAMOS_VERSION" ]]; then
    log "Using exact SteamOS certified release ${SELECTED_TAG}."
else
    warn "No exact certified release exists for SteamOS ${STEAMOS_VERSION}."
    warn "Using newest non-surpassed certification: ${SELECTED_STEAMOS}."
fi

if [[ "$FUZZY" == "1" ]]; then
    warn "--fuzzy is retained for compatibility; certified fallback is now always bounded to older releases in the same SteamOS major/minor series."
fi

BASE_URL="https://github.com/${SUPPORT_REPO}/releases/download/${SELECTED_TAG}"
ARCHIVE="$TMP/${SELECTED_ASSET}"
CHECKSUM="${ARCHIVE}.sha256"

log "Downloading published release ${SELECTED_TAG}..."

HTTP="$(curl -sS -L --retry 2 -w '%{http_code}' "${BASE_URL}/${SELECTED_ASSET}" -o "$ARCHIVE")" ||
    die "Failed to contact GitHub."

if [[ "$HTTP" == "404" ]]; then
    rm -f "$ARCHIVE"
    die "Selected certified release disappeared before download: ${SELECTED_TAG}"
fi
[[ "$HTTP" == "200" ]] || die "Unexpected HTTP ${HTTP} downloading release."

HTTP_SHA="$(curl -sS -L --retry 2 -w '%{http_code}' "${BASE_URL}/${SELECTED_ASSET}.sha256" -o "$CHECKSUM")" ||
    die "Failed to download release checksum."
[[ "$HTTP_SHA" == "200" ]] || die "Unexpected HTTP ${HTTP_SHA} downloading checksum."

if [[ -n "$RECOVERY_PLAN_FILE" ]]; then
    python3 "$TMP/support/lib/recovery_release_plan.py" bind-archive \
        --plan "$RECOVERY_PLAN_FILE" --archive "$ARCHIVE" >/dev/null ||
        die "Downloaded release differs from the immutable recovery plan."
fi

# The archive/sidecar format is already the Core release contract. Require its
# two existing metadata assets in the selected release inventory before fetch.
python3 - "$RELEASES_JSON" "$SELECTED_TAG" "$SELECTED_ASSET" <<'PYASSETS'
import json,sys
rows=[r for r in json.load(open(sys.argv[1])) if r.get('tag_name')==sys.argv[2]]
if len(rows)!=1:raise SystemExit('Selected release inventory missing or ambiguous; no fabricated provenance')
names={a['name'] for a in rows[0].get('assets',[])}
stem=sys.argv[3].removesuffix('.tar.gz')
if not {stem+'.build-info.txt',stem+'.provenance.json'}<=names:
    raise SystemExit('Published release lacks existing authenticated metadata inputs; no first-install product can be retained')
PYASSETS
for suffix in build-info.txt provenance.json; do
    curl -fSL --retry 2 "${BASE_URL}/${SELECTED_ASSET%.tar.gz}.$suffix" \
        -o "${ARCHIVE%.tar.gz}.$suffix" || die "Release metadata download failed."
done
INSTALL_FUZZY=0
[[ "$SELECTED_STEAMOS" != "$STEAMOS_VERSION" ]] && INSTALL_FUZZY=1

install_archive "$ARCHIVE" "$CHECKSUM" "$INSTALL_FUZZY"
install_recovery_guardian
retain_core_checkout
offer_reboot
