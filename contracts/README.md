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

Unknown additive fields are permitted. Removing a required field, changing its
meaning, or tightening a previously valid value requires a new schema version.

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
