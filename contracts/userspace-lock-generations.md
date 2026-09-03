# Reviewed userspace-lock data-generation handoff

Status: design contract; not yet an active production schema or trust root.

This handoff defines the stable data boundary shared by OPEMOS.EXE and the
installed Core/CLI. It does not authorize either consumer to copy the other's
cache, transport, activation, or rollback implementation. `BOUNDARIES.md`
remains authoritative if this design summary conflicts with it.

## Purpose and channel separation

A reviewed userspace lock is data. Publishing a compatible lock must not
require rebuilding OPEMOS.EXE, reinstalling Core/CLI, updating a desktop or
interstitial binary, or reimaging SteamOS. Binary/runtime releases and reviewed
userspace-lock data generations use separate signing policies, sequence state,
stores, activation markers, health acknowledgements, and rollback decisions.

Core owns closure audit, candidate finalization, signer/keyring and target
policy, descriptor/manifest schemas, canonical publication, and interpretation.
OPEMOS.EXE owns host acquisition and cache activation. Installed Core/CLI owns
installed-device discovery, acquisition, cache activation, repair integration,
retention, health acknowledgement, rollback, and command/status interfaces.

## Stable discovery assets

Consumers obtain two canonically named files from an independently configured
Core discovery location:

- `opemos-userspace-lock-discovery-v1.json`
- `opemos-userspace-lock-discovery-v1.json.sig`

The descriptor contains no URL. Redirect and endpoint policy belongs to each
consumer's networking layer. Consumers first snapshot both bounded files, then
verify the detached signature with the locally installed reviewed data keyring
and signer policy before parsing or acting on descriptor metadata.

The schema-1 descriptor is a closed canonical JSON object with exactly these
top-level fields:

| Field | Required value |
| --- | --- |
| `schemaVersion` | Integer `1` |
| `kind` | `opemos-userspace-lock-discovery` |
| `channel` | `reviewed` |
| `sequence` | Positive unsigned 64-bit generation sequence |
| `publishedAt` | Canonical UTC RFC 3339 timestamp |
| `authority` | Closed authority identity below |
| `compatibility` | Closed consumer/schema requirements below |
| `generation` | Closed immutable release identity below |
| `targets` | Sorted, unique exact-target lock records below |

`authority` contains exactly `policyId`, `policySchemaVersion`, `policySha256`,
`keyringFilename`, `keyringSha256`, and `signingKeyFingerprint`.
`policyId` is `opemos-userspace-lock-generations`; schema version is `1`; names
are plain basenames; hashes are lowercase SHA-256; the fingerprint is the full
uppercase fingerprint accepted by the pinned policy. All values must equal the
consumer's installed trust root. A descriptor cannot introduce or replace its
own authority.

`compatibility` contains exactly `discoverySchemaVersion`,
`generationManifestSchemaVersion`, `userspaceLockSchemaVersion`, and
`minimumInstallerResultSchemaVersion`. Schema 1 sets each to integer `1`.
Unknown or unsupported values fail closed; consumers do not guess compatibility.

`generation` contains exactly `releaseTag`, `manifestFilename`,
`manifestSha256`, `manifestSize`, `signatureFilename`, `signatureSha256`,
`signatureSize`, and `previousManifestSha256`. Filenames are plain basenames,
sizes are positive and bounded by the eventual schema, and the predecessor is
either null for the first generation or the lowercase SHA-256 of the previous
authorized manifest. The release tag and filenames are identities, not URLs.

Each closed `targets` entry contains exactly `target` and `lock`. `target`
contains exactly `steamosVersion`, `kernelVersion`, `nvidiaVersion`, and
`architecture`; matching is exact and the current architecture is `x86_64`.
`lock` contains exactly `filename`, `schemaVersion`, `sha256`, and `size`.
Entries are sorted by the four-field target tuple and duplicate targets are
invalid. A consumer selects only an exact target; absence is a safe no-match.

## Immutable generation manifest

After authenticating discovery, a consumer downloads the exact manifest and
detached signature named by `generation`, verifies both byte identities, and
authenticates the manifest with the same installed data-generation authority.
The closed schema-1 manifest contains exactly:

- `schemaVersion`: integer `1`;
- `kind`: `opemos-userspace-lock-generation`;
- `channel`: `reviewed`;
- `sequence` and `publishedAt`, exactly equal to discovery;
- `authority`, byte-for-byte equal to discovery;
- `previousManifestSha256`, equal to discovery;
- `targetLocks`, sorted unique target/lock identities equal to discovery; and
- `files`, a sorted unique closed inventory of `role`, `filename`, `size`, and
  `sha256` records.

Allowed file roles are closed by the future schema and include reviewed locks,
package archives, detached package signatures, minimal keyrings, signer/target
policies, optional reviewed payload profiles, and required provenance. The
manifest cannot authorize executable Core/runtime replacement. Every selected
lock must bind its complete package set to manifest file records with no missing
or extra inputs.

## Acceptance and activation invariants

Both consumers apply the same semantic order:

1. Snapshot and authenticate the discovery descriptor with the installed trust
   root before parsing identities.
2. Require a supported closed schema, exact authority and compatibility, a
   sequence above the durable high-water mark, and a valid predecessor chain.
3. Select an exact target without fallback or approximation.
4. Download only the named manifest/signature and selected manifest-owned files.
5. Reauthenticate the manifest and verify every file's name, role, size, hash,
   package signature, lock membership, and target identity.
6. Stage privately, fsync, publish an immutable local generation, then atomically
   switch the consumer-local active marker while preserving previous and
   high-water state.
7. Acknowledge health only after that consumer's full validation succeeds;
   otherwise restore the independently revalidated last-known-good generation.

Unknown authority/schema, replay or downgrade, target mismatch, partial or
changed input, ENOSPC/inode exhaustion, cancellation, failed health validation,
or cleanup failure cannot modify the active generation or high-water mark.
Network unavailability is never permission to weaken trust or discard a valid
cached generation.

## Migration gate

This document freezes the intended field names for cross-project planning but
does not activate them. Core must still publish JSON Schemas, deterministic
valid/hostile/replay fixtures, a dedicated reviewed public keyring and policy,
publisher evidence, and installed-device lifecycle commands. Legacy embedded
locks and both consumers' existing paths remain until unit, integration,
cancellation, cleanup, failure-injection, power-loss, and cross-repository
equivalence tests pass.
