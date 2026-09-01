# SteamOS NVIDIA Open Kernel Module Support

Build, install, and maintain NVIDIA open kernel modules matched to a specific
SteamOS release, Neptune kernel, and NVIDIA userspace version.

This project is under active development. SteamOS 3.8.16 has been tested with
the published NVIDIA 575.64.05 project release and with pristine upstream
NVIDIA 580.119.02 as a development control case. A completely fresh-stock
installation still needs end-to-end validation.

## Repository responsibilities

Two project repositories and NVIDIA upstream have deliberately separate jobs:

| Repository | Responsibility |
| --- | --- |
| `CorniiDog/open-gpu-kernel-modules-steamos-support` | SteamOS detection, NVIDIA userspace setup, contained builds, archive validation, installation, uninstall, release selection, and online entry points |
| `CorniiDog/open-gpu-kernel-modules-steamos` | Project-owned NVIDIA source branches and individual SteamOS compatibility patches |
| `NVIDIA/open-gpu-kernel-modules` | Pristine upstream source and tags used as control baselines |

The support repository owns build and release metadata. The source repository
owns patch history. Project releases must identify the exact source commit used
to build their modules.

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
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_install.sh?x=$(date +%s)")
```

### Patched-module development

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --development 580 --resolve-only
```

`--development` selects matching NVIDIA userspace for project source work. It
does not build or replace kernel modules. This mode is intended for developing
and testing a project branch such as `nvidia/580.119.02`.

Install the selected userspace, then create or refresh the matching source
branch from the exact NVIDIA upstream tag in one command:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --development 580 --yes && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_dev.sh?x=$(date +%s)")
```

### Pristine upstream control

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --use-upstream 580 --resolve-only
```

`--use-upstream` selects matching userspace and, without `--resolve-only`,
builds and installs pristine NVIDIA upstream modules. Project patches are not
applied. This establishes the control case used to decide whether a bug belongs
to NVIDIA modules, Gamescope, or their interaction.

Install the selected userspace and build and install the pristine upstream
modules in one command:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --use-upstream 580 --yes --offer-reboot
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
The final result contract names the sidecar so the image builder can copy it
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
`CorniiDog/open-gpu-kernel-modules-steamos-support`, verifies `gh`
authentication and push permission, and updates only the derived exact release.
Add `--create-only` to fail if that release already exists. A noncanonical
repository requires the conspicuous `--development-repository OWNER/REPO`
override. The publisher never discovers, deletes, or modifies unrelated
releases.

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
versions/pkgrels, and signer fingerprints so the image builder can additionally
pin the exact prepared keyring artifact.

Normal installation requires `--userspace-lock FILE` plus the complete package
set. The reviewed schema-1 lock pins the exact SteamOS/NVIDIA target, minimal
keyring hash, package/signature filenames and hashes, versions, architectures,
installed sizes, dependency/provides metadata, and package-specific signers.
Repeated `--dependency-package FILE --dependency-signature FILE` arguments must
match that lock exactly: missing and extra packages both fail closed. Every
dependency is locally staged, signature-verified, path-confined, size-accounted,
and installed in one offline `pacman -U` transaction. Production installation
never accesses a package repository or expands signer trust.

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
`--progress-attempt 0..1000000` lets the image builder correlate retries. Byte
progress covers hashing; item progress covers the Holo database, modules, and
userspace packages; archive layout, dependency closure, and storage calculation
are explicitly indeterminate phases.

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

Maintainers can request `--compression-profile btrfs-zstd3` during
`--validate-only`. This creates a disposable sparse Btrfs filesystem, mounts it
with `compress-force=zstd:3`, writes the already-authenticated package payload
and target-format compressed modules, synchronizes it, and measures the delta in
Btrfs allocated `Used` bytes. The structured result retains conservative logical
and measured physical requirements, data/metadata/system allocation, filesystem
overhead, explicit initramfs and metadata reserves, and whether the measurement
fits the target's reported free space. Scratch mounts are checked and released
on success, failure, and cancellation.

This profile is intentionally validation-only. A non-validation installer call
with the profile fails before mutation with
`compression_profile_mutation_not_implemented`. It must not become installable
until the target-root compression policy can be applied and restored exactly and
an independent post-install validator covers package ownership/content, modules,
initramfs, and final Btrfs state. Archive-size savings remain informational and
never authorize installation.

The first real Fedora measurement of the reviewed SteamOS 3.8.14/NVIDIA
575.64.05 six-package set plus the five target-format modules produced
528,154,624 allocated payload bytes from 1,220,942,301 declared logical bytes.
With the conservative 162,198,248-byte initramfs reserve and 67,108,864-byte
metadata/safety reserve, the measured requirement is 757,461,736 bytes. The
observed recovery root's 908,500,992 available bytes would leave 151,039,256
bytes. This is measurement evidence, not permission to bypass the still-blocked
mutation and final-image validation gates.

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
cleanup. Mutation uses target-root pacman semantics, offline `depmod`,
explicit NVIDIA mkinitcpio configuration, and the target's own `mkinitcpio` in
an x86_64 chroot. Synthetic tests cover success, repeated execution, injected
initramfs failure, and cleanup-safe failure results. On a real recovery image,
the disposable qcow2 overlay is the authoritative rollback boundary and must be
discarded after any non-success result.

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

Transaction backups are retained under the user cache on `/home`. Install and
uninstall cleanup paths restore the previous module directory, refresh module
dependencies, rebuild initramfs when available, and restore SteamOS read-only
state after a failed transaction.

Low-level installers never reboot automatically. `online_install.sh` owns the
normal end-to-end reboot prompt. A standalone pristine-upstream workflow may
opt into a prompt with `setup_nvidia.sh --offer-reboot`.

## Current development target

Pristine upstream 580.119.02 is the control baseline. The next graphics work is
to reproduce the remaining Gamescope/NVIDIA symptom consistently, capture the
relevant Gamescope and kernel logs, create a project `nvidia/580.119.02` branch,
and compare the smallest possible patch against the pristine build.

See [TODO.md](TODO.md) for the detailed checklist and test status.

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
