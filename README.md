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
./bootstrap/online_install.sh
```

### Patched-module development

```bash
./bootstrap/setup_nvidia.sh --development 580 --resolve-only
```

`--development` selects matching NVIDIA userspace for project source work. It
does not build or replace kernel modules. This mode is intended for developing
and testing a project branch such as `nvidia/580.119.02`.

### Pristine upstream control

```bash
./bootstrap/setup_nvidia.sh --use-upstream 580 --resolve-only
```

`--use-upstream` selects matching userspace and, without `--resolve-only`,
builds and installs pristine NVIDIA upstream modules. Project patches are not
applied. This establishes the control case used to decide whether a bug belongs
to NVIDIA modules, Gamescope, or their interaction.

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
