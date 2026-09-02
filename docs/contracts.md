---
layout: page
title: Commands and contracts
description: API-style command, result, progress, trust, and failure reference.
---

## Contents

- [Entry points](#entry-points)
- [Resolver contract](#resolver-contract)
- [Build result](#build-result)
- [Installer contract](#installer-contract)
- [Progress records](#progress-records)
- [Verification records](#verification-records)
- [Trust values](#trust-values)
- [Failure handling](#failure-handling)
- [Compatibility rules](#compatibility-rules)

## Entry points

| Command | Audience | Mutates | Machine output |
| --- | --- | --- | --- |
| `bootstrap/setup_nvidia.sh` | Steam Deck user/developer | Unless `--resolve-only` | Human selection output |
| `bootstrap/online_install.sh` | Steam Deck user | Yes, except help | Installer orchestration |
| `lib/resolve_target.py` | Image builder | No | Resolver schema 2 JSON |
| `bootstrap/build_for_target.sh` | x86_64 build appliance | Build workspace/output only | Build schema 1 with `--result-json` |
| `bootstrap/install_to_root.sh` | x86_64 image appliance | Mounted target unless `--validate-only` | Install schema 1 plus progress |
| `bootstrap/audit_userspace_closure.py` | Maintainer | Stage/output only | Candidate-lock schema 1 |
| `bootstrap/finalize_userspace_lock.py` | Maintainer | Create-only output | Reviewed lock |
| `bootstrap/publish_artifacts.sh` | Maintainer | GitHub release unless `--dry-run` | Publication plan JSON |
| `bootstrap/repack_artifacts.sh` | Maintainer | Output/release unless `--dry-run` | Repack plan/result JSON |
| `lib/validate_install_contract.py` | Consumer | No | Contract-validation result |

Every command supports `--help`; that output is the canonical option list.

## Resolver contract

`lib/resolve_target.py` consumes local GitHub release JSON and returns schema 2.
Stable statuses are:

| Status | Meaning |
| --- | --- |
| `compatible` | One policy-compliant exact-kernel release was selected |
| `no_compatible_artifact` | Releases were valid, but none matched policy |
| `unsupported_target` | Target identity is well-formed but unsupported |
| `invalid_target` | Input identity or releases document is malformed |

A compatible record names the publication, archive, checksum sidecar, and
provenance sidecar. Consumers must reject missing or duplicate required assets.
The resolver performs no download and does not authenticate content by URL.

## Build result

Pass `--result-json FILE` to `build_for_target.sh`. Schema 1 has a stable
envelope:

```json
{
  "schemaVersion": 1,
  "status": "success",
  "reason": "build_succeeded",
  "target": {
    "steamosVersion": "3.8.14",
    "kernelVersion": "6.16.12-valve24.4-1-neptune-616-g...",
    "architecture": "x86_64",
    "nvidiaVersion": "575.64.05"
  },
  "trust": "locally-built-verified",
  "artifacts": {}
}
```

Artifact values are filenames and hashes, not private host paths. Cancellation
is a terminal structured result and kills/reaps child process groups before
temporary-state removal.

## Installer contract

Required direct-mode inputs:

```text
root, archive, checksum, provenance, exact kernel,
nvidia-utils package + signature,
lib32-nvidia-utils package + signature,
reviewed binary keyring, reviewed userspace lock, result JSON
```

Dependencies are supplied as positionally paired repeatable
`--dependency-package` and `--dependency-signature` values. The complete set
must equal the reviewed lock: no missing, unexpected, duplicate, or mismatched
record is allowed.

Terminal statuses are `validated`, `success`, `failed`, and `cancelled`.
`validated` means mutation did not begin. `success` is impossible unless all
post-install verification and cleanup records are present and valid.

Lock mismatches return sorted, bounded fields:

```json
{
  "reason": "userspace_lock_mismatch",
  "missingPackages": [],
  "unexpectedPackages": [],
  "duplicatePackages": [],
  "packageMismatches": [
    {
      "packageName": "egl-wayland",
      "invalidFields": ["filename", "signatureFilename"],
      "expected": {},
      "actual": {}
    }
  ]
}
```

Field order is fixed: filename, signature filename, version, architecture,
package hash, signature hash, signer, installed size, dependencies, provides.

## Progress records

Progress is emitted to stderr as one bounded JSON object per line:

```text
STEAMOS_NVIDIA_PROGRESS {"attempt":1,"completed":3,"indeterminate":false,"phase":"module_install","schemaVersion":1,"total":5,"unit":"items"}
```

Indeterminate form:

```text
STEAMOS_NVIDIA_PROGRESS {"attempt":1,"indeterminate":true,"phase":"initramfs","schemaVersion":1}
```

Rules:

- `attempt` is an integer from 0 through 1,000,000.
- Phase names are fixed internal tokens.
- Determinate counts never decrease or change total/unit within an attempt.
- Records never contain paths, credentials, arbitrary messages, or subprocess
  output.
- Stderr lines without the prefix are not part of this API.

Important phases include hashing, Holo database, archive layout, dependency
closure, storage calculation, compression measurement, pacman policy, runtime
mounts, userspace install/verification, module install/verification, GRUB,
`depmod`, initramfs, installation state, and mount cleanup.

## Verification records

Successful installation requires these nested records:

| Field | Required proof |
| --- | --- |
| `moduleVerification` | Five names, representation, decompressed payload hash, mode 0644, UID/GID 0, architecture, NVIDIA version, exact vermagic |
| `userspaceVerification` | Every locked package/version/hash, owned payload, links, libraries, GSP firmware, Holo records and database consistency |
| `initramfsWorkspace` | Private mounted workspace, capacity, inode policy, mode 1777, and cleanup |
| `initramfsVerification` | Exact generated images/configuration and the explicit early-boot set: `nvidia`, `nvidia_modeset`, `nvidia_uvm`, and `nvidia_drm` |
| `cleanup` | Runtime mount count/release, compression restoration, no trusted partial state |

Schema 1 permits bounded additive fields for forward compatibility. Consumers
must still require every mandatory success field and reject duplicate keys,
oversized records, invalid types, or contradictory status/reason pairs.
The five-module filesystem contract remains separate: `nvidia-peermem` must be
installed and verified on the root filesystem, but is intentionally recorded as
rootfs-only and rejected from the initramfs until an audited target requires it.

## Trust values

| Value | Meaning |
| --- | --- |
| `pending-provenance-verification` | Resolution only; content has not yet been authenticated |
| `development-unverified` | Useful development output with an unresolved policy issue |
| `locally-built-verified` | Exact inputs and payload verified, without hardware certification |
| `certified-published` | Reserved for an artifact with the required hardware certification attestation |

Trust is preserved from authenticated provenance; consumers never promote it.

## Failure handling

Branch on `status`, `reason`, and `phase`, not human-readable `message`. Common
stable reasons include invalid target/arguments, missing exact headers,
signature or provenance failure, userspace lock mismatch, dependency failure,
target-space failure, compression measurement/policy failure, mount identity
drift, target execution trust, module/userspace/initramfs verification failure,
cleanup failure, and cancellation.

Bounded nested diagnostics such as `measurementFailure`, `packageMismatches`,
`moduleMismatches`, `targetExecutionFailure`, and workspace capacity records
are safe to show in a UI. Do not expose raw commands, host paths, credentials,
signature bytes, or unlimited stderr.

## Compatibility rules

- Exact kernel release and vermagic are mandatory.
- NVIDIA module version, userspace, and GSP firmware must agree.
- CPU architecture must be x86_64 for the current appliance contract.
- Same-series SteamOS fallback may not change the exact kernel.
- Optional gaming payloads require exact target support and do not inherit
  fallback eligibility.
- A normal missing artifact is a safe no-match, never permission to approximate.
