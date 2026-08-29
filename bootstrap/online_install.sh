#!/usr/bin/env bash
set -euo pipefail

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"
SUPPORT_BRANCH="${SUPPORT_BRANCH:-main}"

FUZZY=0
IN_CODE=0
LOCAL_SOURCE=""
YES=0

usage()
{
    cat <<EOF
Usage: online_install.sh [options]

Options:
      --fuzzy        Use nearest published SteamOS patch release
      --local PATH   Install an explicitly supplied local bundle/archive
      --in-code      Compile the current NVIDIA source working tree, then install it
  -y, --yes          Automatically confirm installer prompts
  -h, --help         Show this help

Normal and --fuzzy installs only use published release artifacts.
--local and --in-code are explicit development/testing paths.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fuzzy) FUZZY=1; shift ;;
        --in-code) IN_CODE=1; shift ;;
        --local) [[ $# -ge 2 ]] || { echo "--local requires a path." >&2; exit 1; }; LOCAL_SOURCE="$2"; shift 2 ;;
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

need()
{
    command -v "$1" >/dev/null 2>&1 || { printf 'Missing command: %s\n' "$1" >&2; exit 1; }
}

need git
need curl
need tar
need sha256sum
need python3
need nvidia-smi

SUPPORT_REV="$(git ls-remote "https://github.com/${SUPPORT_REPO}.git" "refs/heads/${SUPPORT_BRANCH}" | awk 'NR==1 {print $1}')"
[[ "$SUPPORT_REV" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve support revision." >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --quiet --depth 1 "https://github.com/${SUPPORT_REPO}.git" "$TMP/support"
git -C "$TMP/support" fetch --quiet --depth 1 origin "$SUPPORT_REV"
git -C "$TMP/support" checkout --quiet --detach "$SUPPORT_REV"

source "$TMP/support/lib/common.sh"

require_steamos

STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"
NVIDIA_VERSION="$(get_nvidia_version)"
KERNEL_TAG="$(sanitize_release_component "$KERNEL_VERSION")"

printf '[%s] SteamOS: %s\n' "$PROJECT_NAME" "$STEAMOS_VERSION"
printf '[%s] Kernel:  %s\n' "$PROJECT_NAME" "$KERNEL_VERSION"
printf '[%s] NVIDIA:  %s\n' "$PROJECT_NAME" "$NVIDIA_VERSION"

INSTALL_CHANGED=0

already_installed()
{
    local archive="$1"
    local checksum="$2"
    local expected_sha actual_sha entry
    local state_root="/var/lib/open-gpu-kernel-modules-steamos-support"
    local installed_info="${state_root}/installed-build-info.txt"
    local target_dir="/usr/lib/modules/${KERNEL_VERSION}/updates/open-gpu-kernel-modules-steamos"
    local check_dir="${TMP}/installed-check"
    local resolved resolved_real target_real module installed module_sha installed_sha

    [[ -f "$installed_info" && -d "$target_dir" ]] || return 1

    expected_sha="$(awk '{print $1}' "$checksum" | head -n1)"

    [[ "$expected_sha" =~ ^[0-9a-fA-F]{64}$ ]] ||
        return 1

    actual_sha="$(sha256sum "$archive" | awk '{print $1}')"

    [[ "${expected_sha,,}" == "${actual_sha,,}" ]] ||
        return 1

    while IFS= read -r entry; do
        [[ "$entry" != /* ]] || return 1

        [[ "$entry" != ".." &&
           "$entry" != ../* &&
           "$entry" != */../* &&
           "$entry" != */.. ]] ||
            return 1
    done < <(tar -tzf "$archive") || return 1

    rm -rf "$check_dir"
    mkdir -p "$check_dir"

    tar -xzf "$archive" -C "$check_dir"

    [[ -f "$check_dir/BUILD-INFO.txt" && -d "$check_dir/modules" ]] ||
        return 1

    cmp -s "$check_dir/BUILD-INFO.txt" "$installed_info" ||
        return 1

    resolved="$(modinfo -n nvidia 2>/dev/null || true)"
    [[ -n "$resolved" ]] || return 1

    resolved_real="$(realpath -m "$resolved")"
    target_real="$(realpath -m "$target_dir")"

    case "$resolved_real" in
        "$target_real"/*) ;;
        *) return 1 ;;
    esac

    for module in "$check_dir"/modules/*.ko; do
        [[ -f "$module" ]] || return 1

        installed="$target_dir/$(basename "$module")"
        [[ -f "$installed" ]] || return 1

        module_sha="$(sha256sum "$module" | awk '{print $1}')"
        installed_sha="$(sha256sum "$installed" | awk '{print $1}')"

        [[ "$module_sha" == "$installed_sha" ]] ||
            return 1
    done

    return 0
}

offer_reboot()
{
    [[ "$INSTALL_CHANGED" == "1" ]] || return 0

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
    local args=(--archive "$archive" --checksum "$checksum")
    [[ "$fuzzy_flag" == "1" ]] && args+=(--fuzzy)
    [[ "$YES" == "1" ]] && args+=(-y)

    if already_installed "$archive" "$checksum"; then
        ok "Already installed, healthy, and current."
        log "Nothing to do."
        INSTALL_CHANGED=0
        return 0
    fi

    if [[ -f "/var/lib/open-gpu-kernel-modules-steamos-support/installed-build-info.txt" ]]; then
        log "Existing NVIDIA open kernel module installation requires update or repair."
    fi

    "$TMP/support/bootstrap/install.sh" "${args[@]}"
    INSTALL_CHANGED=1
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

if [[ "$IN_CODE" == "1" ]]; then
    BUILD_OUT="$TMP/in-code-release"
    mkdir -p "$BUILD_OUT"

    log "Compiling current NVIDIA source tree for immediate deployment..."
    "$TMP/support/bootstrap/compile_online.sh" --in-code -o "$BUILD_OUT"

    mapfile -t bundles < <(find "$BUILD_OUT" -maxdepth 1 -type f -name 'nvidia-open-*.zip' | sort)
    (( ${#bundles[@]} == 1 )) || die "Expected exactly one compiled bundle from --in-code."

    resolve_local "${bundles[0]}"
    install_archive "$LOCAL_ARCHIVE" "$LOCAL_CHECKSUM" 0
    offer_reboot
    exit 0
fi

if [[ -n "$LOCAL_SOURCE" ]]; then
    LOCAL_SOURCE="$(realpath "$LOCAL_SOURCE")"
    resolve_local "$LOCAL_SOURCE"
    install_archive "$LOCAL_ARCHIVE" "$LOCAL_CHECKSUM" 0
    offer_reboot
    exit 0
fi

EXACT_TAG="$(release_tag)"
EXACT_ASSET="$(release_asset)"
SELECTED_TAG="$EXACT_TAG"
SELECTED_ASSET="$EXACT_ASSET"
SELECTED_STEAMOS="$STEAMOS_VERSION"

if [[ "$FUZZY" == "1" ]]; then
    RELEASES_JSON="$TMP/releases.json"
    curl -fsSL --retry 2 \
        "https://api.github.com/repos/${SUPPORT_REPO}/releases?per_page=100" \
        -o "$RELEASES_JSON" ||
        die "Failed to query published releases."

    SELECTED="$(python3 -c '
import json,re,sys
target_s,target_n,target_k,path=sys.argv[1:]
def ver(v):
    p=[int(x) for x in v.split(".")]
    while len(p)<3:p.append(0)
    return tuple(p[:3])
ts=ver(target_s)
pat=re.compile(r"^steamos-([0-9]+(?:\.[0-9]+){2})-nvidia-([0-9]+(?:\.[0-9]+){1,2})-k(.+)$")
with open(path,encoding="utf-8") as f: releases=json.load(f)
c=[]
for r in releases:
    if r.get("draft") or r.get("prerelease"): continue
    m=pat.match(r.get("tag_name",""))
    if not m: continue
    sv,nv,kv=m.groups()
    if nv != target_n or kv != target_k: continue
    s=ver(sv)
    if s[:2] != ts[:2]: continue
    tag=r["tag_name"]
    asset="nvidia-open-"+tag+"-x86_64.tar.gz"
    names={a.get("name") for a in r.get("assets",[])}
    if asset not in names or asset+".sha256" not in names: continue
    dist=abs(s[2]-ts[2])
    newer=1 if s[2]>ts[2] else 0
    c.append(((dist,newer),sv,tag,asset))
if c:
    _,sv,tag,asset=min(c,key=lambda x:x[0])
    print("\t".join((sv,tag,asset)))
' "$STEAMOS_VERSION" "$NVIDIA_VERSION" "$KERNEL_TAG" "$RELEASES_JSON")"

    [[ -n "$SELECTED" ]] ||
        die "No published release matches kernel ${KERNEL_VERSION} and NVIDIA ${NVIDIA_VERSION} within SteamOS ${STEAMOS_VERSION%.*}.x. Use --in-code or --local."

    IFS=$'\t' read -r SELECTED_STEAMOS SELECTED_TAG SELECTED_ASSET <<< "$SELECTED"

    if [[ "$SELECTED_STEAMOS" != "$STEAMOS_VERSION" ]]; then
        warn "Using fuzzy SteamOS release ${SELECTED_STEAMOS} for system ${STEAMOS_VERSION}."
    fi
fi

BASE_URL="https://github.com/${SUPPORT_REPO}/releases/download/${SELECTED_TAG}"
ARCHIVE="$TMP/${SELECTED_ASSET}"
CHECKSUM="${ARCHIVE}.sha256"

log "Downloading published release ${SELECTED_TAG}..."

HTTP="$(curl -sS -L --retry 2 -w '%{http_code}' "${BASE_URL}/${SELECTED_ASSET}" -o "$ARCHIVE")" ||
    die "Failed to contact GitHub."

if [[ "$HTTP" == "404" ]]; then
    rm -f "$ARCHIVE"
    if [[ "$FUZZY" == "0" ]]; then
        die "No exact published release exists. Retry with --fuzzy, or use --in-code/--local for testing."
    fi
    die "Selected fuzzy release disappeared before download."
fi
[[ "$HTTP" == "200" ]] || die "Unexpected HTTP ${HTTP} downloading release."

HTTP_SHA="$(curl -sS -L --retry 2 -w '%{http_code}' "${BASE_URL}/${SELECTED_ASSET}.sha256" -o "$CHECKSUM")" ||
    die "Failed to download release checksum."
[[ "$HTTP_SHA" == "200" ]] || die "Unexpected HTTP ${HTTP_SHA} downloading checksum."

install_archive "$ARCHIVE" "$CHECKSUM" "$FUZZY"
offer_reboot