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
| `bootstrap/publish_installer_bundle.sh` | Maintainer | Create-only immutable Core-bundle release unless `--dry-run` | Publication plan JSON |
| `bootstrap/publish_desktop_update.sh` | Maintainer | Create-only GitHub release unless `--dry-run` | Desktop publication schema 1 JSON |
| `bootstrap/repack_artifacts.sh` | Maintainer | Output/release unless `--dry-run` | Repack plan/result JSON |
| `lib/validate_install_contract.py` | Consumer | No | Contract-validation result |
| `lib/installer_bundle_manifest.py` | Maintainer/integrator | Create-only output | Immutable support-bundle manifest |
| `lib/desktop_update_generations.py` | SteamOS desktop launcher | Generation store and atomic markers | Desktop-update schema 1 JSON |

Every command supports `--help`; that output is the canonical option list.

The desktop publication plan binds the canonical repository, release tag,
support commit, title, notes, ordered asset names/hashes/sizes, signer
fingerprint, reviewed keyring hash, and policy hash. A dry run performs no
GitHub command. Live publication requires `--create-only` and refuses an
existing tag; it never edits or clobbers a release.

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
Its additive machine-readable definition is
[`resolver-result-v2.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/resolver-result-v2.schema.json).
Compatibility and asset selection remain support-owned policy; graphical
consumers validate this result but must not independently select a release.

When no valid release matches, reason `no_compatible_release` includes a
bounded `nextAction` authorizing the managed x86_64 exact-target build entry
point. Its additive `buildPlan` is bound to
`policies/exact-target-builds-v1.json` and pins the exact NVIDIA version,
source repository/ref/commit, and authenticated known-good baseline identity.
The consumer must verify the policy file through the immutable Core bundle and
pass the source commit to `build_for_target.sh --source-commit`. A target with
no reviewed plan returns `no_reviewed_exact_target_build_plan` without an
action. Missing, duplicate, or otherwise invalid release assets also receive no
fallback because they represent publication-integrity failures.

The canonical cross-frontend corpus is
[`resolver-compatibility-v2.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/fixtures/resolver-compatibility-v2.json).
It covers malformed targets, malformed and duplicate release metadata,
incomplete and duplicate canonical assets, an unreviewed target, and the exact
reviewed build authorization. Core executes all cases during its local suite;
consumers use the same bundle-authenticated bytes for parity tests.

## Installer consumer bundle

`lib/installer_bundle_manifest.py` replaces consumer-maintained copies of the
support file inventory. Given an exact 40-character support commit, it reads
all required blobs and executable bits directly from Git, then emits
canonical JSON containing their paths, roles, modes, sizes, hashes, and a
deterministic bundle ID. It never derives trusted bytes from the mutable
working tree.

Consumers pin the support commit and the resulting manifest SHA-256. They must
validate the manifest, download exactly its confined paths from that commit,
and verify every mode, size, and hash before execution. The manifest generator
uses create-only output; see the
[`contracts/README.md`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/README.md) for
commands and the self-reference rationale.

`bootstrap/publish_installer_bundle.sh` publishes the generated document as a
single asset in a commit-specific, create-only release. It never modifies an
NVIDIA artifact release. Publication supplies availability, not independent
authentication: production consumers must pin the manifest digest separately.

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
The additive machine-readable envelope is
[`installer-result-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-result-v1.schema.json).
The schema defines record shape and mandatory success proofs;
`lib/validate_install_contract.py` additionally enforces cross-record target,
package, and module-payload equality.

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

The additive single-record definition is
[`installer-progress-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-progress-v1.schema.json).
The schema describes record shape; `lib/validate_install_contract.py` remains
authoritative for stream-wide attempt ordering and monotonicity.

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
- `hashing` is one aggregate byte sequence per attempt. Its fixed total is the
  sum of every immutable archive, checksum, provenance, package, detached
  signature, keyring, userspace-lock, and optional gaming-profile input; it
  never restarts for an individual file.
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
| `moduleVerification` | Five names, representation, decompressed payload hash, mode 0644, UID/GID 0, and equality with the validated artifact hashes that bind architecture, NVIDIA version, and exact vermagic |
| `userspaceVerification` | Every locked package/version/hash, owned payload, links, libraries, GSP firmware, Holo records and database consistency |
| `initramfsWorkspace` | Private mounted workspace, capacity, inode policy, mode 1777, and cleanup |
| `initramfsVerification` | Exact generated images/configuration and the explicit early-boot set: `nvidia`, `nvidia_modeset`, `nvidia_uvm`, and `nvidia_drm` |
| `payloadReceipt` | Rootfs-resident, hash-bound validation/module/userspace/initramfs evidence committed only after payload verification |
| `cleanup` | Runtime mount count/release, compression restoration, no trusted partial state |

Schema 1 permits bounded additive fields for forward compatibility. Consumers
must still require every mandatory success field and reject duplicate keys,
oversized records, invalid types, or contradictory status/reason pairs.
The module verification hashes must also exactly equal the five validated
artifact payload hashes. Matching expected and actual hashes that are not
bound to the authenticated validation record are rejected.
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

## Installed recovery contract

`recoveryctl.sh status --json` emits one bounded schema-1 document. Its stable
top-level fields are `schemaVersion`, `status`, `reason`, `target`,
`moduleVerification`, `fallback`, and `actions`. Supported statuses are:

| Status | Meaning |
| --- | --- |
| `healthy` | All five modules match the running kernel and NVIDIA userspace |
| `recovery-required` | The active slot cannot start the exact NVIDIA stack |
| `fallback-active` | A mutually exclusive recovery profile is installed |
| `unknown` | Inspection was unsafe, malformed, excessive, or incomplete |

The default recovery action is `console`; it blacklists both driver families.
`igpu-desktop` requires a validated boot-VGA Intel/AMD device.
`nouveau-experimental` requires `--allow-nouveau` and is never returned as an
automatic action. `disable-fallback` refuses to mutate until
`moduleVerification.status` is `verified`.

`repair-online` re-enters the canonical installer with the exact support commit
recorded during guardian installation. It may install only an artifact accepted
by the existing exact-kernel resolver. No match leaves recovery active.
`rollback-plan` is coordination-only: its response deliberately does not name a
disk or slot because those identities belong to the recovery environment or
image-builder caller and must be revalidated immediately before mutation.

Delayed repair state is stored atomically outside the replaceable rootfs and
uses these UI phases: `offline_waiting`, `retry_scheduled`, `downloading`,
`verifying`, `rebuilding`, `installing`, `restored`, `cancelled`, and `failed`.
The transaction binds the exact kernel, NVIDIA version, and support commit.
NetworkManager connectivity events trigger an immediate retry where available;
a bounded systemd timer is the fallback. Network loss, DNS/TLS failure, captive
portals, reboots, and flapping connectivity never block boot or disable the
console fallback. Cancellation sets `automaticRetry=false` without touching a
verified slot or partially activating a candidate.

The first successful resolver response creates an immutable release plan. Its
SteamOS, NVIDIA, full kernel tag, release tag, asset name, and downloaded
archive hash remain fixed across connectivity retries and reboots. A newly
published exact certified artifact may be selected while the transaction is
still `offline_waiting`, before a plan exists. It cannot be spliced into an
active download/install; changing plans requires cancellation or completion.
A later certified equivalent does not invalidate a restored
`locally-built-verified` system and is considered only by an explicit future
maintenance transaction. Wrong-kernel or changed-NVIDIA publications remain
ineligible under the ordinary resolver policy.

### Open OPEMOS frontend boundary

`open_opemos_contract.py` converts a validated recovery status document into a
bounded schema-1 view model named **Open OPEMOS**. It supplies stable labels for
fallback, offline wait, retry, download, exact-kernel rebuild, install,
verification, restoration, and cancellation. Every button maps to an enumerated
`recoveryctl.sh` argument vector. The frontend may theme these states and
request SteamOS Desktop Mode, but it must not gain a general command runner.

In particular, the frontend must never accept a device pathname, regex-rewrite
a disk name into a script, run a caller-provided shell fragment, disable
fallback directly, or infer a kernel/artifact. Privileged operations remain
fixed `recoveryctl.sh` commands with validated enumerated profiles and identity
documents. `restore-graphics` is enabled only after exact module verification.
The versioned native desktop frontend and its virtual-display tests are owned
by the dedicated desktop application layer; this contract remains independent
of a specific GUI toolkit.
