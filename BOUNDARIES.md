# OPEMOS ownership boundary

> **READ-ONLY GOVERNANCE CONTRACT**
>
> This file may change only when the project owner explicitly requests a
> boundary change. Feature work, refactoring, repinning, release work, and
> automated cleanup must not edit it. Summaries elsewhere are non-authoritative.

## Dependency direction

```text
OPEMOS Core contracts
├── CLI
├── SteamOS Desktop Companion
├── SteamOS DRM/KMS interstitial
└── OPEMOS.EXE
```

OPEMOS Core never imports, invokes, builds against, or requires OPEMOS.EXE.
Frontends do not import, link against, or execute one another.

## OPEMOS Core owns

- SteamOS/NVIDIA compatibility, release selection, and safe next actions.
- Exact Valve headers, NVIDIA source selection, exact-target builds, artifact
  validation, canonical bundle manifests, schemas, and publication contracts.
- Reviewed userspace locks, signer/keyring policy, payload profiles, package
  authentication, and dependency correctness.
- Mounted-target installation, internal rollback, modules, GRUB, `depmod`,
  initramfs, receipts, and structured post-install verification.
- Machine-readable progress/results and target-side CLI, Desktop Companion,
  DRM/KMS interstitial, recovery guardian, and device update contracts.
- Core contract, archive, Fedora build/transaction, target mutation, and
  target-client tests.

## OPEMOS.EXE owns

- macOS/Tauri windows, menus, accessibility, labels, progress weighting,
  diagnostics, controls, and application updates.
- Host HTTP acquisition, physical cache location, retries, authenticated
  manifest pinning, host-to-appliance transport, and transfer cleanup.
- Recovery-image inspection, boot-slot/kernel discovery, A/B and partition
  layout, QEMU/appliance lifecycle, mount orchestration, and exclusive overlay
  ownership.
- Outer rollback by retaining or discarding disposable overlays, preservation
  of the source image, independent final-image/output-manifest validation,
  export, Finder integration, and verified USB writing.
- The installation-media welcome application and its guarded target-disk
  selection bridge.
- macOS UI, download/transfer, VM, overlay, export, USB, and independent image
  tests.

## Sole UI exception

OPEMOS.EXE may carry and install authenticated, Core-owned SteamOS UI payloads
from the exact pinned Core bundle into a disposable target image. This permits
the generated image to contain the Desktop Companion and DRM/KMS interstitial
and permits consistent branding and progress semantics.

This is the only frontend-boundary exception. It is payload deployment, not a
source, build, or runtime dependency: OPEMOS.EXE must not import, link, modify,
or execute those SteamOS frontends as part of its macOS runtime. Core remains
their sole implementation, release, authentication, and device-runtime owner.
The builder-owned installation-media welcome application remains separate.

## Shared handoff

1. OPEMOS.EXE discovers the exact target from the recovery image.
2. The pinned Core resolver decides compatibility or authorizes its bounded
   exact-target build contract.
3. OPEMOS.EXE downloads and transfers only identities named by authenticated
   Core contracts.
4. Core validates and mutates the mounted disposable target transaction.
5. OPEMOS.EXE independently validates the resulting image and either exports
   it or discards the overlay.

Transport success never establishes trust. Core success never replaces the
builder's independent final-image check. Maintainers own real SteamOS/NVIDIA
hardware certification and cross-repository release approval.
