---
layout: page
title: No-input boot interstitial
description: Direct DRM/KMS recovery progress before SteamOS starts Gaming or Desktop Mode.
---

## Contents

- [Purpose](#purpose)
- [Display architecture](#display-architecture)
- [Progress contract](#progress-contract)
- [Build and test](#build-and-test)
- [Installation contract](#installation-contract)
- [Failure behavior](#failure-behavior)
- [Remaining hardware gate](#remaining-hardware-gate)

## Purpose

`opemos-interstitial` is a dedicated, no-input graphical session displayed
while the boot guardian checks exact-kernel NVIDIA support. It is not KDE,
Gamescope, a window manager, or a replacement desktop. The service completes,
releases the display, and allows SteamOS to continue to the Gaming or Desktop
session it had already selected.

This component is separate from the windowed
[SteamOS desktop companion](desktop-companion.md). The companion runs after a
desktop exists; the interstitial is designed specifically not to require one.

## Display architecture

The Linux executable opens a DRM primary node, finds a connected bounded mode,
creates an XRGB8888 dumb buffer, draws the OPEMOS interface entirely in
software, and applies a legacy KMS modeset. It does not initialize OpenGL,
Vulkan, CUDA, X11, Wayland, Gamescope, or any input device.

The renderer scans only `/dev/dri/card0` through `card15`, caps a mode at
8192×8192 and 33,554,432 pixels, accounts for the device pitch, and restores
the prior CRTC/framebuffer state before destroying its own framebuffer. SIGINT
and SIGTERM are converted into a normal cleanup path. A 300-second application
watchdog and a 315-second service ceiling keep a broken display path from
holding graphical boot indefinitely.

The systemd service is ordered before `display-manager.service` and
`graphical.target`. The guardian starts after the interstitial process has
launched and writes its status through a root-owned runtime document. Renderer
failure is deliberately fail-open for presentation: it is recorded in the
journal, DRM is released, and the authoritative guardian still chooses normal
graphics or its console-safe fallback.

## Progress contract

The renderer accepts only a bounded schema-1 document at:

```text
/run/opemos/interstitial/progress.json
```

The canonical fields are `schemaVersion`, `sequence`, `status`, `phase`,
`completed`, `total`, `stepCompleted`, and `stepTotal`. The first counter pair
drives the blue, operation-wide bar. The optional second pair drives the green
current-step bar; an absent pair is rendered as bounded indeterminate motion.
Step progress may reset only when the enumerated phase changes. Schema-1
consumers accept documents produced before the optional step pair was added,
while current producers always emit both step fields. There are no
caller-controlled labels, paths, shell
commands, URLs, or error messages. Phases are enumerated in the Rust model and
the Python writer; `interstitial/progress-schema-v1.json` is the versioned
consumer fixture. Sequence numbers must increase, terminal states are
immutable, and determinate counters must be paired and internally consistent.

The file is opened with `O_NOFOLLOW`, bounded to 64 KiB, and in production must
be root-owned and not group- or world-writable. `interstitial_progress.py`
serializes concurrent writers with a private lock and publishes each update by
fsync followed by atomic replacement.

## Build and test

Build the SteamOS/Arch x86_64 binary:

```bash
cargo build --locked --release --manifest-path interstitial/Cargo.toml
```

Portable model, rasterizer, and contract tests run on macOS:

```bash
cargo test --locked --manifest-path interstitial/Cargo.toml
python3 tests/interstitial.py
./test_update_macos.sh
```

The last command starts a loopback-only, time-bounded browser simulation. It
does not install anything and does not claim macOS runtime compatibility. Use
`./test_update_macos.sh --no-open --duration 5` for its automated health and
content check.

The Fedora x86_64 appliance compiles the Linux DRM implementation and attempts
the real KMS path when QEMU exposes a connected scanout:

```bash
./tests/vm/run.sh --no-image-download --interstitial
```

If headless QEMU exposes a DRM node without a connected connector, the VM
requires the bounded fail-open result and proves that no renderer remains.

## Installation contract

The recovery guardian installer accepts an optional authenticated executable:

```bash
./bootstrap/install_recovery_guardian_to_root.sh \
  --root /mounted/root \
  --support-revision FULL_40_CHARACTER_COMMIT \
  --nvidia 575.64.05 \
  --interstitial-binary /staging/opemos-interstitial \
  --interstitial-sha256 EXACT_SHA256
```

The binary and hash must be supplied together. The installer snapshots the
input into a private directory, validates the exact hash and x86_64 ELF
identity, and installs it mode 0755. Without the optional binary, the service
unit remains conditionally inactive and the existing guardian behavior is
unchanged.

The checksum is an installation binding, not an independent trust anchor. A
normal-user release remains disabled until the binary, checksum, support
revision, and release identity are bound by the reviewed desktop/update signing
policy. CI output is a development artifact.

## Failure behavior

- Missing DRM, no connected mode, invalid pitch, or a modeset error releases
  resources and lets boot continue without the cosmetic interface.
- Missing, malformed, excessive, writable, regressed, or contradictory progress
  stops the renderer and leaves the guardian authoritative.
- A failed guardian state is shown as `RECOVERY NEEDS ATTENTION`; the guardian
  selects its console-safe fallback independently.
- The renderer never enables a driver, changes a systemd default target, runs a
  repair command, or accepts user input.

## Remaining hardware gate

The portable and Fedora contracts do not replace physical validation. Before
enabling the service in a normal-user release, test simpledrm and intended iGPU
paths on real SteamOS hardware, display hotplug, internal/external displays,
suspend/resume boundaries, abrupt power loss, renderer SIGKILL, and verified
handoff into both Gaming and Desktop Mode.
