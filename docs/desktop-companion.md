---
layout: page
title: SteamOS desktop companion
description: Native status UI, authenticated generations, activation health, and rollback.
---

## Contents

- [Scope](#scope)
- [Build and test](#build-and-test)
- [Update generation contract](#update-generation-contract)
- [Activation and rollback](#activation-and-rollback)
- [Current trust gate](#current-trust-gate)

## Scope

`opemos-recovery-status` is a lightweight, read-only Rust interface for SteamOS
and Arch Linux Desktop Mode. It displays the bounded schema-1 result produced by
the installed boot guardian. It does not run as root, install drivers, or copy
recovery policy into the UI.

The native interface uses the same dark, Steam-blue, and NVIDIA-green visual
language as OPEMOS.EXE. Invalid, excessive, contradictory, or timed-out guardian
responses produce a fail-closed status rather than an inferred result.

## Build and test

Build on an x86_64 SteamOS/Arch development environment:

```bash
cargo build --locked --release --manifest-path desktop/Cargo.toml
```

The Fedora VM contract compiles the optimized Linux executable and launches it
under a virtual X server:

```bash
./tests/vm/run.sh --desktop-gui
```

GitHub Actions also builds in an Arch Linux container. Its uploaded executable
is a development artifact, not an authenticated release or automatic update.

## Update generation contract

`lib/desktop_update_generations.py` implements the local, crash-safe half of the
desktop update contract. A candidate requires:

- canonical schema-1 release metadata;
- an exact x86_64 ELF payload, size, and SHA-256;
- a detached signature from one active package-specific update signer;
- a reviewed keyring whose hash is pinned by the support-owned policy; and
- a 40-character support revision and canonical version/release identity.

The manager snapshots every input, verifies the signature over the exact private
snapshot, writes a private staging generation, fsyncs its files and directories,
and publishes it under the manifest SHA-256. It never overwrites an existing
generation.

```text
desktop_update_generations.py stage \
  --store STORE --manifest MANIFEST --signature MANIFEST.sig --binary BINARY
desktop_update_generations.py activate \
  --store STORE --generation SHA256 --timeout 90
desktop_update_generations.py acknowledge \
  --store STORE --generation SHA256
desktop_update_generations.py recover --store STORE
desktop_update_generations.py resolve --store STORE
```

Only one lifecycle operation may hold the store lock. The store, generation
directories, marker files, and immutable payload modes are revalidated on every
operation. Symlinks, changed payloads, unsafe permissions, unknown signers, and
wrong architectures fail before activation.

## Activation and rollback

Activation durably records the candidate and last-known-good generation before
atomically replacing the `current` marker. The new GUI acknowledges health only
after it has rendered a valid guardian result. If that acknowledgement is late,
missing, or the candidate changes, recovery restores the independently verified
previous generation. The order is intentionally safe at each power-loss point:

1. Crash before switching `current`: discard the uncommitted pending record.
2. Crash after switching: retain the pending deadline and roll back on expiry.
3. Crash after health is durable: finalize acknowledgement on the next run.
4. Crash during rollback: observe the restored pointer and remove stale pending
   state without reactivating the failed generation.

`bootstrap/launch_desktop_companion.sh` resolves and revalidates the active
generation immediately before launch. It exports an exact generation identity
so the rendered application can issue its bounded health acknowledgement.

## Current trust gate

`trust/desktop-update-signers.json` is intentionally `unconfigured`. Production
staging therefore fails closed until maintainers publish and review a dedicated
desktop-release signing key, pin its binary keyring hash, and add the first
active signer. Network acquisition, release publication, retention limits, and
installer delivery remain separate gates; callers must not replace the trust
policy through ordinary command-line input.
