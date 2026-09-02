---
layout: page
title: Image-builder integration
description: Exact contract for resolving, validating, and installing into a disposable SteamOS image overlay.
---

## Contents

- [Responsibility boundary](#responsibility-boundary)
- [End-to-end flow](#end-to-end-flow)
- [Target discovery](#target-discovery)
- [Artifact resolution](#artifact-resolution)
- [Appliance handoff](#appliance-handoff)
- [Validation and mutation](#validation-and-mutation)
- [Progress UI](#progress-ui)
- [Final-image acceptance](#final-image-acceptance)

## Responsibility boundary

The image builder owns recovery-image layout and appliance lifecycle. This
repository owns artifact and installation correctness.

The builder must:

- inspect the recovery image and determine the actual boot slot and exact
  kernel when multiple kernels exist;
- preserve the source image and mutate only a disposable writable overlay;
- mount rootfs `/boot` normally and the matching EFI partition at `/efi`;
- transfer exclusive ownership of the overlay between native and x86_64
  appliances;
- consume structured resolver, progress, validation, and installation results;
- independently inspect the exported image before using an `-nvidia.img`
  suffix.

The support backend must:

- resolve or build for the exact target;
- authenticate modules, userspace, signatures, locks, and provenance;
- install and verify userspace, five modules, GRUB arguments, `depmod`, and
  initramfs contents;
- clean runtime mounts and temporary state on success, failure, and
  cancellation;
- fail closed on drift, ambiguity, missing trust inputs, or insufficient space.

## End-to-end flow

```text
read-only source image
        |
        v
native inspection appliance ---> exact SteamOS/kernel/architecture
        |
        v
schema-2 resolver -----------> published exact artifact or safe no-match
        |
        v
authenticated bundle + writable overlay
        |
        v
exclusive x86_64 Fedora appliance
        |
        +--> validate-only
        +--> mutate same validated snapshot
        +--> structured post-install verification
        |
        v
independent exported-image inspection ---> trusted output or discard overlay
```

## Target discovery

Never derive the target from macOS or Fedora. Read the mounted image:

- `VERSION_ID` from the target SteamOS release metadata;
- exact kernel directory used by the selected boot entry;
- target ELF architecture;
- A/B rootfs and EFI pairing;
- populated Holo database at `/usr/lib/holo/pacmandb`.

Ambiguous boot slots, multiple unexplained kernels, a missing package database,
or an EFI partition that is not distinct FAT storage are typed failures.

## Artifact resolution

The builder fetches GitHub release metadata and invokes:

```bash
python3 lib/resolve_target.py \
  --steamos TARGET_VERSION \
  --kernel EXACT_KERNEL \
  --architecture x86_64 \
  --releases /shared/releases.json
```

Only `status=compatible` may proceed. The returned archive, checksum, and
provenance assets are all mandatory. Trust remains
`pending-provenance-verification` until external and embedded provenance match
and all referenced hashes validate.

Same-series SteamOS fallback can select an older SteamOS release only when the
exact kernel still matches. Optional gaming-payload capability never uses that
fallback; it requires an exact supported target and complete profile assets.

## Appliance handoff

On Apple Silicon, native inspection can use an aarch64 appliance, but source
compilation and offline installation execute in the managed x86_64 Fedora
appliance. Before handoff:

1. unmount and stop the native appliance;
2. retain—but do not export or flatten—the writable overlay;
3. attach it exclusively to the x86_64 appliance;
4. prove no other appliance or host mount references the target filesystem;
5. mount rootfs, var, and EFI according to the inspected A/B layout.

The installer must never call `steamos-readonly` against Fedora or infer its
target from Fedora's running kernel.

## Validation and mutation

Use identical staged inputs for `--validate-only` and mutation. The installer
copies them into a private immutable snapshot and rejects changes during copy.
Hold one exclusive per-target lifecycle lock throughout both operations.

Direct mode uses the command shown in the
[developer guide](developer-guide.md#test-offline-installation). Authenticated
bundle mode replaces loose userspace inputs with an immutable imported
generation:

```bash
./bootstrap/install_to_root.sh \
  --input-source authenticated-bundle \
  --authenticated-bundle /media/certified-userspace.bundle \
  --bundle-store /appliance/cache/imported \
  --bundle-keyring /appliance/trust/nvidia-userspace.gpg \
  --bundle-reviewed-signers trust/nvidia-userspace-package-signers.json \
  --bundle-steamos 3.8.14 \
  --bundle-nvidia 575.64.05 \
  --root /target-root \
  --archive /appliance/modules.tar.gz \
  --checksum /appliance/modules.tar.gz.sha256 \
  --kernel EXACT_KERNEL \
  --result-json /appliance/results/install.json \
  --validate-only
```

Bundle and loose userspace modes are mutually exclusive. There is no network
fallback during installation.

If conservative logical storage admission fails, the caller may explicitly
request `--compression-profile btrfs-zstd3`. This is permitted only when exact
scratch-Btrfs measurement authorizes the same mutation policy. The builder must
not estimate a compression ratio, resize partitions, or delete package files.

## Progress UI

Read stderr line by line. Only records beginning with
`STEAMOS_NVIDIA_PROGRESS ` are machine-readable. Treat other output as bounded
diagnostic text.

- Byte-count phases may drive determinate progress.
- Package, module, and mount phases use real item counts.
- Pacman policy, GRUB, `depmod`, and initramfs can be indeterminate.
- Never fabricate a percentage for an opaque command.
- Correlate retries with `--progress-attempt`.
- Continue displaying cleanup after a failed or cancelled work phase.

Validate both streams before accepting the result:

```bash
python3 lib/validate_install_contract.py \
  --result /shared/install-result.json \
  --progress /shared/installer-stderr.log
```

## Final-image acceptance

Do not trust only an installer exit code. Require a schema-1 `success` result
with:

- exact `moduleVerification` for all five decompressed payloads;
- exact `userspaceVerification`, package database consistency, libraries,
  links, and GSP firmware;
- exact `initramfsVerification` for the selected kernel, including the explicit
  four-module early-boot set and rootfs-only `nvidia-peermem` classification;
- successful GRUB argument mutation;
- `mountsReleased: true` and `compressionPolicyRestored: true`;
- no stale runtime mounts or installer workspace;
- unchanged source recovery-image hash.

Then independently remount the exported image read-only and verify the same
module hashes, userspace versions, firmware, dependency metadata, boot files,
and initramfs inventory. Until Valve repair propagation, A/B update behavior,
and physical NVIDIA boot pass, classify the result as mutation-valid rather
than install-ready or certified.
