<p align="center">
  <img src="docs/assets/images/opemos-pill.svg" alt="OPEMOS gradient pill" width="112">
</p>

<h1 align="center">OPEMOS</h1>

<p align="center"><strong>Command-line packaging and exact-kernel NVIDIA enablement for SteamOS.</strong></p>

<p align="center">
  <a href="https://github.com/CorniiDog/OPEMOS/actions/workflows/shell.yml"><img src="https://img.shields.io/github/actions/workflow/status/CorniiDog/OPEMOS/shell.yml?branch=main&amp;style=for-the-badge&amp;logo=gnubash&amp;logoColor=white&amp;label=checks&amp;labelColor=192c3c" alt="OPEMOS checks status"></a>
  <a href="https://github.com/CorniiDog/OPEMOS/actions/workflows/pages.yml"><img src="https://img.shields.io/github/actions/workflow/status/CorniiDog/OPEMOS/pages.yml?branch=main&amp;style=for-the-badge&amp;logo=githubpages&amp;logoColor=white&amp;label=docs&amp;labelColor=192c3c" alt="OPEMOS documentation status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1a9fff?style=for-the-badge&amp;labelColor=192c3c" alt="MIT license"></a>
</p>

OPEMOS—**Open Packaging for Exact-kernel Modules on SteamOS**—is the unofficial
command-line and automation toolkit for building, authenticating, packaging,
and installing NVIDIA open kernel modules matched to an exact SteamOS release,
Neptune kernel, architecture, and NVIDIA userspace version. It fails closed
rather than substituting a nearby kernel or unreviewed userspace payload.

## Choose an interface

| I want to… | Use |
| --- | --- |
| Build a recovery image through a graphical macOS application | [OPEMOS.EXE](https://github.com/CorniiDog/OPEMOS.EXE) · [Documentation](https://corniidog.github.io/OPEMOS.EXE/) |
| View installed NVIDIA recovery health in SteamOS Desktop Mode | [OPEMOS native status companion](desktop/README.md) |
| Test or integrate the temporary no-input boot display | [OPEMOS DRM/KMS interstitial](docs/interstitial.md) |
| Inspect, build, validate, publish, or install from a terminal or automation | **OPEMOS CLI** · continue below |

> [!IMPORTANT]
> This project is under active development. Published artifacts are
> exact-kernel builds, but a completely fresh-stock installation and NVIDIA
> hardware boot still require end-to-end certification. Read the trust status
> shown by the resolver; do not treat `locally-built-verified` as
> `certified-published`.

## Start here

Read the **[OPEMOS documentation](https://corniidog.github.io/OPEMOS/)**.

- [Install from a Steam Deck terminal](docs/getting-started.md)
- [Developer tutorials](docs/developer-guide.md)
- [OPEMOS.EXE integration](docs/image-builder.md)
- [OPEMOS.EXE graphical application](https://corniidog.github.io/OPEMOS.EXE/)
- [Command and JSON contracts](docs/contracts.md)
- [No-input boot interstitial](docs/interstitial.md)
- [Trust, safety, and recovery model](docs/security.md)
- [Complete technical reference](docs/technical-reference.md)
- [Current roadmap](TODO.md)

## Steam Deck terminal installation

Open Konsole in Desktop Mode. Inspect the selected release without changing
the system:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --resolve-only
```

Install the matching published modules and userspace:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/OPEMOS/main/bootstrap/online_install.sh?x=$(date +%s)")
```

The canonical installer also installs a persistent boot guardian. On every
activated SteamOS slot it verifies all five NVIDIA modules against the running
kernel and installed userspace before the display manager starts. If an A/B
update activates a kernel without matching modules, it enters a console-safe
recovery profile instead of allowing a black graphical boot. Inspect or repair
it through the UI-neutral JSON contract:

```bash
sudo /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/recoveryctl.sh status --json
sudo /home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/recoveryctl.sh repair-online --json
```

Automatic fallback disables both NVIDIA and Nouveau. An Intel/AMD boot-VGA
desktop is accepted only after hardware validation; Nouveau is experimental
and requires the explicit `--allow-nouveau` option. Fallback is removed only
after exact NVIDIA module verification succeeds.

The online bootstrap currently downloads from mutable `main`; review the
[public installer trust limitations](docs/security.md#public-online-installer)
before using it on a production system. For local review, clone the repository
and inspect help before mutation:

```bash
git clone https://github.com/CorniiDog/OPEMOS.git opemos
cd opemos
./bootstrap/setup_nvidia.sh --resolve-only
./bootstrap/online_install.sh --help
```

Uninstall:

```bash
cd ~/opemos
./bootstrap/uninstall.sh
```

## Development

Run the non-destructive local suite:

```bash
./tests/check.sh
```

On Apple Silicon, use the pinned x86_64 Fedora VM for Linux-only transaction,
Btrfs, mount, chroot, and cancellation coverage:

```bash
./tests/vm/run.sh
```

## Repository boundaries

| Repository | Responsibility |
| --- | --- |
| [`OPEMOS`](https://github.com/CorniiDog/OPEMOS) | CLI workflows, exact artifact resolution, builds, userspace locks, offline installation, provenance, and publication |
| [`OPEMOS.EXE`](https://github.com/CorniiDog/OPEMOS.EXE) | Desktop UI, recovery-image inspection, appliance lifecycle, safe image export, and independent final-image validation |

The authoritative, read-only repository and UI exception rules are in
[`BOUNDARIES.md`](BOUNDARIES.md). Responsibility summaries elsewhere are
non-authoritative.
| [`open-gpu-kernel-modules-steamos`](https://github.com/CorniiDog/open-gpu-kernel-modules-steamos) | Versioned NVIDIA source branches and SteamOS-specific patches |

## Safety properties

- Exact target kernel, architecture, NVIDIA version, module vermagic, and
  five-module inventory are mandatory.
- Normal resolution fails closed instead of selecting a closest kernel.
- Offline installation uses authenticated, reviewed local inputs and never
  examines the Fedora appliance's running kernel.
- Image mutation is confined to a disposable overlay; the original recovery
  image must remain unchanged.
- Failed or cancelled transactions require verified mount, compression-policy,
  and temporary-state cleanup before their result can be trusted.

See the [security model](docs/security.md) and
[complete technical reference](docs/technical-reference.md) for the exact
contracts and remaining certification gates.
