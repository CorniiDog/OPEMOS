---
layout: page
title: Trust and safety
description: Authentication boundaries, fail-closed policies, storage admission, rollback, and remaining certification gates.
---

## Contents

- [Trust boundary](#trust-boundary)
- [Public online installer](#public-online-installer)
- [Artifact authentication](#artifact-authentication)
- [Userspace trust](#userspace-trust)
- [Target-owned execution](#target-owned-execution)
- [Archive and path confinement](#archive-and-path-confinement)
- [Storage admission](#storage-admission)
- [Mutation and rollback](#mutation-and-rollback)
- [Installed-system update recovery](#installed-system-update-recovery)
- [Optional CUDA omission](#optional-cuda-omission)
- [Remaining certification gates](#remaining-certification-gates)

## Trust boundary

Exact matching is necessary but is not authentication. A trusted install binds:

- target SteamOS, exact kernel, architecture, and NVIDIA version;
- archive, checksum, external and embedded provenance;
- five logical module payload hashes and vermagic;
- signed userspace package closure and package-specific signer policy;
- reviewed minimal keyring and userspace lock;
- support and source commits plus build toolchain identity;
- verified target mutation and final-image contents.

Hashes downloaded from the same mutable location as their payload detect
corruption but are not independent proof of publisher identity.

## Public online installer

The convenience command currently bootstraps from mutable GitHub `main`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/.../main/bootstrap/online_install.sh)
```

The script pins subsequent work for run consistency, but the initially
downloaded bytes are not authenticated by an immutable signed bootstrap. This
is a known production trust gap. Prefer a reviewed local checkout until a
release/tag/commit bootstrap and independently authenticated manifest are
published.

## Artifact authentication

The canonical publisher validates all four release assets before GitHub
mutation: archive, checksum, build info, and provenance. Canonical names,
target identity, release tag, clean commits, embedded provenance, five-module
inventory, module payload hashes, version, architecture, and vermagic must
agree.

Published trust remains whatever authenticated provenance declares. A local
verified build is not silently promoted to certified merely because it is on a
GitHub release.

Valve headers require a reviewed signer fingerprint and pinned keyring.
`prepare_valve_keyring.py` authenticates the reviewed `holo-keyring` source,
extracts only its expected keyring, verifies its hash, and creates the binary
format required by `gpgv`.

## Userspace trust

The installer accepts one reviewed lock and its exact complete package set.
Every package has pinned filename, version, architecture, package/signature
hash, installed size, dependency/provides metadata, and package-specific signer
fingerprint. A signer approved for one package cannot sign another package
unless policy explicitly approves that mapping.

Closure audits use one dated Arch Linux Archive snapshot. They may collect all
cryptographically valid but unreviewed signers in one candidate, but invalid
signatures or keys absent from the authenticated full keyring terminate the
audit. Candidates cannot be installed until finalized against reviewed policy
and a minimal keyring.

## Target-owned execution

Pacman hooks and target `mkinitcpio` executables are code from the mounted
target. Before mutation, the installer snapshots and validates their confined
paths, ownership, permissions, executors, and configuration. It rechecks them
before pacman and again after the authenticated package transaction before
initramfs generation.

SteamOS's root-owned relative `/bin -> usr/bin` alias is accepted because every
component resolves inside the target. Absolute escapes, untrusted ownership,
writable ancestors, local hook overrides, missing executors, or later drift
fail closed.

## Archive and path confinement

Archive consumers enforce compressed, expanded, member-count, per-member, and
metadata bounds. They reject absolute/traversal paths, duplicate entries,
special files, escaping symlink or hardlink targets, missing hardlink targets,
and changed-during-snapshot inputs.

Every installer input is copied to a private immutable staging directory.
Validation and mutation use only those copies. Target destinations are checked
for symlink escape before every destructive phase.

## Storage admission

Default admission uses conservative logical installed sizes, module growth,
replacement credit, initramfs growth, and explicit reserves. Package archive
compression is informational and grants no admission credit.

`--compression-profile btrfs-zstd3` is a separate measured policy. It writes
the exact authenticated payload into disposable scratch Btrfs with the same
`compress-force=zstd:3` policy, measures allocated bytes, adds filesystem and
initramfs reserves, and authorizes mutation only if the exact measured result
fits. The live target must independently prove that the policy applies to every
new destination. No fixed compression ratio is used.

Pacman's logical `CheckSpace` is suppressed only when the unchanged validation
document explicitly authorizes this measured path and the live mount still
matches. Signature, lock, offline, database, and post-install checks remain
unchanged.

## Mutation and rollback

OPEMOS.EXE mutates only a disposable overlay. The installer holds an
exclusive per-target lifecycle lock and repeatedly verifies rootfs and EFI
identity. `/dev`, `/proc`, `/sys`, and a private appliance-backed `/var/tmp`
workspace remain mounted only for the controlled transaction and initramfs
phases.

Cleanup unmounts exact recorded targets in reverse order, restores Btrfs policy,
removes temporary state, and reports each invariant separately. A cleanup
failure supersedes the original top-level reason while preserving bounded
nested diagnostics. The caller must discard every failed or cancelled overlay.

## Installed-system update recovery

The canonical one-line installer installs a persistent, support-owned boot
guardian. Its bounded schema-1 status document reports the active kernel,
expected NVIDIA userspace, all five module paths, vermagic and module versions,
active fallback profile, and supported next actions. It is intentionally
UI-neutral: a terminal, OPEMOS.EXE, or a themed on-device recovery application
must consume the same status and action contract instead of reimplementing
kernel compatibility decisions.

The guardian runs before the display manager. A newly active A/B slot that
lacks an exact module set is classified `recovery-required` and enters the
`console` profile. That profile blacklists NVIDIA and Nouveau together and
removes the forced NVIDIA initramfs fragment before rebuilding initramfs. It
therefore cannot race two DRM drivers for one GPU. `igpu-desktop` additionally
requires a boot-VGA Intel or AMD device. `nouveau-experimental` is never
automatic and requires explicit authorization; it preserves and disables the
normal NVIDIA configuration before regeneration.

The executable snapshot is root-owned on the shared home filesystem rather
than inside the replaceable root slot. Its systemd entry point and NVIDIA
configuration paths are registered through SteamOS's supported
`/etc/atomic-update.conf.d` migration list. This is the persistence boundary;
putting the service only in `/usr` would lose it when an update replaces the
inactive rootfs.

Guardian installation validates every existing destination and ancestor before
root mutation. Symlink ancestors, non-regular destinations, writable or
unexpected-owner paths, and systemd enablement links with unexpected targets
are rejected. Offline staging requires its caller to provide the exact support
commit explicitly; it never derives a trusted identity from an arbitrary local
checkout or rewrites a detected device name into executable shell code.

At boot, conflicting NVIDIA identity markers across persistent and active-slot
locations are rejected rather than resolved by precedence. A malformed,
missing, or pinned-policy-mismatched identity makes inspection fail closed, and
the guardian enables the mutually exclusive console fallback even when the
status helper exits nonzero. Identity records and fallback state are read from
confined descriptors; symlinks, hardlinks, unsafe modes or owners, excessive
files, and replacement during a read are rejected.

`repair-online` is bound to the exact support commit installed by the original
transaction. It uses the normal published-release resolver and still requires
the exact running kernel and matching userspace. If no authenticated exact
artifact exists, it fails closed and leaves fallback active. Successful
download or compilation alone never disables fallback: five-module verification
must pass first. A/B rollback remains a coordination operation because disk,
slot, EFI, and target identity must be established by the caller; the support
CLI never guesses among multiple SteamOS layouts.

Internet access is never a boot dependency. A delayed repair transaction is
stored atomically with the shared root-owned guardian snapshot and binds the
kernel, NVIDIA version, and support commit. NetworkManager connectivity changes
may wake it, while a bounded timer supplies a fallback retry. DNS, TLS, captive
portal, and network failures leave console recovery active. Cancelling disables
automatic retries without changing either slot. An authenticated offline cache
may be used only after its exact target and hashes are revalidated; dynamic or
nearest-version cache substitution remains forbidden.

Recovery, cancellation, guardian fallback, and manual fallback changes are
serialized by a recovery-operation lock. The transaction record rejects
duplicate or noncanonical JSON, unsafe ownership/mode/link state, invalid phase
transitions, concurrent writers, and non-durable replacement. The separate
installer lifecycle lock continues to serialize root mutation; recovery does
not hold that lock while invoking the nested canonical installer.

Generation health and rollback additionally bind the rootfs receipt's exact
userspace-lock filename and SHA-256 to the selected generation target record.
Target equality alone is not treated as proof that the installed package set
came from that generation.

## Optional CUDA omission

`gaming-no-cuda-v1` is a support-owned, exact-target package profile. It omits
only reviewed optional CUDA compute components using deterministic package
repacking. It preserves graphics, Vulkan, GLVND/EGL/OpenGL, NVENC/NVDEC, GSP
firmware, NGX/DLSS, recovery rendering, required 32-bit gaming libraries,
package ownership, dependencies, and provenance.

OPEMOS.EXE never deletes guessed filenames. Unsupported targets keep the
option disabled. Reinstalling the complete authenticated packages restores the
normal payload safely. “Omit optional CUDA” does not mean the ordinary complete
NVIDIA driver lacks CUDA compatibility.

## Remaining certification gates

The following cannot be inferred from fixture or VM success:

- fresh-stock SteamOS recovery installation;
- Valve `repair_device.sh` propagation and A/B update behavior;
- physical NVIDIA GPU boot, rendering, suspend/resume, and hardware coverage;
- Valve Secure Boot or a final module-signing policy;
- immutable authenticated public bootstrap;
- hardware certification attestation bound to exact artifact hashes;
- independent archival recovery when GitHub, Valve, or Arch endpoints are
  unavailable.

Until those gates pass, report the narrow verified status—such as
`nvidia-mutation-valid` or `locally-built-verified`—rather than a broader claim.
