# OPEMOS consumer contracts

This directory contains support-owned, versioned contracts consumed by the
CLI, SteamOS desktop companion, SteamOS DRM/KMS interstitial, and OPEMOS.EXE.
Frontends may add presentation and session checks; they must not independently
reimplement compatibility, package-selection, signer, or mutation policy.

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

Unknown additive fields are permitted. Removing a required field, changing its
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
fields. The matrix declares `message` unfrozen and compares only terminal
acceptance/status plus the authoritative validator's structural rules.

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
not an independent trust signal.
