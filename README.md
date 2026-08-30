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

Schema version `1` returns `status=compatible` with certification, archive, and
SHA256-sidecar URLs; `no_compatible_artifact`, `unsupported_target`, and
`invalid_target` are normal fail-closed results and contain no downloadable
artifact. The resolver requires an exact kernel match and applies only the same
bounded, non-forward SteamOS major/minor fallback used by the live installer.

When no certified artifact exists, an x86_64 Fedora builder appliance can create
one without using its running kernel:

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
absolute or traversal archive paths. After extraction, the exact-kernel build
tree and every required prepared-tree file must resolve inside the disposable
extraction root; symlink escapes are rejected. Extraction also retains
libarchive's default intermediate-symlink protections, uses atomic safe writes,
and does not restore package ownership or permissions.

The build records the compiler used by Valve's kernel and the compiler used for
the external modules. If their major versions differ, it prefers an installed
`gcc-MAJOR` compatibility compiler and otherwise keeps the output explicitly
`development-unverified`. Use `--require-compiler-major-match` when a caller
must fail instead. Exact compiler version, binutils, make, kmod, Fedora identity,
support/source commits and dirty state, and per-module hashes/version/vermagic
are recorded in `BUILD-INFO.txt`.

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
does not download a keyring and then trust it from the same transaction.

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
