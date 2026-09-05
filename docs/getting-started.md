---
layout: page
title: Steam Deck terminal
description: Inspect, install, test, and remove NVIDIA support from a native SteamOS terminal.
---

## Contents

- [Before you begin](#before-you-begin)
- [Inspect without changing the system](#inspect-without-changing-the-system)
- [Install a published build](#install-a-published-build)
- [Use a reviewed local checkout](#use-a-reviewed-local-checkout)
- [Development and upstream control modes](#development-and-upstream-control-modes)
- [Install a local artifact](#install-a-local-artifact)
- [Uninstall](#uninstall)
- [Where files are stored](#where-files-are-stored)

## Before you begin

Use Desktop Mode and open Konsole. Keep the Deck on external power, preserve
important data, and read the resolver's trust classification before installing.
SteamOS updates can replace the active A/B system slot, so an install that works
today is not automatically carried into a future SteamOS image.

The inspection and published-install entrypoints check these executable names
before they create a working directory or request administrator access:

```text
curl
git
modinfo
python3
realpath
sha256sum
tar
zstd
```

On SteamOS, `modinfo` is provided by the `kmod` package, `python3` by the
`python` package, and `realpath` plus `sha256sum` by GNU coreutils. NVIDIA
userspace setup also preflights `awk`, `sort`, `tar`, `grep`, `tail`, `mkdir`,
`rm`, `sudo`, `pacman`, `ldconfig`, `cp`, `install`, `sed`, and `tee` before the
phase that uses them. The standard SteamOS base supplies these through its
base shell/coreutils, pacman, kmod, and privilege packages.

On a stock SteamOS image, use Valve's supported package-management workflow to
restore a missing prerequisite. Do not paste an unreviewed command that disables
read-only mode or refreshes package databases merely to satisfy this list. The
entrypoint reports `Missing command: NAME` and exits before installation when a
required tool is absent.

Development source builds additionally require rootless Podman with its graph
root under the user's home directory. QEMU, host storage tools, and Linux/macOS
application dependencies belong to OPEMOS.EXE host setup and are not Core CLI
prerequisites.

The expected kernel module set is exactly:

```text
nvidia.ko
nvidia-drm.ko
nvidia-modeset.ko
nvidia-peermem.ko
nvidia-uvm.ko
```

## Inspect without changing the system

The resolution-only path reports the selected SteamOS, kernel, NVIDIA version,
release, and trust state without installing userspace or modules:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --resolve-only
```

`no_compatible_artifact` is a safe result. It means an exact artifact has not
been published; it is not permission to use a closest kernel.

## Install a published build

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_install.sh?x=$(date +%s)")
```

The orchestrator selects matching userspace, validates the release artifact,
installs the modules, refreshes dependency metadata and initramfs state, and
owns the optional reboot prompt. Low-level installers do not reboot by
themselves.

The initial `curl` currently comes from mutable `main`. That gives run
consistency after download but is not independent bootstrap authentication.
See [Public online installer](security.md#public-online-installer).

## Use a reviewed local checkout

This path lets you inspect the exact scripts before running them:

```bash
cd ~
git clone https://github.com/CorniiDog/OPEMOS.git opemos
cd opemos
./bootstrap/setup_nvidia.sh --resolve-only
./bootstrap/online_install.sh --help
./bootstrap/online_install.sh
```

Before rerunning after a SteamOS reinstall, capture a non-sudo baseline:

```bash
./tests/non_sudo.sh --online \
  --report "$HOME/.cache/open-gpu-kernel-modules-steamos-support/baselines/pre-reinstall.txt"
```

## Development and upstream control modes

Select matching userspace for a project source branch without replacing kernel
modules:

```bash
./bootstrap/setup_nvidia.sh --development 580 --resolve-only
```

Build and install pristine NVIDIA upstream as a control case, without project
patches:

```bash
./bootstrap/setup_nvidia.sh --use-upstream 580 --resolve-only
./bootstrap/setup_nvidia.sh --use-upstream 580 --yes --offer-reboot
```

Build a pristine archive without replacing installed modules:

```bash
./bootstrap/install_upstream.sh --build-only 580.119.02
```

These are development workflows. They must not be presented as certified
published installation.

## Install a local artifact

Route a local archive through the normal orchestration and validation path:

```bash
./bootstrap/online_install.sh --local /absolute/path/to/nvidia-open-....tar.gz
```

Compile the current NVIDIA source checkout and pass it to the installer:

```bash
./bootstrap/online_install.sh --in-code
```

## Uninstall

From the repository checkout:

```bash
./bootstrap/uninstall.sh
```

The transaction restores module dependencies, initramfs state when available,
and SteamOS read-only state. A failed rollback remains a failure; do not treat
partial cleanup as success.

## Where files are stored

OPEMOS retains its original internal directory identifier so upgrades and
uninstalls can find existing state.

Large source trees, build output, backups, and containers stay on `/home`:

```text
~/.cache/open-gpu-kernel-modules-steamos-support/
```

Installed state is recorded under:

```text
/var/lib/open-gpu-kernel-modules-steamos-support/
```

Only the final module set belongs under the exact running kernel's module tree.
