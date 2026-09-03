---
layout: page
title: Complete technical reference
nav_order: 90
description: Detailed build, installer, trust, storage, recovery, and testing behavior.
---

This page preserves the project's comprehensive engineering reference. For a
task-oriented introduction, start with the [documentation home](index.md).

## Contents

- [Repository responsibilities](#repository-responsibilities)
- [Operating modes](#operating-modes)
- [Local and in-code testing](#local-and-in-code-testing)
- [Version and archive safety](#version-and-archive-safety)
- [SteamOS storage policy](#steamos-storage-policy)
- [Installation state and rollback](#installation-state-and-rollback)
- [Current development target](#current-development-target)
- [Local checks](#local-checks)
- [Non-sudo reinstall baseline](#non-sudo-reinstall-baseline)

## OPEMOS

Build, install, and maintain NVIDIA open kernel modules matched to a specific
SteamOS release, Neptune kernel, and NVIDIA userspace version.

This project is under active development. SteamOS 3.8.16 has been tested with
the published NVIDIA 575.64.05 project release and with pristine upstream
NVIDIA 580.119.02 as a development control case. A completely fresh-stock
installation still needs end-to-end validation.

## Repository responsibilities

Three project repositories and NVIDIA upstream have deliberately separate jobs:

| Repository | Responsibility |
| --- | --- |
| [OPEMOS](https://github.com/CorniiDog/OPEMOS) | SteamOS detection, NVIDIA userspace setup, contained builds, archive validation, installation, uninstall, release selection, and online entry points |
| [OPEMOS.EXE](https://github.com/CorniiDog/OPEMOS.EXE) | Graphical recovery-image workflow, managed appliances, safe export, USB workflow, and independent final-image validation |
| `CorniiDog/open-gpu-kernel-modules-steamos` | Project-owned NVIDIA source branches and individual SteamOS compatibility patches |
| `NVIDIA/open-gpu-kernel-modules` | Pristine upstream source and tags used as control baselines |

OPEMOS owns canonical bundle membership and authentication policy, while
OPEMOS.EXE owns host acquisition and transport. OPEMOS owns rollback within a
mounted target transaction; OPEMOS.EXE owns disposable-overlay rollback and
source-image preservation. The OPEMOS.EXE installation-media welcome UI is not
the OPEMOS fullscreen no-input DRM/KMS interface—these have separate runtimes
and lifecycle owners. The latter is the sole UI ownership exception: its full
implementation belongs to OPEMOS Core and OPEMOS.EXE consumes it only as an
authenticated target payload.

`Automatic` is explicit user source intent constrained to reviewed Core
production policy, not permission to select development or approximate inputs.
OPEMOS.EXE deploys the OPEMOS-owned interstitial payload but never launches it;
a Core-owned installed-device supervisor owns launch and monitoring afterward.

OPEMOS owns build and release metadata. The source repository
owns patch history. Project releases must identify the exact source commit used
to build their modules.

Host acquisition, appliance attachment, and installed-device networking are
three separate authorities. User source selection is recorded as intent by
OPEMOS.EXE and authorized independently by Core. Recovery-image A/B layout is
builder orchestration; SteamOS owns base OS slot transitions, while Core owns
NVIDIA repair and verification in response. Installed-device connectivity and
credentials remain SteamOS/user state, not Core state. OPEMOS.EXE host ownership
is cross-platform even though macOS is the currently implemented and validated
host.

Source commit, kernel/header identity, toolchain, and container digest are the
reproducible inputs. Kernel-module bytes may still differ across builds because
the upstream build is not currently bit-for-bit deterministic.

## Operating modes

### Certified production

```bash
./bootstrap/setup_nvidia.sh --resolve-only
```

Normal resolution selects a published project release. It prefers the current
SteamOS version, permits only the bounded same-series fallback policy, and
keeps NVIDIA userspace matched exactly to the selected project modules.

The normal online installer installs the matching release:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_install.sh?x=$(date +%s)")
```

### Patched-module development

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --development 580 --resolve-only
```

`--development` selects matching NVIDIA userspace for project source work. It
does not build or replace kernel modules. This mode is intended for developing
and testing a project branch such as `nvidia/580.119.02`.

Install the selected userspace, then create or refresh the matching source
branch from the exact NVIDIA upstream tag in one command:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --development 580 --yes && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_dev.sh?x=$(date +%s)")
```

### Pristine upstream control

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --use-upstream 580 --resolve-only
```

`--use-upstream` selects matching userspace and, without `--resolve-only`,
builds and installs pristine NVIDIA upstream modules. Project patches are not
applied. This establishes the control case used to decide whether a bug belongs
to the project's module changes, NVIDIA upstream, or another part of the
graphics stack.

Install the selected userspace and build and install the pristine upstream
modules in one command:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --use-upstream 580 --yes --offer-reboot
```

To build the pristine archive without changing installed modules:

```bash
./bootstrap/install_upstream.sh --build-only 580.119.02
```

When Podman is already installed, build-only mode does not request sudo.

Artifacts are preserved under:

```text
~/.cache/open-gpu-kernel-modules-steamos-support/upstream-builds/
```

## Local and in-code testing

### Offline image-target resolution

Image builders must resolve compatibility from the mounted target image, not
from the Fedora appliance or macOS host. Fetch the GitHub releases API response,
then pass the detected SteamOS identity, exact module-directory kernel, and ELF
architecture to the versioned JSON resolver:

```bash
python3 lib/resolve_target.py \
    --steamos 3.8.16 \
    --kernel 6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45 \
    --architecture x86_64 \
    --releases releases.json
```

Schema version `2` returns `status=compatible` with publication, archive,
SHA256-sidecar, and provenance-sidecar URLs; `no_compatible_artifact`,
`unsupported_target`, and `invalid_target` are normal fail-closed results and
contain no downloadable artifact. The resolver requires an exact kernel match
and applies only the same bounded, non-forward SteamOS major/minor fallback used
by the live installer.
Resolution reports trust as `pending-provenance-verification`; consumers must
require the external provenance to be byte-identical to the archive's embedded
`PROVENANCE.json` before preserving its declared trust classification. A
published local build therefore remains `locally-built-verified` and is never
silently promoted to `certified-published`.

When no compatible published artifact exists, an x86_64 Fedora builder
appliance can create one without using its running kernel:

```bash
./bootstrap/build_for_target.sh \
    --steamos 3.8.14 \
    --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
    --nvidia 575.64.05 \
    --install-dependencies \
    --output /shared/artifacts
```

The command derives and downloads the exact Valve headers package, clones the
matching project NVIDIA source branch, builds against the extracted SteamOS
tree, and verifies the five-module set, matching NVIDIA version, x86_64 ELF
architecture, and exact vermagic. Module validation also produces a temporary
machine-readable record containing every module hash and validated property.
It emits the existing installer-compatible `.tar.gz`, `.sha256`, and build-info
files. `--source` and `--headers-package` permit pinned local inputs;
`--resolve-only` returns a JSON build plan without network or build activity.
The command intentionally requires an x86_64 Fedora appliance even when QEMU is
running on an Apple Silicon macOS host.

Before compilation, it validates the package's exact Arch metadata and rejects
absolute or traversal paths, duplicate members, special device/stream entries,
escaping symlink/hardlink targets, absent hardlink targets, and archives beyond
bounded compressed, expanded, member, or metadata limits. After extraction,
the exact-kernel build tree and every required prepared-tree file must resolve
inside the disposable extraction root; symlink escapes are rejected. Extraction
also retains libarchive's default intermediate-symlink protections, uses atomic
safe writes, and does not restore package ownership or permissions.

The build records the compiler used by Valve's kernel and the compiler used for
the external modules. If their major versions differ, it prefers an installed
`gcc-MAJOR` compatibility compiler and otherwise keeps the output explicitly
`development-unverified`. Use `--require-compiler-major-match` when a caller
must fail instead. Exact compiler version, binutils, make, kmod, Fedora identity,
support/source commits and dirty state, and per-module hashes/version/vermagic
are recorded in `BUILD-INFO.txt`.
The same data and the validated per-module records are published as a versioned
`.provenance.json` sidecar and embedded in the archive as `PROVENANCE.json`.
The final result contract names the sidecar so OPEMOS.EXE can copy it
directly into its image manifest without parsing human-readable logs.

### Canonical artifact publication

`bootstrap/publish_artifacts.sh` is the sole release-publication contract. It
requires the archive, SHA256 sidecar, build information, and provenance
sidecar, then validates their canonical basenames, checksum, target identity,
trust classification, release identity, clean support/source commits, and
byte-identical embedded metadata before contacting GitHub. It also rejects
unsafe or duplicate archive members and independently hashes the exact five
canonical modules against provenance. Existing releases may be updated only
when their tag resolves to the provenance support commit. `compile.sh
--auto-upload` delegates to this command and therefore publishes the same four
ordered assets with the same generated title and notes.

Inspect a non-mutating machine-readable plan first:

```bash
./bootstrap/publish_artifacts.sh \
  --archive /shared/nvidia-open-....tar.gz \
  --checksum /shared/nvidia-open-....tar.gz.sha256 \
  --build-info /shared/nvidia-open-....build-info.txt \
  --provenance /shared/nvidia-open-....provenance.json \
  --dry-run
```

Live publication is fixed to
`CorniiDog/OPEMOS`, verifies `gh`
authentication and push permission, and updates only the derived exact release.
Add `--create-only` to fail if that release already exists. A noncanonical
repository requires the conspicuous `--development-repository OWNER/REPO`
override. The publisher never discovers, deletes, or modifies unrelated
releases.

### Revisioned compressed-module repacks

`bootstrap/repack_artifacts.sh` is the canonical maintainer path for converting
an authenticated existing raw-module release to `.ko.zst`. It verifies the
archive checksum, byte-identical embedded/external metadata, exact five-module
set, provenance hashes, NVIDIA version, x86_64 ELF identity, and exact vermagic
before encoding with the pinned deterministic `zstd 1.5.7` command. The new
schema-1 provenance retains each raw `payloadSha256` separately from the
compressed representation hash and records the source release and sidecar
hashes. Output uses a new `-modules-zstd-rN` tag; publication always delegates
to `publish_artifacts.sh --create-only`, so the source release cannot be
overwritten.
The repacker streams raw modules, compressed representations, and the final tar
and gzip layers through bounded temporary files rather than retaining a
multi-gigabyte artifact in memory. Production use also requires
`--support-commit` to equal the clean checkout actually executing the command;
duplicate options, unsafe output directories, partial output sets, and stale
create-only destinations fail closed.

```bash
./bootstrap/repack_artifacts.sh \
  --archive /shared/nvidia-open-....tar.gz \
  --checksum /shared/nvidia-open-....tar.gz.sha256 \
  --build-info /shared/nvidia-open-....build-info.txt \
  --provenance /shared/nvidia-open-....provenance.json \
  --output-dir /shared/repacked \
  --support-commit "$(git rev-parse HEAD)" \
  --revision 1 \
  --dry-run
```

Remove `--dry-run` to create the four local assets. Add `--publish` to that
non-dry invocation to publish the revision create-only. The dry-run writes no
assets and returns a bounded schema-1 JSON plan suitable for a conditional
maintainer action in OPEMOS.EXE.

### Optional gaming payload profile

`profiles/gaming/reviewed-policy-v1.json` is the support-owned contract for
optional CUDA-compute omission. A conforming profile must preserve graphics,
Vulkan, GLVND/EGL, NVENC/NVDEC, exact-version GSP firmware, required 32-bit
gaming libraries, recovery rendering, exact package ownership, and provenance.
Delivery is by deterministic support-owned package repacking from the exact
Arch-signed packages and reviewed userspace lock—OPEMOS.EXE never
deletes guessed paths. The repacker verifies every omitted member's source
hash, regenerates `.PKGINFO`, `.BUILDINFO`, and `.MTREE`, assigns a distinct
package release, and requires the exact reviewed output hashes before pacman
can see the packages. This preserves normal pacman ownership and makes both a
repeat reduced install and a later complete-package reinstall deterministic.

The first reviewed target is SteamOS 3.8.14, NVIDIA 575.64.05, x86_64, kernel
`6.16.12-valve24.4-1-neptune-616-gfe145653a794`. It omits 316,170,989 logical
bytes consisting only of the 64-bit CUDA driver/debugger/NVVM/PTX-JIT/MPS
components and the 32-bit CUDA driver/PTX-JIT components. It deliberately
preserves Vulkan shader compilation, GLVND/EGL/OpenGL, NVENC/NVDEC, GSP
firmware, NGX/DLSS, Xorg/recovery rendering, and the remaining 32-bit gaming
stack. This option means “omit optional CUDA compute components to save
space.” It does **not** mean that the ordinary complete NVIDIA driver lacks
CUDA compatibility; the normal workflow remains unchanged and installs those
components.

The resolver always returns
`capabilities.optionalCudaOmission`. It reports `supported: true` only for an
exact SteamOS/kernel/NVIDIA/architecture record whose canonical profile and
lock assets occur exactly once in the selected release. Same-series fallback
never enables the capability. The installer accepts
`--gaming-payload-profile`; it authenticates the profile, source packages,
detached Arch signatures, and userspace lock before deriving anything. Both
`--validate-only` and mutation return `gamingPayload` with the exact target,
delivery method, omitted/preserved capabilities, source and derived package
records, and saved-byte count. A missing profile, closest-kernel target,
changed source, unexpected output hash, or incomplete release asset set fails
before mutation. Targets other than the exact reviewed record remain disabled.
During mutation a private repository-free pacman configuration permits the two
derived local packages only after the validator has authenticated their source
signatures and reproduced their pinned output hashes. This does not weaken the
GPG verification of the source or dependency packages. The derivation emits
bounded `gaming_payload_repack` item progress and is cancelled with the same
process-group and immutable-staging cleanup as the rest of validation.

### Offline mounted-root installation

`bootstrap/install_to_root.sh` defines the image-builder installation boundary
for an explicit mounted SteamOS root. It is intended to run only in the managed
x86_64 Fedora appliance and never examines the appliance kernel or invokes
`steamos-readonly`. In addition to the verified module archive, checksum, and
schema-1 provenance, callers must provide exact local `nvidia-utils` and
`lib32-nvidia-utils` packages, both detached signatures, and a reviewed GPG
keyring. No package or source is downloaded during installation. Before
mutation, the caller must leave the rootfs-owned `<root>/boot` visible and mount
the corresponding `efi-A` partition separately at `<root>/efi`; the installer
refuses to guess an A/B slot. Before mutation, `/efi` must be a distinct FAT
mount rather than another view of the rootfs. It atomically and idempotently enforces
`rd.driver.blacklist=nouveau`, `modprobe.blacklist=nouveau`,
`nvidia-drm.modeset=1`, and `nvidia-drm.fbdev=1` on every recognized Linux entry
in `<root>/efi/EFI/steamos/grub.cfg`, including Valve's
`steamenv_boot linux ...` form, replacing conflicting values while preserving
the original command prefix.
The root must contain Valve's populated, confined package database at
`/usr/lib/holo/pacmandb`. The installer passes that exact root-prefixed path to
pacman and never creates or falls back to `/var/lib/pacman`; validation records
the canonical database path and observed package count before mutation. Every
local record must have a confined regular `desc` file whose package name,
version, and directory identity agree; duplicate records and databases missing
the `filesystem`, `glibc`, or `pacman` base records are rejected. An unrelated
installed record may omit `%ISIZE%`; its identity and dependency metadata remain
usable, but it receives no storage credit. A package that will be replaced must
have one numeric `%ISIZE%`, otherwise validation fails closed and reports the
package directory and invalid field names.
Package signatures must resolve to an active package-specific fingerprint in
`trust/nvidia-userspace-package-signers.json`. Fedora `gpgv` requires a binary
keyring; an ASCII-armored pacman keyring must be dearmored before use. The
result records the supplied binary keyring SHA256, both package hashes, complete
versions/pkgrels, and signer fingerprints so OPEMOS.EXE can additionally
pin the exact prepared keyring artifact.

Normal installation requires `--userspace-lock FILE` plus the complete package
set. The reviewed schema-1 lock pins the exact SteamOS/NVIDIA target, minimal
keyring hash, package/signature filenames and hashes, versions, architectures,
installed sizes, dependency/provides metadata, and package-specific signers.
Repeated `--dependency-package FILE --dependency-signature FILE` arguments must
match that lock exactly: missing and extra packages both fail closed. Every
dependency is locally staged, signature-verified, path-confined, size-accounted,
and installed in one offline `pacman -U` transaction. Production installation
never accesses a package repository or expands signer trust. After the
transaction, every locked package—not only the two NVIDIA seeds—is queried for
its exact full version and checked with pacman's installed-file integrity audit.
The verifier also re-hashes each staged package against validated metadata and
compares installed regular-file bytes, modes, ownership, symlinks, and hardlinks
directly with the authenticated package payload. Any mismatch invalidates the
disposable overlay before module installation. The five installed modules are
then independently decompressed as needed and compared with the validated
logical module hashes, exact filenames, ownership, and modes before depmod or
initramfs generation.

Successful userspace verification emits a bounded schema-1 record containing
the exact locked package identities, versions and hashes, affirmative pacman
query/integrity/payload checks, per-package file/link/library counts, and the
exact-version GSP firmware inventory. It also identifies the confined Holo
pacman database and binds its verified locked-package count to the reviewed lock.
Pacman's local-database consistency check must also pass after the transaction,
covering dependency and record consistency before module mutation begins.
Installer success is impossible without this record matching the validated
database, package set, and NVIDIA version; the result does not expose host
paths or an unbounded package-file inventory.

A lock mismatch reports the complete bounded package-set difference before
mutation: sorted missing, unexpected, and duplicate identities plus one sorted
metadata-difference record per affected package. Metadata fields always appear
in this order: filename, signature filename, version, architecture, package
hash, signature hash, signer fingerprint, installed size, dependencies, and
provides. Dependency and provides values are compared as sorted unique sets.
At most 64 incoming and 64 reviewed packages, with at most 64 bounded relation
values per package, are accepted; inputs beyond those limits fail closed rather
than producing unbounded diagnostics.

Maintainers create candidate locks with
`bootstrap/audit_userspace_closure.py`. It authenticates `core`, `extra`, and
`multilib` databases from one explicit dated Arch Linux Archive snapshot,
resolves all dependencies absent from the target Holo database, downloads the
selected packages and detached signatures into an empty staging directory, and
verifies them against a hash-pinned full Arch keyring before reading
`.PKGINFO`. Cryptographically valid package/signature pairs absent from the
production package/signer policy are collected in `missingReview`; invalid
signatures stop the audit. The audit never mutates the target or trust policy.
Candidate locks are not installable: maintainers must review every missing
mapping and prepare a minimal keyring, then run
`bootstrap/finalize_userspace_lock.py`. The create-only finalizer recomputes
review status from the production policy, verifies that the minimal keyring
contains every required signer and no unrelated primary keys, and atomically
emits the reviewed lock. Malformed, oversized, duplicate-identity, or unreadable
inputs fail without an output file; manual status edits are unsupported.
Repository databases are path-validated with bounded member, record, relation,
and expansion limits before confined extraction. Keyring packages expose only
the exact pinned regular member; redirects, archive traversal, links, partial
downloads, changed-during-inspection archives, and non-atomic candidate outputs
are rejected. Archive consumers validate and extract from a private snapshot,
and reject a source that changes while that snapshot is created.

The first reviewed bundle is
`locks/userspace/steamos-3.8.14-nvidia-575.64.05.json`, paired with
`trust/keyrings/archlinux-nvidia-userspace-2025-08-01.gpg`. Its pinned
2025-08-01 closure is `nvidia-utils`, `lib32-nvidia-utils`, `egl-wayland`,
`eglexternalplatform`, `egl-gbm`, and `egl-x11`. Support-owned manifests pin the
full Arch keyring source and the exact dated `core`, `extra`, and `multilib`
database hashes used by the audit.

During validation, stderr contains throttled lines beginning with
`STEAMOS_NVIDIA_PROGRESS ` followed by a schema-1 JSON object. Records contain
only a bounded numeric attempt, a fixed phase, an indeterminate flag, or numeric
unit/completed/total fields—never filesystem paths or free-form messages.
`--progress-attempt 0..1000000` lets OPEMOS.EXE correlate retries. Byte
progress covers one monotonic aggregate of all immutable installer inputs. Its
fixed total is their combined byte size and it never resets between files. Item
progress covers the Holo database, modules, and userspace packages; archive
layout, dependency closure, and storage calculation are explicitly
indeterminate phases.

Mutation uses the same schema and attempt. It reports pacman-policy preparation,
the four runtime mounts, the exact incoming package count, all five
modules, GRUB, depmod, initramfs, installation-state writing, and reverse-order
mount cleanup. Package, module, and mount work uses real item totals. Opaque
operations begin indeterminate and emit a one-item completion only after the
command succeeds; in particular, mkinitcpio output is never converted into a
fabricated percentage. A failed or cancelled command therefore leaves its phase
incomplete while cleanup continues to report independently.

OPEMOS.EXE and other consumers can run `lib/validate_install_contract.py --result
RESULT --progress STDERR_LOG` before accepting either stream. Schema 1 requires
the stable envelope and all successful verification/cleanup records while
allowing additive fields for forward compatibility. Duplicate JSON keys,
truncation, oversized records, invalid counts, decreasing progress, changing
totals/units, incomplete cleanup, and missing structured success records fail
closed. The validator emits its own bounded machine-readable verification result.

Before an offline-root mutation, `lib/snapshot_target_execution.py` records the
target's standard libalpm hooks, hook executors, mkinitcpio executable,
configuration fragments, presets, and initcpio tree. These inputs must be
confined regular files/directories owned like the target root, must not be group-
or world-writable, and may not contain final-input symlinks, missing hook
executors, or local `/etc/pacman.d/hooks` overrides. Confined root-owned
intermediate directory aliases such as SteamOS's relative `/bin -> usr/bin` are
accepted only when both the alias and every resolved directory remain inside the
target and have trusted ownership and modes. The installer revalidates the
snapshot before pacman, then creates a fresh snapshot after the authenticated
package transaction and its own managed configuration write and revalidates it
immediately before mkinitcpio. Any drift fails closed with
`target_execution_trust`, a bounded `targetExecutionFailure` record, and normal
mount cleanup; executable mode alone is never accepted as sufficient trust.

Validation also performs the authoritative storage preflight. It reads each
authenticated package's declared installed size and dependency/provides fields,
resolves the complete incoming-plus-installed dependency closure against parsed
Holo records with pacman's `vercmp`, and accounts for package/module files being
replaced. The result reports `rootAvailableBytes`, `rootRequiredBytes`,
`varAvailableBytes`, `varRequiredBytes`, `efiAvailableBytes`,
`efiRequiredBytes`, `packageInstalledBytes`, `moduleInstalledBytes`, and
`initramfsReserveBytes` under `validation.storage`, together with the dependency
closure and filesystem-compression context. An insufficient target fails before
mutation with `target_space_insufficient` while retaining those fields.

Scratch-measurement failures are schema-1 results rather than undifferentiated
command errors. A bounded `measurementFailure` object records a stable phase,
safe command identity, exit status, and at most 512 sanitized stderr characters.
Missing tools, filesystem creation, loop mounting, package/module extraction,
Zstd, Btrfs usage collection/parsing, ENOSPC, and cleanup have distinct stable
reasons. Host paths, URLs, credentials, control characters, and unbounded tool
output are not propagated. The validator and outer installer result validate
and preserve this object unchanged; malformed diagnostics fail closed.

Root admission uses declared logical package sizes, the estimated final zstd
module sizes, replacement credits, existing initramfs sizes, module growth per
initramfs, and explicit metadata reserves. Btrfs compression is detected and
reported. The signed packages' compressed archive bytes and the difference from
their declared installed sizes are included as an informational proxy indicating
whether the declarations are likely conservative; that proxy is explicitly not
a prediction of Btrfs allocation. No hypothetical compression savings are
credited. The default remains intentionally conservative: a target that fits
only under an assumed compression ratio is rejected unless an explicit measured
profile is requested.

Maintainers can request `--compression-profile btrfs-zstd3`. Validation creates
a disposable sparse Btrfs filesystem, mounts it
with `compress-force=zstd:3`, writes the already-authenticated package payload
and target-format compressed modules, synchronizes it, and measures the delta in
Btrfs allocated `Used` bytes. The structured result retains conservative logical
and measured physical requirements, per-package and module allocation,
data/metadata/system allocation, filesystem overhead, explicit initramfs and
metadata reserves, compression ratio, available space, shortfall, and final
margin. Scratch mounts are checked and released on success, failure, and
cancellation.

Mutation with this profile is fail-closed. Every package, module, configuration,
and `/boot` destination must resolve to the target Btrfs filesystem. Existing
destination ancestors carrying Btrfs NOCOW or NOCOMPRESS attributes are
rejected because the remount cannot make those writes equivalent to the scratch
measurement. Because Btrfs compression mount options are filesystem-wide, the
root filesystem must have exactly one mount in the appliance namespace;
additional mounts of the same filesystem fail before mutation. Immediately
before writing, the installer records the original compression option, remounts
the disposable target with `compress-force=zstd:3`, verifies that policy at each
mutation boundary, then restores and verifies the original option on success,
failure, or cancellation. An exact installed package version skipped by
`pacman --needed`, or an exact five-module payload that the installer does not
rewrite, may receive its measured no-op allocation as replacement credit.
Ordinary upgrades receive no physical credit because old and new extents may
coexist during the transaction. Archive-size savings remain informational and
never authorize installation.

Pacman's logical `CheckSpace` calculation cannot represent this authenticated
physical-allocation admission. The installer therefore suppresses `CheckSpace`
only for an explicitly requested `btrfs-zstd3` mutation whose exact unchanged
validation document says both `admissionAuthorized: true` and
`mutationProfileImplemented: true`, and only after independently verifying the
live target is still mounted with `compress-force=zstd:3`. A mode-0600 temporary
configuration retains the appliance's complete `[options]` policy—including
local signature policy—while dropping repository sections and exactly one
canonical `CheckSpace` directive. Ambiguous directives, option-level includes,
missing authorization, validation drift, or mount-policy drift fail before
pacman. Every other path uses pacman's normal configuration and `CheckSpace`.
The temporary configuration is confined to mutation work and removed on
success, failure, or cancellation.

Independent recovery-overlay validation remains required before this path is
considered release-ready. Terminal result cleanup
records distinguish `mountsReleased` from `compressionPolicyRestored` so the
OPEMOS.EXE does not mistake a restored mount tree for restored Btrfs policy.

The first real Fedora measurement of the reviewed SteamOS 3.8.14/NVIDIA
575.64.05 six-package set plus the five target-format modules produced
528,154,624 allocated payload bytes from 1,220,942,301 declared logical bytes.
With the conservative 162,198,248-byte initramfs reserve and 67,108,864-byte
metadata/safety reserve, the measured requirement is 757,461,736 bytes. The
observed recovery root's 908,500,992 available bytes would leave 151,039,256
bytes. That exact admission can now pass pacman's otherwise contradictory
logical-space gate through the scoped policy above; mutation and final-image
validation gates remain fail-closed.

Prepare the minimal binary keyring from an existing trusted Arch key source:

```bash
python3 bootstrap/prepare_nvidia_package_keyring.py \
  --source /usr/share/pacman/keyrings/archlinux.gpg \
  --output /shared/approved-package-signers.gpg
```

The command accepts armored or binary source material, requires every active
fingerprint in the reviewed manifest, exports only those keys, and refuses to
overwrite an existing output.

Use `--validate-only` before allowing any image mutation. Validation requires
the exact target kernel directory, byte-identical external/embedded provenance,
all five module hashes and metadata, matching signed userspace package releases,
and versioned GSP firmware. Module archives use an exact allowlist with duplicate,
extra, and oversized compressed/decompressed content rejected. Signed userspace
packages are also independently bounded and inspected before `pacman`: duplicate
or noncanonical paths, device/FIFO entries, malformed or escaping links, excessive
member counts, and excessive expanded sizes fail closed. Every existing component
of every project-owned or package-member mutation destination must be confined
beneath the target root and must not be a symlink.

The versioned `--result-json` uses stable bounded status/reason/phase tokens and
logical filenames rather than host paths. Successful validation records the
archive and provenance hashes, reviewed lock identity/hash, keyring identity/hash,
storage accounting, and every package's filenames, full version, architecture,
package/signature hashes, signer, installed size, dependencies, and provides.
Malformed CLI input writes `invalid_arguments` when a result path is available;
duplicate singleton options are rejected. Cancellation escalates from TERM to
KILL after a bounded grace period and reaps the process group before reporting
cleanup. Before validation, every authenticated archive, package, signature,
keyring, lock, provenance document, and optional payload profile is copied into
a private mode-0700 staging tree with bounded per-input sizes. Each copy is
mode 0600 and is rejected if the source is a symlink, changes identity or
metadata while being copied, is replaced at its pathname, or exceeds its
validator-aligned size limit. Validation and mutation use only these snapshots,
which are removed on validation, failure, cancellation, and success paths.
The installer also records the exact rootfs and EFI mount IDs, sources,
filesystem types, device numbers, and stable mount options before input
snapshotting and validation, then requires the same identities afterward.
Those identities are rechecked before userspace/module changes, bootloader
configuration, depmod, initramfs generation, state writing, and cleanup. An
identity mismatch fails closed; cleanup will not recursively unmount paths
through a replaced target mount, revalidating before every owned unmount.
Before pacman, mutation recursively bind-mounts `/dev`, `/proc`, and
`/sys` into the confined target, makes each tree recursively slave, verifies
the exact source/target SOURCE, FSTYPE, MAJ:MIN, and derived FSROOT topology
(so a same-device sibling bind is rejected), and bind-mounts a private appliance-backed
scratch directory at target `/var/tmp`. The target directory must be confined,
nonsymlinked, root-owned, and mode 1777. Validation reports a missing directory
as `preparation-required` without mutating the image; mutation creates only that
missing directory with mode 1777 before establishing runtime mounts. Existing
unsafe objects or permissions remain fail-closed. The backing filesystem must
have at least the validated initramfs reserve and 4096 available inodes.
Filesystems that report a finite inode count use the conservative `statvfs`
value. Filesystems such as Btrfs that report all inode counters as zero must pass
a bounded, cleaned 4,096-file allocation probe; zero is never interpreted
directly as either exhaustion or unlimited capacity. The bind destination itself
receives no inode credit and remains non-mutating during validation. Structured
workspace results record `finite-statvfs`, `dynamic-probed`, or
`not-applicable-bind-target` as the inode-capacity basis. All four mounts remain
through pacman hooks and the explicit `mkinitcpio` run. The final result records
their expected/released counts plus bounded workspace condition and capacity
metadata. Reverse-order recursive cleanup is required on every terminal path,
including pacman-hook failure and cancellation.

Success also requires bounded `initramfsVerification`. The installer hashes each
generated `initramfs-*.img` before a time/byte-capped target `lsinitcpio -l`
capture, then rechecks the unchanged image while producing exact image and
ordered-listing hashes. Every image must contain exactly one copy of the
declared early-boot modules (`nvidia`, `nvidia_modeset`, `nvidia_uvm`, and
`nvidia_drm`) and the managed modprobe configuration. `nvidia-peermem` remains
mandatory in the independently verified rootfs module set but must be absent
from these initramfs images. The result exposes both `requiredModules` and
`rootfsOnlyModules` instead of deriving early-boot contents from all installed
modules. The result binds those
records to the snapshotted mkinitcpio/lsinitcpio identities and configuration
hash; malformed, duplicate security-relevant, oversized, linked, partial, or
drifted inputs fail closed before installation state is committed.

Pacman post-transaction hook failures are detected independently from pacman's
exit status using fixed C-locale failure markers. A reported hook failure stops
the installer before userspace verification or module mutation.

Mutation uses target-root pacman semantics, offline `depmod`,
explicit NVIDIA mkinitcpio configuration, and the target's own `mkinitcpio` in
an x86_64 chroot. Synthetic tests cover success, repeated execution, injected
initramfs failure, and cleanup-safe failure results. On a real recovery image,
the disposable qcow2 overlay is the authoritative rollback boundary and must be
discarded after any non-success result.

Raw and already-zstd-compressed archive modules both install canonically as
root-owned mode-0644 `.ko.zst` files. Raw input is compressed into confined
temporary storage before an explicit install step; archive member modes are
never inherited. Post-install module verification aggregates all five module
outcomes and returns bounded target-relative diagnostics containing
representation, expected/actual decompressed hashes, mode, UID/GID, compressed
size, decompression status, and deterministic invalid-field lists. The final
installer result preserves this module-verification record. Schema-1 success is
rejected unless the record verifies exactly all five modules; exact decompressed
payload equality binds the installed files to the already authenticated module
architecture, NVIDIA version, and target-kernel vermagic.

The module archive safety policy allows at most 1 GiB compressed, 1 GiB for any
individual module member, and 2 GiB total expansion. External and embedded
metadata remain capped at 1 MiB each. These bounds accommodate the observed
632.5 MB development artifact without permitting unbounded extraction. Each
userspace package is capped at 2 GiB compressed, 2 GiB per member, 16 GiB total
declared expansion, 250,000 members, and a 64 MiB bounded archive listing;
signatures, keyrings, locks, checksums, and provenance have smaller type-specific
limits.

Pass `--result-json FILE` when invoking the build from an appliance controller.
The file is written atomically with schema version `1`, target identity, trust
classification, a stable success/failure reason, and artifact filenames and
hash on success. It contains filenames rather than private host paths. Human
logs remain diagnostic output; callers should branch on this JSON contract.
Invalid CLI/target input also writes this contract when `--result-json` is
present, including when an unknown option occurs before it. Stable failure
reasons include `invalid_target`, `unsupported_architecture`, header discovery,
download, signature and identity failures, incomplete header trees, source and
compiler failures, module-set/architecture/vermagic failures, packaging
failures, and `cancelled`.
The appliance manager cancels a running build by sending SIGTERM (SIGINT is
also accepted) to the build-script process. Downloads, extraction, and the
parallel compiler run in dedicated process groups, so cancellation terminates
their descendants before temporary state is removed. Final artifact filenames
are published only after packaging and hashing succeed, and existing outputs
are never overwritten.

For authenticated local builds, provide a reviewed keyring and the exact full
fingerprint expected to sign the Valve headers package:

```bash
./bootstrap/build_for_target.sh \
    ... \
    --header-keyring /appliance/trust/valve-package-signers.gpg \
    --header-signer FULL_HEXADECIMAL_FINGERPRINT
```

The detached `${headers_url}.sig` is downloaded automatically from the same
Valve package location. With `--headers-package`, also pass
`--headers-signature`. `gpgv` must validate the package and the actual signing
key or its reported primary key must match the pinned fingerprint. Supplying a
keyring without an exact fingerprint is rejected. The project intentionally
does not download a keyring and then trust it from the same transaction. A
SHA256 calculated after downloading headers is recorded as provenance, not
described as authentication; verified status requires the detached signature.

The reviewed trust inputs live in `trust/valve-package-signers.json`. Prepare
the exact pinned keyring for an appliance with:

```bash
python3 bootstrap/prepare_valve_keyring.py \
    --output /appliance/trust/valve-package-signers.gpg
```

The helper rejects redirects, verifies the committed SHA256 of Valve's official
`holo-keyring` package, extracts only its declared keyring, verifies that second
committed hash, confirms the pinned signer is present, converts Valve's armored
key collection into the binary keyring format required by `gpgv`, and writes the
result atomically. It requires `bsdtar` and GnuPG (`gpg`/`gpgv`). For offline/reproducible
appliance construction, pass the already downloaded package with `--package`.
The currently pinned historical-header signer is
`889B5EBDDD505A683621900DAF1D2199EF0A3CCF`, identified in Valve's keyring as
`GitLab CI Package Builder <ci-package-builder-1@steamos.cloud>`.
Builds require the requested fingerprint to be marked `active` in the committed
trust manifest; an arbitrary caller-supplied keyring cannot confer verified
status. Key rotation adds a newly reviewed active entry, while revocation keeps
the historical entry and changes its status to `revoked`, causing builds to
fail closed before downloading or compiling.

Previously authenticated headers can be moved through an offline archive
without turning a locally calculated hash into trust evidence:

```bash
python3 lib/authenticated_cache_bundle.py export \
    --artifact linux-neptune-headers.pkg.tar.zst \
    --signature linux-neptune-headers.pkg.tar.zst.sig \
    --keyring /appliance/trust/valve-package-signers.gpg \
    --reviewed-signers trust/valve-package-signers.json \
    --output /media/headers-cache.bundle
python3 lib/authenticated_cache_bundle.py import \
    --bundle /media/headers-cache.bundle \
    --keyring /appliance/trust/valve-package-signers.gpg \
    --reviewed-signers trust/valve-package-signers.json \
    --store /appliance/cache/imported
```

Both operations re-run `gpgv`, require the same reviewed active signer and
keyring digest, and bound every input. Imports are immutable generations named
by the canonical manifest hash and print a schema-1 machine result containing
the verified artifact path. Corrupt, partial, symlinked, policy-drifted, or
oversized bundles fail before a generation is published; importing an exact
generation again is idempotent. The output is suitable for passing back to
`build_for_target.sh --headers-package` with its bundled signature. It is not a
substitute for archiving reviewed keyrings and signer policy independently.

Userspace closures and certified release assets use the multi-artifact form.
Its JSON spec lists `{artifact, signature}` pairs; the generation additionally
contains the exact reviewed policy and provenance documents:

```bash
python3 lib/authenticated_cache_bundle.py export-set \
    --spec package-set.json --policy userspace-lock.json \
    --provenance certified-provenance.json \
    --keyring /appliance/trust/valve-package-signers.gpg \
    --reviewed-signers trust/valve-package-signers.json \
    --output /media/certified-userspace.bundle
python3 lib/authenticated_cache_bundle.py import-set \
    --bundle /media/certified-userspace.bundle \
    --keyring /appliance/trust/valve-package-signers.gpg \
    --reviewed-signers trust/valve-package-signers.json \
    --store /appliance/cache/imported
```

The manifest is canonical and bounded to 64 uniquely named artifacts and 8 GiB
total. Every import revalidates every detached signature and exact signer,
keyring, policy, provenance, size, and hash before atomically publishing an
immutable generation. Unexpected entries, duplicates, partial copies, symlinks,
policy drift, and corrupt existing generations fail closed.

Validate or install a local archive through the same online orchestration path:

```bash
./bootstrap/online_install.sh --local /path/to/nvidia-open-....tar.gz
```

Build the current NVIDIA source checkout and pass it to the installer:

```bash
./bootstrap/online_install.sh --in-code
```

Use `--help` on an entry point before running a system-changing workflow.
Resolution-only and build-only operations state that they do not replace
kernel modules.

## Version and archive safety

An install archive must match all of the following:

- current SteamOS version, unless fuzzy matching was explicitly requested;
- exact running Neptune kernel;
- exact installed NVIDIA userspace version;
- exact module vermagic;
- exactly the five expected NVIDIA modules.

The expected set is:

```text
nvidia.ko
nvidia-drm.ko
nvidia-modeset.ko
nvidia-peermem.ko
nvidia-uvm.ko
```

Release archives may contain raw `.ko` files or compressed `.ko.zst` files.
Installed modules are stored as `.ko.zst`. Health checks compare uncompressed
module content, so archive and installed compression formats may differ.

## SteamOS storage policy

SteamOS has a small root filesystem. Large source trees, container storage,
build output, extraction directories, staging data, and transaction backups
belong under the user cache on `/home`:

```text
~/.cache/open-gpu-kernel-modules-steamos-support/
```

The pre-OPEMOS identifier in cache, state, lock, and module-directory paths is
intentional. Those paths are a stable on-disk compatibility contract, not the
project's display name; renaming them would strand existing installation state
and rollback data.

Offline-target build sessions hold a per-session advisory lock. At the next
build, recognized `target-build.*` directories older than 24 hours are removed
only when that lock can be acquired; active, recent, symlinked, and unknown
cache entries are preserved.

Existing release bundles are reused only for clean source/support checkouts and
`locally-built-verified` metadata. Before a cache hit, the archive, checksum,
BUILD-INFO, provenance, canonical filenames, exact identities, five-module
inventory, and archive contents must pass the same validator used at the
publication boundary. Unverified or malformed bundles are rebuilt.

Only the final compressed module set is copied into:

```text
/usr/lib/modules/<kernel>/updates/open-gpu-kernel-modules-steamos/
```

The installer performs a replacement-aware free-space preflight before
removing the existing module directory. Fedora container `/tmp` is an
intentional exception: it lives inside rootless Podman storage under `/home`,
not in host SteamOS `/tmp`.

## Installation state and rollback

Installed state is recorded under:

```text
/var/lib/open-gpu-kernel-modules-steamos-support/
```

The offline image installer additionally commits a payload receipt under the
root filesystem:

```text
/usr/lib/open-gpu-kernel-modules-steamos-support/offline-install/receipt.json
```

It binds the exact target identity to hashes of build information, provenance,
input validation, five-module verification, userspace verification, and
initramfs verification. Evidence files are written first and the manifest is
atomically replaced last, so interruption cannot create a trusted partial
receipt. This location is separate from recovery `/var`, which Valve may not
propagate. OPEMOS.EXE must still verify the receipt and payload on the newly
installed disk; receipt presence alone is not install or hardware
certification.

Transaction backups are retained under the user cache on `/home`. Install and
uninstall cleanup paths restore the previous module directory, refresh module
dependencies, rebuild initramfs when available, and restore SteamOS read-only
state after a failed transaction. After a successful transaction, recognized
backup generations are limited to the ten newest generations younger than 90
days; the just-created rollback generation is always protected. Unknown entries
and symlinks are never pruned, and retention failure preserves every backup.

Low-level installers never reboot automatically. `online_install.sh` owns the
normal end-to-end reboot prompt. A standalone pristine-upstream workflow may
opt into a prompt with `setup_nvidia.sh --offer-reboot`.

## Current development target

The current integration target is a validated end-to-end recovery-image
mutation using the reviewed NVIDIA modules and userspace closure. Remaining
release gates include fresh-stock installation, Valve repair/A-B propagation,
independent exported-image inspection, and physical NVIDIA hardware boot.

See the [project TODO](https://github.com/CorniiDog/OPEMOS/blob/main/TODO.md)
for the detailed checklist and test status.

## Local checks

Run the non-destructive repository checks with:

```bash
./tests/check.sh
```

This checks shell syntax, whitespace, local `--help` behavior, mutually
exclusive resolver modes, terminology, and pre-bootstrap temp-helper ordering.

## Non-sudo reinstall baseline

Run the user-space regression baseline before reinstalling SteamOS:

```bash
./tests/non_sudo.sh --online \
    --report "$HOME/.cache/open-gpu-kernel-modules-steamos-support/baselines/pre-reinstall.txt"
```

The runner places a failing `sudo` shim first in `PATH`, so any unexpected
privilege request fails the test. It checks cache ownership, non-root `zstd`
staging, exact module sets, detached-HEAD semantics, certified release policy,
the cached artifact, the installer validation/cancellation boundary, and all
three resolver modes. Omit `--online` for a fast offline run.

The fast checks also run `tests/transaction.sh`. This redirects all privileged
paths into a temporary fake system root and uses mocked lifecycle commands. It
verifies byte-for-byte rollback after a partial module copy, initramfs failure,
state-write failure, and post-removal uninstall failure, plus a successful
install/uninstall cycle. It snapshots the real project module directory before
and after to prove that the live installation was untouched.

On a macOS host, run the same Bash 4+ transaction coverage in a disposable,
headless Fedora guest with:

```bash
./tests/vm/run.sh
```

The runner pins and verifies the Fedora 42 cloud image, creates a fresh sparse
overlay and NoCloud seed on every invocation, uses QEMU user-mode networking
(never bridged networking or SSH), and returns a schema-1 JSON result over the
serial console. It also probes advisory locking, private mount namespaces, and
a synthetic loop-backed Btrfs filesystem. A disposable recovery/A-B fixture
also proves pre-mutation space refusal, signal rollback, inactive-slot
activation, active-slot preservation, and repeat execution using real Btrfs
subvolumes and read-only snapshots. Guest disks, seed media, logs,
sockets, and runtime files stay under ignored `tests/vm/.cache` and
`tests/vm/.runtime` directories. The base image is the only persistent cache;
delete it to force a verified refetch. This harness does not prove real
SteamOS pacman/mkinitcpio behavior, Valve bootloader propagation, or NVIDIA
hardware boot behavior.

The guest additionally runs a deterministic pacman/mkinitcpio compatibility
fixture inside a real Linux `chroot`. It installs the exact five-module set,
runs a package hook that creates a reproducible gzip/newc initramfs, and inspects
the image for all modules and the NVIDIA modprobe policy. Capacity refusal,
hook failure, process-group cancellation, rollback, and repeat execution are
covered. These fixture commands model transaction boundaries; they are not the
real Arch pacman or Valve mkinitcpio packages.

For an optional real Arch Linux boundary test, run:

```bash
./tests/vm/run-arch.sh
```

This separate headless guest pins the immutable Arch cloud image
`20260815.573966`, verifies its official signed SHA-256 document with the CI key
published by the Arch `arch-boxes` project at source commit
`7f733af26fe9a0c93fdabba13e7803cbe803374a`, and refuses missing, linked,
corrupt, or mismatched inputs. Inside the disposable guest it uses actual signature-
enforcing `pacman`, installs/verifies actual `mkinitcpio`, regenerates and
inspects the guest initramfs, rejects a missing package without a stale database
lock, cancels a process-group generation loop, and compares image contents after
a clean repeat. `--no-download` requires all three already verified cache inputs.
This proves upstream Arch tooling behavior, not Valve's package set, hooks,
presets, recovery propagation, or SteamOS boot behavior.

With both verified base images already cached, the authenticated offline-cache
contract can be exercised concurrently and without downloads in isolated Fedora
and Arch guests:

```bash
./tests/vm/run-offline-cache-matrix.sh
```

The matrix returns one schema-1 JSON result containing each guest result and
uses only the existing pinned caches; it never falls back to the network.

The offline-root installer can select the imported userspace/certified inputs
directly without translating them into an online or loose-file fallback:

```bash
bootstrap/install_to_root.sh \
    --input-source authenticated-bundle \
    --authenticated-bundle /media/certified-userspace.bundle \
    --bundle-store /appliance/cache/imported \
    --bundle-keyring /appliance/trust/archlinux-nvidia-userspace-2025-08-01.gpg \
    --bundle-reviewed-signers trust/nvidia-userspace-package-signers.json \
    --bundle-steamos 3.8.14 --bundle-nvidia 575.64.05 \
    --root /target-root --archive /appliance/modules.tar.gz \
    --checksum /appliance/modules.tar.gz.sha256 \
    --kernel 6.11.11-valve19-1-neptune-611 \
    --result-json /appliance/results/install.json
```

Bundle mode is explicit and mutually exclusive with loose userspace,
signature, lock, provenance, keyring, and dependency arguments. It reimports
and revalidates the immutable generation, requires the exact reviewed target,
package set, hashes, signatures, keyring, policy, and provenance, then uses the
normal private installer snapshots and full validation path. There is no
network fallback. Validation/results identify `authenticated-bundle` plus the
canonical cache ID; cancellation or policy drift fails before target mutation.

Prune the imported generation store under exact count and byte limits with:

```bash
python3 lib/prune_authenticated_cache.py \
    --store /appliance/cache/imported --max-count 8 \
    --max-bytes 17179869184 --protect CACHE_ID
```

The pruner shares the importer lock, automatically protects active installer
leases, and refuses missing, corrupt, or over-budget protected generations.
Unprotected symlinks, partial entries, corrupt generations, and excess valid
generations are moved into a private rollback directory before deletion.
Cancellation restores anything already moved. Its schema-1 result records every
keep/remove decision and the exact retained count and bytes. Bundle-mode
installers acquire a generation lease while importing and release it on every
validation, failure, cancellation, and successful mutation path.

An optional Valve-recovery controller is available as
`tests/vm/run-steamos-recovery.sh`. `--fixture` runs the deterministic disposable
A/B filesystem, preset, hook, rollback, and idempotency path. The real-media form
accepts only `--archive /absolute/path/to/steamdeck-recovery-*.img.bz2`; it never
downloads recovery media or accepts a URL/hash supplied on the command line.
Instead it requires a reviewed immutable filename, compressed size/hash, raw
size, release identity, and Valve source-evidence record in
`trust/steamos-recovery-images.json`, streams decompression with an exact 32-GiB
cap, and attaches the raw image read-only to a pinned Fedora serial controller.
The controller mounts recovery partitions read-only/noexec, locates exactly one
SteamOS root, checks the real Holo database, mkinitcpio executable/presets, and
libalpm hooks, then propagates those inputs into temporary guest-local A/B image
files. It performs no SSH, GUI, bridging, host mounts, or writes to the recovery
media. The committed manifest is deliberately `unconfigured`: Valve's current
official support/download pages do not publish a checksum file or detached
signature, so real-media execution fails closed until trustworthy immutable
evidence is reviewed and pinned. Even then, this controller proves filesystem
and transaction compatibility only—not Valve Secure Boot or NVIDIA hardware.

After reinstalling and cloning the same support commit, run:

```bash
./tests/non_sudo.sh --online \
    --report "$HOME/.cache/open-gpu-kernel-modules-steamos-support/baselines/post-reinstall.txt"

diff -u \
    "$HOME/.cache/open-gpu-kernel-modules-steamos-support/baselines/pre-reinstall.txt" \
    "$HOME/.cache/open-gpu-kernel-modules-steamos-support/baselines/post-reinstall.txt"
```

The report is line-oriented `key=value` data. SteamOS/kernel/module differences
are expected at specific reinstall stages; `result=pass` and
`sudo_invocations=0` must remain stable. Preserve the pre-reinstall report
outside `/home` too if the reinstall procedure will erase the home partition.
