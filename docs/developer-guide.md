---
layout: page
title: Developer guide
description: Build, test, audit, repack, and publish exact SteamOS NVIDIA artifacts.
---

## Contents

- [Set up and test](#set-up-and-test)
- [Build for an offline target](#build-for-an-offline-target)
- [Resolve an artifact](#resolve-an-artifact)
- [Audit and finalize userspace](#audit-and-finalize-userspace)
- [Test offline installation](#test-offline-installation)
- [Publish a release](#publish-a-release)
- [Publish a desktop companion update](#publish-a-desktop-companion-update)
- [Create a compressed-module revision](#create-a-compressed-module-revision)
- [Publish the documentation](#publish-the-documentation)
- [Test matrix](#test-matrix)

## Set up and test

Clone both repositories when developing SteamOS source changes. OPEMOS owns
orchestration and contracts; the source repository owns the
actual NVIDIA branches.

```bash
git clone https://github.com/CorniiDog/OPEMOS.git opemos
git clone https://github.com/CorniiDog/open-gpu-kernel-modules-steamos.git
cd opemos
./tests/check.sh
```

Do not develop by silently falling back to NVIDIA upstream. Project sources
must resolve to an explicit `nvidia/<version>` branch and exact commit.

## Build for an offline target

Run this inside an x86_64 Fedora appliance. The running Fedora kernel is not a
build input:

```bash
./bootstrap/build_for_target.sh \
  --steamos 3.8.14 \
  --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
  --nvidia 575.64.05 \
  --source-commit 40bd1b5d6d39ae4e4180b7a665df144b08854d14 \
  --install-dependencies \
  --output /shared/artifacts \
  --result-json /shared/build-result.json
```

Start with `--resolve-only` to inspect the derived headers URL and build plan.
For authenticated local headers, supply all three values:

```bash
./bootstrap/build_for_target.sh \
  --steamos 3.8.14 \
  --kernel 6.16.12-valve24.4-1-neptune-616-gfe145653a794 \
  --nvidia 575.64.05 \
  --headers-package /shared/linux-neptune-headers.pkg.tar.zst \
  --headers-signature /shared/linux-neptune-headers.pkg.tar.zst.sig \
  --header-keyring /appliance/trust/valve-package-signers.gpg \
  --header-signer FULL_HEXADECIMAL_FINGERPRINT \
  --output /shared/artifacts
```

Success produces the archive, checksum, build information, provenance, and a
schema-1 result. Compiler-major mismatch lowers trust unless
`--require-compiler-major-match` makes it fatal.
Production consumers obtain `--nvidia` and `--source-commit` from the
resolver's authenticated `nextAction.buildPlan`; they do not select a nearby
driver branch or follow a moving ref independently.

## Resolve an artifact

Fetch the releases API separately, then resolve from the mounted target—not
from the host or appliance:

```bash
python3 lib/resolve_target.py \
  --steamos 3.8.16 \
  --kernel 6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45 \
  --architecture x86_64 \
  --releases releases.json
```

The resolver never downloads or mutates. A compatible schema-2 response remains
`pending-provenance-verification` until the consumer validates the sidecars and
embedded provenance.

## Audit and finalize userspace

Maintainers resolve the complete missing dependency closure from one pinned
Arch Linux Archive snapshot:

```bash
python3 bootstrap/audit_userspace_closure.py \
  --root /evidence/target-root \
  --snapshot 2025/08/01 \
  --snapshot-url https://archive.archlinux.org/repos/2025/08/01/ \
  --full-keyring /evidence/archlinux-full.gpg \
  --keyring-source /evidence/archlinux-keyring.pkg.tar.zst \
  --keyring-source-signature /evidence/archlinux-keyring.pkg.tar.zst.sig \
  --nvidia-utils /evidence/nvidia-utils.pkg.tar.zst \
  --nvidia-utils-signature /evidence/nvidia-utils.pkg.tar.zst.sig \
  --lib32-nvidia-utils /evidence/lib32-nvidia-utils.pkg.tar.zst \
  --lib32-nvidia-utils-signature /evidence/lib32-nvidia-utils.pkg.tar.zst.sig \
  --steamos 3.8.14 --nvidia 575.64.05 \
  --stage /evidence/staged --output /evidence/candidate.json
```

The candidate is non-installable until every `missingReview` entry is reviewed.
Finalize it create-only with the minimal keyring:

```bash
python3 bootstrap/finalize_userspace_lock.py \
  --candidate /evidence/candidate.json \
  --minimal-keyring /evidence/reviewed-minimal.gpg \
  --reviewed-policy trust/nvidia-userspace-package-signers.json \
  --reviewed-at 2026-09-01 \
  --output /evidence/userspace-lock.json
```

## Test offline installation

Always validate before mutation. The root must expose rootfs `/boot` normally
and mount the matching EFI partition at `<root>/efi`:

```bash
./bootstrap/install_to_root.sh \
  --root /mnt/target \
  --archive /shared/modules.tar.gz \
  --checksum /shared/modules.tar.gz.sha256 \
  --provenance /shared/modules.provenance.json \
  --kernel EXACT_KERNEL \
  --nvidia-utils /shared/nvidia-utils.pkg.tar.zst \
  --nvidia-utils-signature /shared/nvidia-utils.pkg.tar.zst.sig \
  --lib32-nvidia-utils /shared/lib32-nvidia-utils.pkg.tar.zst \
  --lib32-nvidia-utils-signature /shared/lib32-nvidia-utils.pkg.tar.zst.sig \
  --package-keyring /shared/reviewed-userspace.gpg \
  --userspace-lock /shared/userspace-lock.json \
  --result-json /shared/install-result.json \
  --validate-only
```

Remove `--validate-only` only after the caller has accepted the exact structured
result and confirmed it is operating on a disposable overlay.

## Publish a release

Inspect the canonical plan first:

```bash
./bootstrap/publish_artifacts.sh \
  --archive /shared/nvidia-open-....tar.gz \
  --checksum /shared/nvidia-open-....tar.gz.sha256 \
  --build-info /shared/nvidia-open-....build-info.txt \
  --provenance /shared/nvidia-open-....provenance.json \
  --dry-run
```

Create a new release without clobbering an existing tag:

```bash
./bootstrap/publish_artifacts.sh \
  --archive /shared/nvidia-open-....tar.gz \
  --checksum /shared/nvidia-open-....tar.gz.sha256 \
  --build-info /shared/nvidia-open-....build-info.txt \
  --provenance /shared/nvidia-open-....provenance.json \
  --create-only
```

The publisher derives tag, title, notes, and four ordered assets from validated
metadata. It is fixed to the canonical repository unless an explicit
development-only repository override is supplied.

## Publish a desktop companion update

Desktop companion releases use a separate canonical schema-1 manifest,
detached signature, hash-pinned public keyring, and reviewed signer policy. See
[SteamOS desktop companion](desktop-companion.md#release-signing-and-publication)
for the complete public-key onboarding, manifest creation, offline signing,
dry-run, and create-only publication workflow. The repository never generates
or stores the signing private key.

## Create a compressed-module revision

```bash
./bootstrap/repack_artifacts.sh \
  --archive /shared/nvidia-open-....tar.gz \
  --checksum /shared/nvidia-open-....tar.gz.sha256 \
  --build-info /shared/nvidia-open-....build-info.txt \
  --provenance /shared/nvidia-open-....provenance.json \
  --output-dir /shared/repacked \
  --support-commit "$(git rev-parse HEAD)" \
  --revision 1 \
  --dry-run
```

Remove `--dry-run` to create local assets. Publishing uses a new revision tag
and create-only semantics; the original release is never overwritten.

## Publish the documentation

Documentation lives under `docs/` and is built by
`.github/workflows/pages.yml`. In the GitHub repository settings, select
**Pages → Build and deployment → GitHub Actions** once. After that, pushes to
`main` that change the README, documentation, or Pages workflow build and
deploy the site automatically. Pull requests build the site without deploying.

Run the local documentation contract before committing:

```bash
python3 tests/documentation.py
```

It validates required pages, front matter, navigation, internal links and
anchors, documented command paths, the concise README size limit, and the Pages
workflow's required actions and permissions.

## Test matrix

| Command | Coverage |
| --- | --- |
| `./tests/check.sh` | Syntax, contracts, confinement, deterministic fixtures, fake-root transactions |
| `./tests/non_sudo.sh` | User workflow with a failing `sudo` shim |
| `./tests/vm/run.sh` | Fedora x86_64, Bash 4+, locks, mounts, Btrfs, chroot, A/B fixtures |
| `./tests/vm/run-arch.sh` | Real Arch pacman/mkinitcpio boundary |
| `./tests/vm/run-offline-cache-matrix.sh` | Concurrent authenticated-cache behavior with pinned cached guests |

Hardware boot, Valve update propagation, Secure Boot, and real recovery media
are separate gates; fixture success does not certify them.
