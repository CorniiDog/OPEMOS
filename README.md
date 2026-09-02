# SteamOS NVIDIA Open Kernel Module Support

Build and install NVIDIA open kernel modules matched to an exact SteamOS
release, Neptune kernel, architecture, and NVIDIA userspace version.

> [!IMPORTANT]
> This project is under active development. Published artifacts are
> exact-kernel builds, but a completely fresh-stock installation and NVIDIA
> hardware boot still require end-to-end certification. Read the trust status
> shown by the resolver; do not treat `locally-built-verified` as
> `certified-published`.

## Documentation

The full documentation is available at:

**https://corniidog.github.io/open-gpu-kernel-modules-steamos-support/**

- [Install from a Steam Deck terminal](docs/getting-started.md)
- [Developer tutorials](docs/developer-guide.md)
- [Image-builder integration](docs/image-builder.md)
- [Command and JSON contracts](docs/contracts.md)
- [Trust, safety, and recovery model](docs/security.md)
- [Complete technical reference](docs/technical-reference.md)
- [Current roadmap](TODO.md)

## Steam Deck terminal installation

Open Konsole in Desktop Mode. Inspect the selected release without changing
the system:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_setup_nvidia.sh?x=$(date +%s)") --resolve-only
```

Install the matching published modules and userspace:

```bash
cd ~ && bash <(curl -fsSL "https://raw.githubusercontent.com/CorniiDog/open-gpu-kernel-modules-steamos-support/main/bootstrap/online_install.sh?x=$(date +%s)")
```

The online bootstrap currently downloads from mutable `main`; review the
[public installer trust limitations](docs/security.md#public-online-installer)
before using it on a production system. For local review, clone the repository
and inspect help before mutation:

```bash
git clone https://github.com/CorniiDog/open-gpu-kernel-modules-steamos-support.git
cd open-gpu-kernel-modules-steamos-support
./bootstrap/setup_nvidia.sh --resolve-only
./bootstrap/online_install.sh --help
```

Uninstall:

```bash
cd ~/open-gpu-kernel-modules-steamos-support
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

The support repository owns target detection, builds, validation, installers,
trust policy, and release metadata. The companion
[`open-gpu-kernel-modules-steamos`](https://github.com/CorniiDog/open-gpu-kernel-modules-steamos)
repository owns versioned NVIDIA source branches and SteamOS-specific source
patches.

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
