---
layout: page
title: Commands and contracts
description: API-style command, result, progress, trust, and failure reference.
---

## Contents

- [Entry points](#entry-points)
- [Resolver contract](#resolver-contract)
- [Source intent authorization](#source-intent-authorization)
- [Installed-device lock generations](#installed-device-lock-generations)
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
| `bootstrap/generationctl.sh` | Installed Core/CLI | Private reviewed-lock generation store | Device-generation schema 1 JSON |
| `lib/consume_appliance_generation.py` | Managed x86_64 appliance | Create-only private installer-input staging | Development generation preparation JSON |
| `lib/source_intent_contract.py` | Image builder/CLI | No | Source-authorization schema 1 JSON |

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

## Source intent authorization

`source-intent-v1.schema.json` records exactly one requested mode: automatic,
an exact published artifact, a reviewed exact-target local build, an exact
reviewed project source, or explicit upstream development. Core's
`source_intent_contract.py` consumes that intent together with the bounded
release metadata and its own reviewed build policy, then returns
`source-authorization-v1.schema.json`.

```bash
python3 lib/source_intent_contract.py \
  --intent /shared/source-intent.json \
  --releases /shared/releases.json
```

Automatic may select only the normal resolver's published artifact or reviewed
exact-target action. Exact publication and project requests must match their
requested immutable identity. Upstream development requires an explicit
acknowledgement, the canonical NVIDIA repository, exact version tag and commit;
it returns `development-unverified` with publication forbidden. Rejection
never falls back to a different mode. The deterministic fixture generator
defines malformed, unsupported, unreviewed and successful outcomes without
freezing human text.

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

## Installed-device lock generations

`bootstrap/generationctl.sh` manages the installed device's own reviewed-lock
cache. It is separate from OPEMOS.EXE's host cache and from the desktop
companion's binary-update generations.

For host-to-appliance integration, OPEMOS.EXE stages the closed
`appliance-generation-handoff-v1.schema.json` receipt and its flat file set.
Core's `consume_appliance_generation.py` treats that receipt only as transport
integrity and independently reauthenticates the exact signed generation before
mapping its reviewed lock to installer inputs. This path is currently guarded
by `--development-test`; no production generation authority is configured.

Implemented inactive commands are:

| Command | Effect |
| --- | --- |
| `activate` | Authenticate and create-only stage an exact local generation, then atomically select it pending health |
| `activate-downloaded` | Reauthenticate a manifest-hash-selected entry from Core's separate device download cache, then use the same activation/health transaction |
| `status` | Read bounded durable active/LKG/high-water state |
| `check` | Revalidate the active and last-known-good cached payloads |
| `acknowledge-health --evidence FILE` | Advance LKG using exact generation-bound recovery evidence |
| `rollback` | Reauthenticate and restore LKG without decreasing high-water |
| `prune` | Retain active, LKG, and a bounded recent set |

For development and contract testing only, `update` and `update-or-repair`
accept `--transport PROGRAM` plus an exact SteamOS/kernel/NVIDIA/architecture
target. Core snapshots the single-link executable and invokes its private copy
once for each bounded acquisition phase with `--destination DIRECTORY` and
`--request-plan FILE`. Core alone derives that immutable plan from the installed
bootstrap policy and, after signature verification, the authenticated discovery
and manifest snapshots. The transport receives exact URLs and identities; it
does not select a release, redirect, filename, or hash. It runs with only `PATH`
and C-locale variables under a five-minute ceiling. Exit 69 means the source is
unavailable and exit 73 means staging storage is full; every other nonzero exit
is a generic transport failure.

Each phase must return exactly the requested single-link files. Core rejects
missing, extra, substituted, oversized, or renamed output and rechecks the
installed policy, keyring, and checkpoint identities between phases and before
publication. Every trust snapshot independently requires the configured owner,
a single link, and no group/other write access. Core also binds each containing
trust directory's device, inode, owner, group, and mode for the full operation.
Each file guard is captured from the same open descriptor as its validated
bytes, so a concurrent file or directory replacement cannot bind stale bytes
to a new identity. Payload responses are streamed through exact size/SHA-256 checks
into private mode-0400 staging rather than accumulated in memory. Successful
acquisition authenticates the descriptor, manifest,
both detached signatures, authority, exact target, lineage, and every payload
size/hash before create-only publication under
`downloads/<manifest-sha256>`. It does not change active, last-known-good,
health-pending, or high-water state. Partial and hostile staging is removed
without following links on failure, timeout, and catchable cancellation;
abandoned confined staging is removed by the next locked lifecycle operation.
A separate Core watchdog owns the isolated transport process group and monitors
a close-on-exec
control pipe held only by the lifecycle process. If that owner is terminated by
SIGKILL, pipe EOF makes the watchdog terminate and reap the transport group;
the next locked operation removes its uncommitted staging tree. A transport may
not daemonize or escape its assigned process group. Real SteamOS observation
remains a production gate. This injected surface is disabled unless the
explicit development trust override is active.

Results follow
[`device-generation-result-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/device-generation-result-v1.schema.json).
Health evidence follows the closed
[`device-generation-health-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/device-generation-health-v1.schema.json).
Core validates both through `lib/device_generation_contract.py`; the canonical
compatibility matrix is emitted deterministically by
`lib/generate_device_generation_fixtures.py`.

Health acknowledgement and rollback both reauthenticate their selected cached
generation against the complete currently installed policy/keyring authority.
They also independently observe the current SteamOS version, running kernel,
architecture, and Core's installed NVIDIA identity, then require one exact
target lock in the selected generation. The observed identity must also match
the current rootfs's confined, owner-controlled payload receipt after all six
receipt files and hashes are reverified from one opened receipt-directory
descriptor. Core binds that directory's device, inode, ownership, and mode
through the complete read, so evidence from replaced directory instances cannot
be mixed into one health decision; a persistent `/var` marker alone is not
sufficient. Health evidence cannot substitute for this target observation,
and a last-known-good generation for another A/B slot cannot be restored after
a slot transition. Core rechecks same-descriptor trust guards before committing
state. A valid generation from an older or otherwise different policy cannot
become LKG or be restored merely because its cached hashes remain intact.

The observation root is `/` in production and cannot be selected by the
caller. A separate `--target-root` exists only behind the explicit development
trust override so confined synthetic roots can exercise A/B transitions. It is
rejected before any lifecycle command when production policy is active.

Core persists the accepted `{generation,target,receiptId}` as a private health
marker. A pending marker records the prior state revision and hash before the
shared state advances; after the state commit, Core atomically promotes it.
Locked restart reconciliation discards an uncommitted intent or completes a
committed one. Consequently cancellation or power loss cannot silently bind an
old LKG acknowledgement to a new slot, and `status` refuses a healthy/LKG state
whose internal marker is absent or inconsistent. This marker is device-local
transaction state, not a shared frontend schema.

Local activation also uses a private internal intent record between immutable
generation publication and the alternating durable state markers. A locked
restart binds that intent to the exact prior state revision and hash. It removes
an authenticated-but-uncommitted cache generation, or clears the intent after
verifying that the candidate is already the durable active generation. This
internal crash-recovery record is not a shared wire contract and does not alter
the discovery, generation-manifest, state, health, or result schemas.

Production `update` and `update-or-repair` remain fail-closed because no
reviewed data-generation signer, keyring, bootstrap checkpoint, or canonical
device discovery endpoint is configured. Local activation is an integration
surface, not permission to replace the legacy reviewed lock path.

The inactive shared bootstrap contract is defined by the closed
`userspace-lock-bootstrap-policy-v1.schema.json` and
`userspace-lock-bootstrap-checkpoint-v1.schema.json` schemas. The policy binds
authority/keyring identity, strong OpenPGP algorithms, canonical channel and
immutable-release namespaces, supported schema versions, and replay rules. The
checkpoint binds those exact policy bytes to a minimum sequence and manifest
hash. `lib/generate_userspace_lock_bootstrap_fixtures.py` provides the bounded
deterministic acceptance matrix; its `.invalid` endpoints and synthetic hashes
are not production configuration.

After those inputs are independently authenticated,
`lib/userspace_lock_request_plan.py` derives the closed immutable request plan
described by `userspace-lock-generation-request-plan-v1.schema.json`. It fixes
canonical discovery/signature requests plus manifest/signature/payload release
URLs, hashes, sizes, order, origin, and `redirects=false`; it is a pure planner,
not an HTTP client. The matching deterministic hostile matrix is emitted by
`lib/generate_userspace_lock_request_plan_fixtures.py`.

The authentication handoff uses
`lib/userspace_lock_verifier_evidence.py`: Core invokes a trusted detached-
signature verifier with the exact policy-bound keyring, document, and signature
snapshots, validates its bounded exit/status result, and returns an immutable
in-process capability. `userspace-lock-verifier-evidence-v1.schema.json` covers
the corresponding audit record only. Parsing or fabricating that JSON never
creates the capability. The deterministic matrix is emitted by
`lib/generate_userspace_lock_verifier_evidence_fixtures.py`.

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
Cross-frontend consumers can generate the canonical compatibility matrix with
`python3 lib/generate_installer_result_fixtures.py`. Its output is canonical,
strict JSON bounded to 512 KiB; human-readable `message` values are explicitly
unfrozen. Accepted failed-module and failed-userspace cases preserve one strict,
bounded actionable diagnostic subdocument with its matching phase and omit all
success-only sibling proofs. The generator itself is authenticated as part of
the Core bundle.

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
- Phase names are stable producer tokens. Consumers accept an otherwise-valid
  unknown token as an additive phase and present it as indeterminate until
  their own presentation mapping understands it.
- Determinate counts never decrease or change total/unit within an attempt.
- `hashing` is one aggregate byte sequence per attempt. Its fixed total is the
  sum of every immutable archive, checksum, provenance, package, detached
  signature, keyring, userspace-lock, and optional gaming-profile input; it
  never restarts for an individual file.
- Records never contain paths, credentials, arbitrary messages, or subprocess
  output.
- Stderr lines without the prefix are not part of this API.
- The canonical bounded compatibility corpus is emitted by
  `lib/generate_installer_progress_fixtures.py`. It freezes accept/reject and
  parsed-record-count expectations, not optional human wording.

The progress record has no terminal marker. The final schema-1 installer
result and process exit establish termination. Core stops emitting before exit
and reaps its subprocess group; OPEMOS.EXE owns binding received bytes to the
correct host operation and discarding out-of-session data. Frontends also own
labels, progress weights, animation, layout, and accessibility.

## Validation proof

[`installer-validation-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-validation-v1.schema.json)
defines the complete verified `validation` object embedded in validation-only
and successful installation results. It preserves whether inputs arrived
directly or from one exact authenticated cache bundle, including that bundle's
SHA-256 identity. Archive/provenance hashes, reviewed lock and keyring,
packages, dependency closure, five module hashes, Holo database identity,
`/boot` and `/efi` policy, storage admission, compression evidence, and the
gaming-payload selection are mandatory.

Top-level validation, input-source, boot, storage, and compression objects are
additive. Closed cryptographic identity, package, dependency, module, and
reviewed gaming-profile records reject unknown fields because an ignored field
could otherwise create two interpretations of the authenticated identity.
Cross-field equality, uniqueness, accounting, and measured-Btrfs invariants are
enforced by `write_install_result.py`; JSON Schema alone is not the mutation
authority.

The nested gaming selection has its own closed contract in
[`installer-gaming-payload-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-gaming-payload-v1.schema.json).
`not-requested` records contain only schema, status, and profile identity.
`reviewed` records preserve the exact target, delivery process, omitted and
preserved capabilities, derived package ownership, hashes, signer identities,
sizes, and saved-byte accounting. Structural validity alone is not permission
to install: Core recomputes the record from the bundled reviewed policy,
exact profile, and reviewed userspace lock, then binds both derived packages to
the validated package records. Unsupported and unreviewed profiles are resolver
capability states and cannot appear as validation proof.

The canonical compatibility matrix is generated by
`lib/generate_installer_gaming_payload_fixtures.py`. Gaming payload schema 1 is
closed at every level because ignored metadata could create a second meaning
for an authenticated omission. Additive evolution therefore requires a new
gaming-payload schema version even though the enclosing validation document
allows bounded additions.

`generate_installer_validation_fixtures.py` publishes the deterministic
cross-frontend acceptance matrix. Its expected subset freezes acceptance, not
human-readable wording.

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

The initramfs workspace record is defined by
[`installer-initramfs-workspace-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-initramfs-workspace-v1.schema.json),
with its deterministic matrix generated by
`lib/generate_installer_initramfs_workspace_fixtures.py`. Validation-only
results preserve either an already-safe target `/var/tmp` or a safe
`preparation-required` state using the bounded 4096-byte/one-inode preflight.
Mutation success instead requires the private workspace to remain mounted with
mode 1777, 4096 required inodes, and `requiredBytes` exactly equal to the
validated initramfs storage reserve.

`finite-statvfs` requires a numeric sufficient inode count. `dynamic-probed`
records successful creation of the required temporary inode set and therefore
carry a null count. `not-applicable-bind-target` is restricted to target-only
checks whose backing mount will replace the directory. `dynamic-probe-failed`
is failure-only. Structural failure diagnostics remain usable by failed
installer results, but cannot satisfy validation or mutation success. Human
messages are unfrozen; required semantics are closed while bounded top-level
metadata remains additive.

The standalone module record is defined by
[`installer-module-verification-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-module-verification-v1.schema.json).
Its canonical compatibility matrix is generated by
`lib/generate_installer_module_verification_fixtures.py`. A successful record
must contain exactly the five expected module identities as normalized
`.ko.zst` files beneath the exact target kernel, with decompressed payload
hashes equal to the authenticated validation hashes, mode 0644, and UID/GID 0.
The schema permits bounded additive top-level metadata, but module records are
closed so an ignored field cannot change the meaning of a verified payload.
Failed mismatch records remain consumable diagnostics and can never satisfy a
successful installation proof.

The userspace proof is defined by
[`installer-userspace-verification-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-userspace-verification-v1.schema.json),
with its canonical matrix generated by
`lib/generate_installer_userspace_verification_fixtures.py`. Each closed package
record preserves its reviewed filename, version, archive hash, dependencies and
providers, plus positive pacman query/integrity and package-payload path, hash,
mode, ownership and link checks. The proof also binds the reviewed userspace
lock and provenance hashes, the confined Holo database result, and sorted GSP
firmware paths under the exact NVIDIA version. Dependency/provider arrays use
set semantics: ordering is irrelevant and duplicate entries are rejected.
Only bounded top-level
additions are allowed. Failed package diagnostics contain safe relative entry
identities and ordered failure categories, never host paths or package bytes.

The initramfs proof is defined by
[`installer-initramfs-verification-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-initramfs-verification-v1.schema.json),
with its compatibility matrix generated by
`lib/generate_installer_initramfs_verification_fixtures.py`. It records the
exact target kernel, authenticated `mkinitcpio` and `lsinitcpio` identities,
configuration identity, and one to 32 uniquely named generated images. Each
image carries bounded size/listing counts, image and listing hashes, the exact
configuration path, and exactly `nvidia`, `nvidia_modeset`, `nvidia_uvm`, and
`nvidia_drm`. `nvidia-peermem` is explicitly rootfs-only and is not an
early-boot requirement.

This nested record is success-only. Initramfs generation or verification
errors are represented by the outer failed installer result. A record is
self-describing, so an alternate well-formed image hash remains structurally
valid; exact kernel equality is a cross-record success invariant, and the image
builder independently verifies the exported image. Nested records are closed;
only bounded top-level additions are permitted for schema-1 evolution.

The rootfs payload receipt is defined by
[`installer-payload-receipt-v1.schema.json`](https://github.com/CorniiDog/OPEMOS/blob/main/contracts/schemas/installer-payload-receipt-v1.schema.json),
with its deterministic matrix generated by
`lib/generate_installer_payload_receipt_fixtures.py`. It contains exactly six
ordered records: build information, provenance, validation, module
verification, userspace verification, and initramfs verification. Each role
has one canonical filename and its producer-owned byte ceiling. Target and
record objects are closed; bounded top-level additions remain compatible.

Core recomputes `receiptId` over canonical compact JSON containing
`schemaVersion`, the exact target, and the ordered records. A terminal success
also requires the receipt target to equal the installation target. The record
SHA-256 values are self-described structural identities in the result contract;
`payload_receipt.py verify` independently hashes the six rootfs-resident files
and rejects any mismatch. OPEMOS.EXE must repeat that mounted-image verification
before accepting the final image. Receipt creation or verification failures use
the outer installer failure result rather than a partial nested receipt.

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
`moduleVerification.status` is `verified`. The installed `recoveryctl` always
requests strict payload-receipt verification. A healthy result then also
requires an exact receipt target, the canonical five `.ko.zst` destinations,
root-owned mode 0644 metadata, and fresh decompressed payload hashes equal to
the installer-committed module verification evidence. Version/vermagic metadata
alone cannot disable fallback or finalize repair. The lower-level status helper
retains an explicit non-receipt mode for compatibility; installed recovery
policy never uses that weaker mode. This stronger decision does not add or
remove recovery-status fields.
Direct fallback removal additionally requires one enumerated fallback profile
to be active. It checks that condition once before prompting, acquires both the
recovery-operation and global lifecycle locks, then repeats the complete
receipt-bound status check under those locks. A changed target, receipt,
module, or fallback state leaves the fallback files and boot policy untouched.
Recovery mutation installs its termination and readonly-restoration handlers
before any command can make the live root writable. Long-running `mkinitcpio`,
GRUB regeneration, and canonical online-repair children run in isolated process
groups. INT, TERM, or HUP terminates and reaps that group, restores readonly
state, releases lifecycle locks through process exit, and returns status 130;
the signal handler never returns into a partially cancelled mutation.
Persistent fallback state is a closed canonical schema-1 record containing
only `active=true` and one enumerated profile. Duplicate keys, extra fields,
non-boolean activation, unknown profiles, non-finite values, or noncanonical
encoding make status `unknown`; they are never interpreted as an active safe
profile. Fallback enablement publishes this record through a dedicated
nonblocking lock, an exclusive private temporary, file and parent-directory
`fsync`, atomic replacement, and post-publication byte verification. Removal
verifies the exact safe regular-file identity before unlinking. A later
operation cleans only bounded, owner-controlled abandoned fallback temporaries.

The boot guardian observes every installed NVIDIA identity marker rather than
accepting the first readable value. Missing, malformed, conflicting, or
expected-policy-mismatched identities make status `unknown`. A failed or
unknown status subprocess is itself a console-fallback condition; shell
`errexit` must never terminate the guardian before the safe profile is enabled.
The guardian treats that initial observation as provisional: before activating
fallback it acquires the recovery-operation lock followed by the global
lifecycle lock and repeats receipt-bound module verification. If the exact
payload recovered while it waited, no fallback mutation occurs. A repeated
failure or an indeterminate recheck still activates the console profile.
The installed guardian always supplies its pinned `nvidia-version` policy by
confined relative name. Once supplied, that policy file is mandatory and must
exactly match the independently observed installed identity; it cannot fall
back to the installed marker when missing, empty, malformed, or stale.
Status inputs are read through confined descriptors and must remain
single-linked, owner-controlled, non-writable by other identities, and stable
for the complete bounded read. Module verification rejects duplicate candidates
instead of choosing by pathname order. The live root requires `modinfo -n` to
resolve exactly one confined candidate; offline roots require exactly one.
Selected module files must remain single-linked, owner-controlled,
non-writable, size-bounded, and identity-stable across both metadata queries.

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
The NVIDIA version and support commit come from the persistent guardian
snapshot on the shared SteamOS home filesystem, not from a replaceable A/B
rootfs slot. Core reads both single-linked mode-`0644` records through one
owner-controlled directory descriptor, enforces their canonical text formats,
and verifies file and directory identities across the read. A missing,
malformed, writable, linked, replaced, or unexpected-owner policy fails closed.
Its schema-1 state is closed, bounded to 64 KiB, canonical JSON with unique
keys, root-owned mode `0600`, single-linked, and protected by its own
descriptor-bound lock. Phase changes follow a closed transition graph and the
attempt counter is bounded. Publication fsyncs both the replacement file and
its parent directory. A second begin, unsafe state object, invalid transition,
or concurrent writer fails without replacing the prior state.
Terminal transaction removal is a distinct locked operation. It accepts only a
fully validated `restored` or `cancelled` record, rechecks the exact inode before
unlinking it, fsyncs the containing directory, and verifies absence. An active,
malformed, replaced, linked, or unsafe transaction is never removed.
When SteamOS independently activates a different kernel, recovery removes the
prior immutable release plan and atomically retargets only an active transaction
to the newly observed kernel plus the same authenticated persistent policy. The
replacement restarts at `offline_waiting`; it never selects a SteamOS slot.
Cancellation remains terminal and cannot be bypassed by automatic retargeting.
After independent exact-target module verification, Core may reconcile an
active transaction directly to `restored` even if interruption prevented the
ordinary intermediate phase writes. Reconciliation requires exact equality of
kernel, NVIDIA version, and support revision; it is idempotent for the same
restored record and rejects cancellation or any identity mismatch. A new
transaction first removes any validated orphan release plan, preventing stale
publication identity from being inherited after interrupted cleanup.
Each transaction and release-plan operation has a closed argument set; fields
belonging to another operation are rejected rather than ignored. Concurrent
readers either return the same canonical record or fail on the nonblocking
descriptor lock. Kernel-released locks after process death do not authorize any
state change, and the next operation must still validate the complete durable
record before proceeding.

All mutating recovery control operations also hold one recovery-operation
lock. This serializes guardian fallback, repair, cancellation, and manual
fallback changes across the complete workflow. Target mutation separately
takes the ordinary installer lifecycle lock; the two locks remain distinct so
the nested canonical installer can take its own lifecycle lock without
self-deadlocking.
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
A plan is closed canonical JSON bounded to 64 KiB in an owner-controlled
directory. It is mode `0600`, single-linked, read through a no-follow
descriptor, and identity-stable across every read and update. Direct plan
operations share a descriptor-bound nonblocking lock. Initial publication is
create-only; archive binding replaces only the exact plan inode that was read
and only after hashing an owner-controlled, non-writable, single-linked regular
archive whose pathname and descriptor identities remain equal.
Plan removal uses the same plan lock, complete canonical validation, exact inode
recheck, parent-directory fsync, and absence verification. Terminal restart
cleanup removes the plan before its transaction record, so interruption cannot
leave an orphaned plan beside no transaction. Cancellation tolerates an absent
plan but refuses to erase a malformed or replaced one.
A later certified equivalent does not invalidate a restored
`locally-built-verified` system and is considered only by an explicit future
maintenance transaction. Wrong-kernel or changed-NVIDIA publications remain
ineligible under the ordinary resolver policy.

Installed-device generation health is acknowledged only when three identities
agree: the independently observed running target, the rootfs payload receipt,
and the selected generation's exact target-lock record. Core compares both the
reviewed lock filename and SHA-256 from the receipt's authenticated validation
evidence. Merely finding the same SteamOS/kernel/NVIDIA target in two different
generations is insufficient. This prevents a healthy receipt produced from one
reviewed lock from acknowledging another generation for the same target.

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
