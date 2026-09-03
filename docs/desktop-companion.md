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
- [Release signing and publication](#release-signing-and-publication)
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
generation. Each generation includes canonical trust metadata binding the
manifest and signature hashes, signer fingerprint, reviewed keyring hash, and
policy hash; all bindings are recomputed whenever the generation is used.

```text
desktop_update_generations.py stage \
  --store STORE --manifest MANIFEST --signature MANIFEST.sig --binary BINARY
desktop_update_generations.py activate \
  --store STORE --generation SHA256 --timeout 90
desktop_update_generations.py acknowledge \
  --store STORE --generation SHA256
desktop_update_generations.py recover --store STORE
desktop_update_generations.py resolve --store STORE
desktop_update_generations.py launch --store STORE
```

Only one lifecycle operation may hold the store lock. The store, generation
directories, marker files, and immutable payload modes are revalidated on every
operation. Symlinks, changed payloads, unsafe permissions, unknown signers, and
wrong architectures fail before activation.

Schema-1 failures use stable reasons including
`desktop_update_authentication_failed`, `desktop_update_busy`,
`desktop_update_state_conflict`, `desktop_update_version_not_newer`,
`desktop_update_health_timeout`, and `desktop_update_launch_failed`. Human
messages remain bounded and do not contain signature material or file contents.

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

`bootstrap/launch_desktop_companion.sh` delegates launch to the generation
manager. On Linux, the manager opens the exact authenticated ELF with
`O_NOFOLLOW`, rechecks its size, mode, ELF identity, and hash through that file
descriptor, copies and rehashes it into a write-sealed `memfd`, and executes the
sealed `/proc/self/fd` identity rather than reopening a mutable pathname. It
exports the exact generation identity so the rendered application can issue its
bounded health acknowledgement.

## Release signing and publication

Desktop updates use a dedicated release-signing key. Do not reuse an Arch,
Valve, NVIDIA, personal commit-signing, or package-signing key. Generate and
retain the private key on a separately controlled maintainer system; this
repository accepts only its exported public keyring. Back up and protect the
private key independently, and verify its full 40-hex-character fingerprint
over a separate trusted channel before reviewing it here.

Export the public key as a binary keyring, then create a candidate policy. The
policy command proves that the exact fingerprint is present in the snapshotted
keyring and binds the policy to its SHA-256. Its output is create-only:

```bash
gpg --batch --export FULL_FINGERPRINT > opemos-desktop-updates.gpg
python3 lib/desktop_update_release.py trust-policy \
  --keyring opemos-desktop-updates.gpg \
  --signer FULL_FINGERPRINT \
  --output desktop-update-signers.candidate.json
```

Review the public-key identity and candidate out of band. Only after that
review, install the binary keyring at
`trust/keyrings/opemos-desktop-updates.gpg` and replace the deliberately
unconfigured `trust/desktop-update-signers.json` in a dedicated reviewed
commit. Never commit a secret key, passphrase, private GnuPG home, or exported
secret-key packet.

Build the Linux x86_64 companion from the exact support revision being
published. Give the executable its canonical name and create the manifest:

```bash
REVISION="$(git rev-parse HEAD)"
VERSION=1.0.0
cp desktop/target/release/opemos-recovery-status opemos-recovery-status
python3 lib/desktop_update_release.py manifest \
  --binary opemos-recovery-status \
  --version "$VERSION" \
  --support-revision "$REVISION" \
  --output "opemos-desktop-v${VERSION}.manifest.json"
```

Sign the exact canonical manifest on the controlled signing system. The
publisher never asks for, imports, or stores the private key:

```bash
gpg --batch --local-user FULL_FINGERPRINT --detach-sign \
  --output "opemos-desktop-v${VERSION}.manifest.json.sig" \
  "opemos-desktop-v${VERSION}.manifest.json"
```

Validate every identity and trust binding without contacting or mutating
GitHub:

```bash
./bootstrap/publish_desktop_update.sh \
  --binary opemos-recovery-status \
  --manifest "opemos-desktop-v${VERSION}.manifest.json" \
  --signature "opemos-desktop-v${VERSION}.manifest.json.sig" \
  --version "$VERSION" \
  --dry-run
```

After reviewing the canonical JSON plan, publish create-only by replacing
`--dry-run` with `--create-only`. The command privately snapshots all five
payload/trust inputs before validation, refuses an existing release, and uploads
the executable, manifest, and detached signature in a deterministic order. It
uses only the committed policy/keyring in production and is fixed to
`CorniiDog/OPEMOS`. A noncanonical repository or alternate trust input requires
`OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE=1`; repository changes additionally require
`--development-repository OWNER/REPO`.

## Current trust gate

`trust/desktop-update-signers.json` is intentionally `unconfigured`. Production
staging therefore fails closed until maintainers publish and review a dedicated
desktop-release public key, pin its binary keyring hash, and add the first active
signer. Canonical manifest generation and create-only publication are
implemented, but this repository never generates or holds the private key.
Network acquisition, retention limits, installer delivery, and real SteamOS
testing remain separate gates; callers must not replace the trust policy through
ordinary runtime command-line input.
