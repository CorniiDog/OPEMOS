# OPEMOS consumer contracts

This directory contains support-owned, versioned contracts consumed by the
CLI, SteamOS desktop companion, SteamOS DRM/KMS interstitial, and OPEMOS.EXE.
Frontends may add presentation and session checks; they must not independently
reimplement compatibility, package-selection, signer, or mutation policy.

The planned two-consumer reviewed-lock data channel is described in
[`userspace-lock-generations.md`](userspace-lock-generations.md). It freezes the
schema-1 discovery/manifest field handoff for implementation planning while
remaining explicitly inactive until Core publishes a dedicated trust root,
publisher evidence, and installed-device lifecycle.

## Schemas

- `schemas/resolver-result-v2.schema.json` describes the additive resolver
  result emitted by `lib/resolve_target.py`.
- `schemas/installer-progress-v1.schema.json` describes one record following
  the `STEAMOS_NVIDIA_PROGRESS ` prefix. Cross-record monotonicity remains a
  stream property enforced by `lib/validate_install_contract.py`.
- `schemas/installer-result-v1.schema.json` describes the terminal result from
  `bootstrap/install_to_root.sh`. Successful results require the validation,
  five-module, userspace, initramfs-workspace, initramfs, receipt, and cleanup
  proof records. Cross-record identity and hash equality remain enforced by
  `lib/validate_install_contract.py`.
- `schemas/installer-validation-v1.schema.json` describes the complete verified
  validation proof nested in validation-only and successful mutation results.
  It includes authenticated input-source identity, package/dependency and
  module sets, boot policy, storage admission, compression measurement, and
  optional reviewed gaming-payload metadata. Security-critical package,
  keyring, dependency, module, and reviewed-profile records remain closed;
  explicitly additive containers permit bounded future fields.
- `schemas/installer-module-verification-v1.schema.json` describes both the
  mandatory five-module success proof and bounded failed mismatch diagnostics.
  Module records are closed; exact-kernel destination and authenticated
  validation-hash binding remain authoritative Core cross-record checks.
- `schemas/installer-userspace-verification-v1.schema.json` describes the
  mandatory reviewed-package success proof and bounded failed mismatch
  diagnostics. Package records are closed and bind filenames, versions,
  hashes, dependencies/providers, payload invariants, pacman consistency, and
  GSP firmware back to the reviewed lock and provenance.
- `schemas/installer-initramfs-verification-v1.schema.json` describes the
  success-only exact-kernel initramfs proof, including authenticated tools,
  configuration, images, listings, and the early-boot/rootfs-only module split.
- `schemas/installer-initramfs-workspace-v1.schema.json` describes target
  `/var/tmp` preflight, preparation-required, private backing, mounted success,
  and bounded failure states with explicit byte/inode capacity semantics.
- `schemas/installer-payload-receipt-v1.schema.json` describes the success-only
  six-document rootfs receipt, canonical receipt identity, exact target, and
  role-specific filenames and byte ceilings.
- `schemas/installer-gaming-payload-v1.schema.json` describes the closed
  `gaming-no-cuda-v1` selection embedded in validation. A reviewed record is
  only terminally authoritative when Core also binds it to the exact reviewed
  target, profile and policy hashes, userspace lock, and derived package set.
- `schemas/userspace-lock-discovery-v1.schema.json` describes the inactive,
  closed signed-discovery identity for reviewed userspace-lock data generations.
- `schemas/userspace-lock-generation-manifest-v1.schema.json` describes the
  inactive, closed immutable generation inventory. Core additionally enforces
  authority, sequence/predecessor, target-lock, manifest-hash, and total-size
  invariants across both documents.
- `schemas/userspace-lock-generation-request-plan-v1.schema.json` describes the
  deterministic, redirects-disabled request set derived only from the installed
  bootstrap policy and independently authenticated discovery, manifest,
  detached signatures, and payload identities. The planner performs no network
  access and accepts no caller-selected URL.
- `schemas/device-generation-result-v1.schema.json` describes bounded results
  and durable state from the inactive installed-device generation lifecycle.
- `schemas/device-generation-health-v1.schema.json` describes the closed,
  exact-generation health evidence required before last-known-good advances.
  `lib/device_generation_contract.py` is the semantic validator shared by the
  installed-device lifecycle and the deterministic compatibility matrix from
  `lib/generate_device_generation_fixtures.py`.

Unknown additive fields are permitted only where a schema explicitly allows
them. Closed objects reject them. Removing a required field, changing its
meaning, or tightening a previously valid value requires a new schema version.

`fixtures/resolver-compatibility-v2.json` is the bounded, strict-JSON
cross-frontend compatibility corpus for resolver schema 2. Consumers run every
case and compare the stable `expected` subset while requiring each listed
`absentFields` member to be absent. Human-readable messages are intentionally
outside the frozen subset.

`lib/generate_installer_result_fixtures.py` deterministically emits the
bounded installer-result schema-1 matrix. It covers validation-only and full
mutation success, mandatory-proof omissions, target/input identity failures,
incomplete cleanup, malformed and duplicate-key JSON, and safe additive
fields. It also includes bounded accepted module/userspace failure diagnostics
with matching phases and no success-only sibling proofs. The matrix declares
`message` unfrozen and compares only terminal acceptance/status plus the
authoritative validator's structural rules.

`lib/generate_installer_progress_fixtures.py` deterministically emits the
bounded installer-progress schema-1 stream matrix. It covers indeterminate
heartbeats, monotonic byte/item counters, phase and attempt resets, additive
fields and phases, malformed or duplicated JSON, counter regressions, and the
published line/stream limits. Oversized streams use one bounded repeat recipe;
consumers materialize that recipe for their limit test rather than embedding a
multi-megabyte fixture in the bundle.

Progress records intentionally do not carry a terminal state. Core emitters
stop before their process exits and Core launchers reap their child process
groups. Correlating a stream with a particular host operation and ignoring
data delivered after that operation has terminated are consumer session-
binding responsibilities, so they are not represented as progress-schema
accept/reject cases. Human-facing labels, weighting, animation, and percentage
composition are likewise frontend presentation rather than Core semantics.

`lib/generate_installer_validation_fixtures.py` deterministically emits the
bounded installer-validation schema-1 matrix. Accepted cases cover direct and
authenticated-bundle inputs plus safe additive fields. Rejection cases cover
missing identities and policy records, inconsistent input-source identities,
unsafe filenames, boot/dependency/storage mismatches, duplicate package
identities, bounded closure overflow, and hostile JSON. Human messages are not
part of its frozen expectations.

`lib/generate_installer_module_verification_fixtures.py` deterministically
emits the bounded module-verification schema-1 matrix. It covers the exact five
normalized modules, payload-hash binding, representation, target path,
ownership, mode and decompression invariants, bounded failure diagnostics,
safe top-level additions, and hostile JSON/document inputs. Human-readable
failure messages are intentionally unfrozen.

`lib/generate_installer_userspace_verification_fixtures.py` deterministically
emits the bounded userspace-verification schema-1 matrix. Structural acceptance
is distinct from exact-installation proof binding: a safe record with a
different package, lock, provenance, dependency/provider list, or NVIDIA
firmware version is representable but cannot prove the reviewed installation.
Dependency and provider arrays use set semantics: ordering is irrelevant,
duplicates are rejected, and exact binding compares their normalized values.

`lib/generate_installer_initramfs_verification_fixtures.py` deterministically
emits the bounded success-only initramfs-verification schema-1 matrix. It
separates structural record acceptance from exact target-kernel binding and
covers tool/config/image identities, bounded listings, the four-module
early-boot set, rootfs-only `nvidia-peermem`, confined module paths, safe
additive top-level metadata, and hostile JSON. Generation or inspection errors
remain typed failures in the outer installer result; Core does not emit a
failed nested initramfs proof.

`lib/generate_installer_initramfs_workspace_fixtures.py` deterministically
emits the bounded workspace schema-1 matrix. Structural acceptance is separate
from terminal binding: validation-only records use the 4096-byte/one-inode
target-directory probe, while mutation success requires a mounted workspace
whose byte requirement equals validated `initramfsReserveBytes` and whose inode
requirement is 4096. Finite, dynamically probed, and bind-target inode modes
remain distinct and contradictory capacity states fail closed.

`lib/generate_installer_payload_receipt_fixtures.py` deterministically emits
the bounded success-only payload-receipt schema-1 matrix. It requires the exact
ordered six-document inventory, canonical role-specific filenames and byte
ceilings, a closed exact target identity, and a receipt ID recomputed from the
canonical `{schemaVersion,target,records}` identity. Structural acceptance is
separate from terminal target binding. Recorded evidence hashes are verified
against the rootfs-resident files by `lib/payload_receipt.py verify`, including
during independent final-image inspection.

`lib/generate_installer_gaming_payload_fixtures.py` deterministically emits
the bounded gaming-payload schema-1 matrix. It covers the closed
`not-requested` state, the exact reviewed profile, target/profile/policy/lock
and package binding failures, capability and package-set contradictions, and
hostile JSON/document inputs. Structural record acceptance is separate from
terminal authority binding. This security-critical record does not permit
additive schema-1 fields; a future representation requires a new schema.

`lib/generate_userspace_lock_generation_fixtures.py` deterministically emits
the bounded inactive discovery/manifest compatibility matrix consumed by
`lib/userspace_lock_generation_contract.py`. It distinguishes structural
document validity, discovery-to-manifest equality, and activation authority.
Accepted cases include a fresh signed bootstrap anchor, an exact next
generation, a publisher sequence gap with direct predecessor continuity, and
bounded authenticated catch-up across missed published generations. Fresh
activation requires an installed exact bootstrap checkpoint; an old signed
mirror response is not treated as fresh. Unknown schemas/policies,
replay/downgrade, missing/tampered/forked/excessive lineage, target or lock
mismatch, nonportable filenames, duplicate identities, hostile JSON, and
oversized documents or inventories fail closed. The authorized opaque host
cache identity is the authenticated generation manifest SHA-256; cache and
network lifecycle remain consumer-owned. Consumers durably retain the paired
sequence and manifest hash for active and last-known-good identities plus a
monotonic high-water sequence that rollback cannot decrease.

`bootstrap/generationctl.sh` is the installed-device CLI entrypoint backed by
`lib/device_generation_lifecycle.py`. Its local `activate`, `status`, `check`,
`acknowledge-health`, `rollback`, and `prune` paths are implemented and tested
against the same generation contracts. The cache is private, create-only,
fsync-backed, independently retained, and separate from desktop binary updates
and OPEMOS.EXE host storage. Health acknowledgement requires closed evidence
bound to the exact active identity. `update` and `update-or-repair`
intentionally fail with `device_generation_network_inactive` until production
trust and endpoint policy exist; caller-selected trust is accepted only under
the explicit development-test override. Cached generations preserve the
canonical signed discovery and sequence-specific manifest filenames. Missed-
generation catch-up accepts bounded document-only lineage directories; it does
not require downloading intermediate payloads.
Device state is committed through alternating revisioned markers. Restart
reconciliation removes bounded abandoned marker temporaries, migrates the
legacy single marker, and fails closed if immutable cache contents prove that a
newer sequence exists than the surviving durable high-water.
Cache retention is marker-first and preserves active/pending-active plus LKG.
New generations require conservative logical-byte and finite-inode admission;
fixed-depth descriptor-relative cleanup rejects links, special files, and
unbounded abandoned trees.

The device compatibility matrix distinguishes JSON/schema structure from
durable lifecycle invariants: a healthy active generation must equal LKG,
pending health must not already equal LKG, active/LKG sequences cannot exceed
high-water, and health evidence must bind the exact active identity. Result and
health objects are closed in schema 1; arbitrary additive fields fail closed.

When resolution returns `no_compatible_artifact` with reason
`no_compatible_release`, `nextAction` explicitly authorizes only the existing
exact-kernel `bootstrap/build_for_target.sh` contract and includes a
hash-addressed reviewed build plan. The plan pins the NVIDIA version, source
repository/ref/commit, and known-good baseline artifact identity. Targets with
no reviewed plan return `no_reviewed_exact_target_build_plan`; publication-
integrity failures never advertise a build fallback.

## Installer bundle manifest

`lib/installer_bundle_manifest.py` creates the canonical consumer bundle from
an immutable Git commit. It reads blobs and executable bits directly from Git,
not from the mutable working tree:

```bash
COMMIT="$(git rev-parse HEAD)"
python3 lib/installer_bundle_manifest.py create \
  --support-commit "$COMMIT" \
  --output "opemos-installer-bundle-${COMMIT}.json"
```

`--dry-run` writes the same canonical JSON to stdout. Output files are
create-only. Validate an existing manifest and every committed blob with:

```bash
python3 lib/installer_bundle_manifest.py validate \
  --manifest "opemos-installer-bundle-${COMMIT}.json" \
  --expected-support-commit "$COMMIT"
```

The manifest itself is a release or handoff artifact, avoiding an impossible
self-reference where a committed file attempts to contain its own commit ID.
Consumers pin the exact support commit and manifest SHA-256, then verify every
listed path, role, mode, size, and hash before execution.

## Immutable bundle publication

Maintainers publish a manifest in its own create-only release. This command
never edits an existing release and does not alter target-specific NVIDIA
artifact releases:

```bash
COMMIT="$(git rev-parse HEAD)"
bootstrap/publish_installer_bundle.sh \
  --support-commit "$COMMIT" \
  --dry-run
```

Remove `--dry-run` only after reviewing the canonical plan. The release tag
and asset name are both `opemos-installer-bundle-<full-commit>`. A consumer
must pin the manifest SHA-256 independently; co-location in a GitHub release is
not an independent trust signal. Production publication is fixed to
`CorniiDog/open-gpu-kernel-modules-steamos-support` and fails before generating
a plan when the checkout's `origin` does not normalize to that exact project.
Only the explicit `--development-repository OWNER/REPO` option permits another
release destination; it does not change the canonical repository identity
embedded in the bundle manifest.
