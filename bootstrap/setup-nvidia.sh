#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SUPPORT_ROOT}/lib/common.sh"

SUPPORT_REPO="${SUPPORT_REPO:-CorniiDog/open-gpu-kernel-modules-steamos-support}"

DRIVER_SPEC=""
RESOLVE_ONLY=0
YES=0

usage()
{
    cat <<EOF
Usage: setup-nvidia.sh [options]

Options:
      --driver VERSION   Explicit NVIDIA branch/version prefix.
                         Examples: 575, 580, 580.105, 580.105.08
      --resolve-only     Resolve and print the selected NVIDIA version only.
  -y, --yes              Automatically confirm setup.
  -h, --help             Show this help.

Without --driver, the NVIDIA version is selected from this projects
published SteamOS releases:

  1. Prefer the current SteamOS version.
  2. Otherwise use the newest older SteamOS release in the same
     major/minor series.
  3. Within that SteamOS release, use the newest published NVIDIA version.

Once selected, NVIDIA userspace is installed at that exact version.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --driver)
            [[ $# -ge 2 ]] || die "--driver requires a version."
            DRIVER_SPEC="$2"
            shift 2
            ;;
        --resolve-only)
            RESOLVE_ONLY=1
            shift
            ;;
        -y|--yes)
            YES=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

if [[ -n "$DRIVER_SPEC" ]]; then
    [[ "$DRIVER_SPEC" =~ ^[0-9]+([.][0-9]+)*$ ]] ||
        die "--driver must be a numeric NVIDIA version prefix such as 575, 580.105, or 580.105.08."
fi

require_steamos

need_cmd curl
need_cmd python3
need_cmd awk
need_cmd sort
need_cmd tar

STEAMOS_VERSION="$(get_steamos_version)"
KERNEL_VERSION="$(get_kernel_version)"

TMP="$(mktemp -d)"

cleanup()
{
    rm -rf "$TMP"
}

trap cleanup EXIT

ARCHIVE_BASE="https://archive.archlinux.org/packages"

resolve_arch_package()
{
    local package="$1"
    local spec="$2"
    local mode="${3:-exact}"
    local first="${package:0:1}"
    local listing="$TMP/${package}.html"
    local file

    curl -fsSL --retry 2 \
        "${ARCHIVE_BASE}/${first}/${package}/" \
        -o "$listing" ||
        die "Failed to query Arch package archive for ${package}."

    case "$mode" in
        exact)
            file="$(
                grep -oE "${package}-${spec}-[0-9]+-x86_64[.]pkg[.]tar[.](zst|xz)" "$listing" \
                    | sort -uV \
                    | tail -n1
            )"

            [[ -n "$file" ]] ||
                die "No exact ${package} package exists for NVIDIA ${spec}."
            ;;

        prefix)
            file="$(
                grep -oE "${package}-${spec}([.][0-9]+)*-[0-9]+-x86_64[.]pkg[.]tar[.](zst|xz)" "$listing" \
                    | sort -uV \
                    | tail -n1
            )"

            [[ -n "$file" ]] ||
                die "No ${package} package matches NVIDIA version prefix ${spec}."
            ;;

        *)
            die "Internal error: unknown NVIDIA package resolution mode: ${mode}"
            ;;
    esac

    printf "%s\n" "$file"
}

resolve_certified_driver()
{
    local releases="$TMP/releases.json"

    log "Looking for NVIDIA releases compatible with SteamOS ${STEAMOS_VERSION}..."

    curl -fsSL --retry 2 \
        "https://api.github.com/repos/${SUPPORT_REPO}/releases?per_page=100" \
        -o "$releases" ||
        die "Failed to query published NVIDIA releases."

    python3 -c '
import json
import re
import sys

target, path = sys.argv[1:]

def sv(v):
    p = [int(x) for x in v.split(".")]
    while len(p) < 3:
        p.append(0)
    return tuple(p[:3])

def nv(v):
    return tuple(int(x) for x in v.split("."))

target_v = sv(target)

pattern = re.compile(
    r"^steamos-"
    r"([0-9]+(?:\.[0-9]+){2})"
    r"-nvidia-"
    r"([0-9]+(?:\.[0-9]+){1,2})"
    r"-k(.+)$"
)

with open(path, encoding="utf-8") as f:
    releases = json.load(f)

candidates = []

for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue

    tag = release.get("tag_name", "")
    m = pattern.match(tag)

    if not m:
        continue

    steam, nvidia, kernel = m.groups()
    steam_v = sv(steam)

    # Never cross SteamOS major/minor automatically.
    if steam_v[:2] != target_v[:2]:
        continue

    # Fallback may only move backward, never forward.
    if steam_v > target_v:
        continue

    candidates.append((
        steam_v,
        nv(nvidia),
        release.get("published_at", ""),
        steam,
        nvidia,
        kernel,
        tag,
    ))

if not candidates:
    raise SystemExit(0)

# Highest non-surpassed SteamOS first.
# Within it, highest NVIDIA version wins.
candidates.sort(reverse=True)

_, _, _, steam, nvidia, kernel, tag = candidates[0]

print("\t".join((steam, nvidia, kernel, tag)))
' "$STEAMOS_VERSION" "$releases"
}

SELECTION_MODE=""
REFERENCE_STEAMOS=""
REFERENCE_KERNEL=""
REFERENCE_RELEASE=""

if [[ -n "$DRIVER_SPEC" ]]; then
    SELECTION_MODE="explicit"

    log "Resolving newest NVIDIA driver matching ${DRIVER_SPEC}..."

    NVIDIA_UTILS_FILE="$(resolve_arch_package nvidia-utils "$DRIVER_SPEC" prefix)"

    NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_FILE#nvidia-utils-}"
    NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_VERREL%-x86_64.pkg.tar.zst}"
    NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_VERREL%-x86_64.pkg.tar.xz}"

    RESOLVED_NVIDIA="${NVIDIA_UTILS_VERREL%-*}"

    REFERENCE_STEAMOS="$STEAMOS_VERSION"
    REFERENCE_KERNEL="$KERNEL_VERSION"
    REFERENCE_RELEASE="explicit:${DRIVER_SPEC}"
else
    SELECTION_MODE="certified"

    SELECTED="$(resolve_certified_driver)"

    [[ -n "$SELECTED" ]] ||
        die "No published NVIDIA release exists for SteamOS ${STEAMOS_VERSION} or an older release in ${STEAMOS_VERSION%.*}.x."

    IFS=$'\t' read -r \
        REFERENCE_STEAMOS \
        RESOLVED_NVIDIA \
        REFERENCE_KERNEL \
        REFERENCE_RELEASE \
        <<< "$SELECTED"

    if [[ "$REFERENCE_STEAMOS" == "$STEAMOS_VERSION" ]]; then
        log "Found exact SteamOS NVIDIA certification."
    else
        warn "No exact NVIDIA certification exists for SteamOS ${STEAMOS_VERSION}."
        warn "Using newest non-surpassed SteamOS certification: ${REFERENCE_STEAMOS}."
    fi

    NVIDIA_UTILS_FILE="$(resolve_arch_package nvidia-utils "$RESOLVED_NVIDIA" exact)"
fi

# Resolve again from the package filename so pkgrel selection cannot alter
# the NVIDIA pkgver we think we selected.

NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_FILE#nvidia-utils-}"
NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_VERREL%-x86_64.pkg.tar.zst}"
NVIDIA_UTILS_VERREL="${NVIDIA_UTILS_VERREL%-x86_64.pkg.tar.xz}"
PACKAGE_NVIDIA_VERSION="${NVIDIA_UTILS_VERREL%-*}"

[[ "$PACKAGE_NVIDIA_VERSION" == "$RESOLVED_NVIDIA" ]] ||
    die "Resolved nvidia-utils package is ${PACKAGE_NVIDIA_VERSION}; expected ${RESOLVED_NVIDIA}."

LIB32_FILE="$(resolve_arch_package lib32-nvidia-utils "$RESOLVED_NVIDIA" exact)"

LIB32_VERREL="${LIB32_FILE#lib32-nvidia-utils-}"
LIB32_VERREL="${LIB32_VERREL%-x86_64.pkg.tar.zst}"
LIB32_VERREL="${LIB32_VERREL%-x86_64.pkg.tar.xz}"
LIB32_NVIDIA_VERSION="${LIB32_VERREL%-*}"

[[ "$LIB32_NVIDIA_VERSION" == "$RESOLVED_NVIDIA" ]] ||
    die "lib32-nvidia-utils resolved to ${LIB32_NVIDIA_VERSION}; expected ${RESOLVED_NVIDIA}."

printf "\n[%s] NVIDIA userspace selection\n" "$PROJECT_NAME"
printf "[%s]   Current SteamOS:   %s\n" "$PROJECT_NAME" "$STEAMOS_VERSION"
printf "[%s]   Current kernel:    %s\n" "$PROJECT_NAME" "$KERNEL_VERSION"
printf "[%s]   Selection mode:    %s\n" "$PROJECT_NAME" "$SELECTION_MODE"
printf "[%s]   Reference SteamOS: %s\n" "$PROJECT_NAME" "$REFERENCE_STEAMOS"
printf "[%s]   Reference kernel:  %s\n" "$PROJECT_NAME" "$REFERENCE_KERNEL"
printf "[%s]   NVIDIA:            %s\n" "$PROJECT_NAME" "$RESOLVED_NVIDIA"
printf "[%s]   nvidia-utils:      %s\n" "$PROJECT_NAME" "$NVIDIA_UTILS_FILE"
printf "[%s]   lib32 utils:       %s\n" "$PROJECT_NAME" "$LIB32_FILE"
printf "[%s]   Reference:         %s\n" "$PROJECT_NAME" "$REFERENCE_RELEASE"

if [[ "$RESOLVE_ONLY" == "1" ]]; then
    printf "\n%s\n" "$RESOLVED_NVIDIA"
    exit 0
fi

need_cmd sudo
need_cmd pacman
need_cmd ldconfig

if [[ "$YES" != "1" ]]; then
    printf "\n"
    read -r -p "[$PROJECT_NAME] Install NVIDIA ${RESOLVED_NVIDIA} userspace? [y/N]: " REPLY

    case "$REPLY" in
        y|Y|yes|YES|Yes) ;;
        *) die "NVIDIA userspace setup cancelled." ;;
    esac
fi

log "Requesting administrator privileges..."
sudo -v

PKG_DIR="$TMP/packages"
mkdir -p "$PKG_DIR"

download_package()
{
    local package="$1"
    local file="$2"
    local first="${package:0:1}"
    local base="${ARCHIVE_BASE}/${first}/${package}/${file}"

    log "Downloading ${file}..."
    curl -fL --retry 2 "$base" -o "${PKG_DIR}/${file}" ||
        die "Failed to download ${file}."

    # Keep detached signatures next to packages when the archive provides them.
    curl -fL --retry 2 "${base}.sig" -o "${PKG_DIR}/${file}.sig" 2>/dev/null ||
        rm -f "${PKG_DIR}/${file}.sig"
}

download_package nvidia-utils "$NVIDIA_UTILS_FILE"
download_package lib32-nvidia-utils "$LIB32_FILE"

STATE_ROOT="/var/lib/open-gpu-kernel-modules-steamos-support/nvidia-setup"
STATE_TMP="$TMP/state"
mkdir -p "$STATE_TMP"

printf "%s\n" "$SELECTION_MODE" > "$STATE_TMP/selection-mode"
printf "%s\n" "${DRIVER_SPEC:-auto}" > "$STATE_TMP/selection-request"
printf "%s\n" "$RESOLVED_NVIDIA" > "$STATE_TMP/nvidia-version"
printf "%s\n" "$STEAMOS_VERSION" > "$STATE_TMP/installed-on-steamos"
printf "%s\n" "$KERNEL_VERSION" > "$STATE_TMP/installed-on-kernel"
printf "%s\n" "$REFERENCE_STEAMOS" > "$STATE_TMP/reference-steamos"
printf "%s\n" "$REFERENCE_KERNEL" > "$STATE_TMP/reference-kernel"
printf "%s\n" "$REFERENCE_RELEASE" > "$STATE_TMP/reference-release"
printf "%s\n" "$NVIDIA_UTILS_FILE" > "$STATE_TMP/nvidia-utils-package"
printf "%s\n" "$LIB32_FILE" > "$STATE_TMP/lib32-nvidia-utils-package"

RO_WAS_ENABLED=0

restore_readonly()
{
    if [[ "$RO_WAS_ENABLED" == "1" ]]; then
        sudo steamos-readonly enable >/dev/null 2>&1 ||
            warn "Failed to re-enable SteamOS read-only mode."
        RO_WAS_ENABLED=0
    fi
}

cleanup_install()
{
    local rc=$?
    trap - EXIT INT TERM
    restore_readonly
    rm -rf "$TMP"
    exit "$rc"
}

trap cleanup_install EXIT
trap "exit 130" INT
trap "exit 143" TERM

if command -v steamos-readonly >/dev/null 2>&1 &&
   steamos-readonly status 2>/dev/null | grep -qi enabled; then
    log "Temporarily disabling SteamOS read-only mode..."
    RO_WAS_ENABLED=1
    sudo steamos-readonly disable
fi

# Preserve the original GRUB defaults the first time this project owns
# NVIDIA userspace setup.
sudo mkdir -p "$STATE_ROOT"

if [[ ! -f "$STATE_ROOT/grub.default.before" &&
      -f /etc/default/grub ]]; then
    sudo cp -a /etc/default/grub "$STATE_ROOT/grub.default.before"
fi

log "Installing NVIDIA userspace ${RESOLVED_NVIDIA}..."

sudo pacman -U --noconfirm --needed \
    "${PKG_DIR}/${NVIDIA_UTILS_FILE}" \
    "${PKG_DIR}/${LIB32_FILE}"

INSTALLED_NV="$(pacman -Q nvidia-utils 2>/dev/null | awk "{print \$2}" || true)"
INSTALLED_NV="${INSTALLED_NV%-*}"

INSTALLED_LIB32="$(pacman -Q lib32-nvidia-utils 2>/dev/null | awk "{print \$2}" || true)"
INSTALLED_LIB32="${INSTALLED_LIB32%-*}"

[[ "$INSTALLED_NV" == "$RESOLVED_NVIDIA" ]] ||
    die "Installed nvidia-utils reports ${INSTALLED_NV:-unknown}; expected ${RESOLVED_NVIDIA}."

[[ "$INSTALLED_LIB32" == "$RESOLVED_NVIDIA" ]] ||
    die "Installed lib32-nvidia-utils reports ${INSTALLED_LIB32:-unknown}; expected ${RESOLVED_NVIDIA}."

log "Writing NVIDIA module configuration..."

sudo install -d -m 0755 /etc/modprobe.d

sudo tee /etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf >/dev/null <<EOF
# Managed by ${PROJECT_NAME}
blacklist nouveau
options nouveau modeset=0
options nvidia-drm modeset=1 fbdev=1
options nvidia NVreg_PreserveVideoMemoryAllocations=1
EOF

CMDLINE_ADD="rd.driver.blacklist=nouveau modprobe.blacklist=nouveau nvidia-drm.modeset=1 nvidia-drm.fbdev=1"

if [[ -f /etc/default/grub ]] &&
   ! grep -q "rd.driver.blacklist=nouveau" /etc/default/grub; then
    sudo sed -i -E \
        "s#^(GRUB_CMDLINE_LINUX_DEFAULT=\")#\1${CMDLINE_ADD} #" \
        /etc/default/grub
fi

if command -v update-grub >/dev/null 2>&1; then
    log "Refreshing GRUB configuration..."
    sudo update-grub
fi

for service in nvidia-suspend.service nvidia-resume.service nvidia-hibernate.service; do
    if systemctl cat "$service" >/dev/null 2>&1; then
        sudo systemctl enable "$service" >/dev/null 2>&1 || true
    fi
done

sudo ldconfig

sudo cp -a "$STATE_TMP/." "$STATE_ROOT/"

restore_readonly
trap - EXIT INT TERM

ok "NVIDIA userspace ${RESOLVED_NVIDIA} installed."
log "Userspace setup is complete; matching kernel modules must now be installed."
log "Recorded setup state: ${STATE_ROOT}"
