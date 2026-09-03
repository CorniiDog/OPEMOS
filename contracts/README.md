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

Unknown additive fields are permitted. Removing a required field, changing its
meaning, or tightening a previously valid value requires a new schema version.

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
