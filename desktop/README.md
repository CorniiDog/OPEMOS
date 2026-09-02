# OPEMOS Desktop Mode status

`opemos-recovery-status` is a small native Rust companion for SteamOS and Arch
Linux. It displays the installed OPEMOS boot guardian's bounded schema-1 status
using the same Steam-blue, NVIDIA-green, dark-glass visual language as
OPEMOS.EXE.

The application is deliberately read-only. It does not run as root, invoke
`sudo`, install drivers, select a kernel, or reproduce recovery policy. Repair
and fallback changes remain explicit `recoveryctl.sh` terminal operations.

## Build on SteamOS or Arch

Install the Rust toolchain and native X11/Wayland development dependencies in a
development environment, then run:

```bash
cargo build --locked --release --manifest-path desktop/Cargo.toml
```

The binary is `desktop/target/release/opemos-recovery-status`. By default it
reads the guardian installed at:

```text
/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/bootstrap/recoveryctl.sh
```

For repository development only, point it at a reviewed checkout:

```bash
cargo run --locked --manifest-path desktop/Cargo.toml -- \
  --recoveryctl "$PWD/bootstrap/recoveryctl.sh"
```

`--smoke-test` renders the native window and closes it after a valid status is
received. The Linux VM test runs that mode under a virtual X server.

## Validate and produce a deployable executable

The repository's pinned Fedora x86_64 VM compiles, unit-tests, release-builds,
and launches the native window under Xvfb:

```bash
./tests/vm/run.sh --desktop-gui
```

The `SteamOS desktop companion` GitHub Actions workflow performs the equivalent
build in an Arch Linux container and uploads the stripped x86_64 executable as
`opemos-recovery-status-x86_64-arch-linux`. A successful virtual-display smoke
test is required before the artifact is uploaded. This workflow artifact is a
development build, not an authenticated release or an update channel; install
it manually only in a disposable test environment until the reviewed
transactional update contract is implemented.

The crash-safe generation manager, activation states, and launcher contract are
documented in the [SteamOS desktop companion guide](../docs/desktop-companion.md).
Production self-update remains disabled until the repository's dedicated signer
policy and immutable release channel are reviewed and configured.
