# Reviewed userspace-lock data-generation handoff

Status: inactive schema contract; no production trust root or endpoint exists.

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

Those basenames are protocol constants, including when a consumer persists
offline lineage. The signature is an OpenPGP v4 detached document signature
(`openpgp-detached-v1`). Consumers require exactly one valid signature from the
installed policy fingerprint and accept only OpenPGP hash algorithm identifiers
8, 9, or 10 (SHA-256, SHA-384, or SHA-512). The pinned signer identity constrains
the public-key algorithm. A weak digest, ambiguous `VALIDSIG` set, malformed
status, or signature by another trusted-keyring member fails closed.
`lib/generate_openpgp_status_fixtures.py` publishes the bounded deterministic
status matrix for primary-key/subkey binding, SHA-256/384/512 acceptance,
weak/expired/revoked/multiple signature rejection, malformed records, and the
status-output ceiling. Human GnuPG diagnostics are not contract data.

The descriptor contains no URL. Core's installed bootstrap policy pins the
canonical HTTPS origin and channel/release namespaces; redirect handling,
transport, and physical caching remain each consumer's networking
implementation. Consumers first snapshot both bounded files, then
verify the detached signature with the locally installed reviewed data keyring
and signer policy before parsing or acting on descriptor metadata.
The descriptor cannot contain the hash of its own detached signature without a
circular identity. Its publication evidence is therefore the canonical
descriptor bytes plus canonical detached-signature asset, authenticated against
the separately installed policy/keyring/checkpoint. Production publication
evidence and trust anchors are deliberately unconfigured while this channel is
inactive.

## Installed bootstrap policy and checkpoint

`userspace-lock-bootstrap-policy-v1.schema.json` is the closed stable-policy
contract installed with a consumer binary or another independently
authenticated configuration channel. It fixes the policy ID/version, exact
keyring basename and SHA-256, one uppercase primary signing fingerprint,
`openpgp-detached-v1`, hash algorithm identifiers `[8,9,10]`, canonical
discovery/signature basenames, a canonical HTTPS origin, a lowercase
path-confined discovery location, an immutable release namespace and tag
prefix, supported schema-version lists, and mandatory monotonic-high-water,
immediate-predecessor, and bounded authenticated-lineage rules. URL userinfo,
ports, query/fragment state, path traversal, ambiguous separators, and mutable
segments such as `latest`, `main`, `HEAD`, or branch refs are rejected. Redirects
are disabled: consumers derive discovery and immutable-asset URLs only from the
pinned origin, path namespace, release tag, and canonical asset filename.
Unicode/IDNA/punycode hosts, IP literals, percent encoding, and path segments
with portable-filesystem ambiguity (leading/trailing dots or Windows device
names) are also rejected rather than normalized differently by consumers.

`userspace-lock-bootstrap-checkpoint-v1.schema.json` is a separate closed
document binding the SHA-256 of those exact canonical policy bytes to
`minimumSequence` and `minimumManifestSha256`. It is stable installed trust
configuration, not content selected by discovery. Active, last-known-good, and
monotonic high-water identities are separate consumer-local data-generation
state. Routine lock generations can advance only through the pinned authority,
compatibility, checkpoint, and predecessor rules; they cannot broaden trust,
add an algorithm, change an endpoint, or lower the checkpoint/high-water.

`lib/userspace_lock_bootstrap_contract.py` is the semantic validator and
`lib/generate_userspace_lock_bootstrap_fixtures.py` emits the bounded
deterministic cross-consumer matrix. Its URLs and hashes are test-only values
under the reserved `.invalid` namespace. The repository deliberately ships no
actual policy file, keyring, checkpoint, endpoint, or production activation.
Policy/keyring/signer rotation and checkpoint advancement require one atomic,
independently authenticated binary or configuration update that installs a new
canonical policy, matching keyring, and matching checkpoint. Schema 1 defines
no in-band rotation or checkpoint-replacement message. Discovery and generation
assets can neither request such a transition nor authorize an older policy.
Consumers retain their monotonic high-water state across an authorized policy
update; emergency downgrade and state-loss recovery remain separately reviewed
future contracts.

## Deterministic immutable request plan

`userspace-lock-generation-request-plan-v1.schema.json` and
`lib/userspace_lock_request_plan.py` define the transport-neutral request plan.
The planner accepts only the immutable in-process capability returned by
`lib/userspace_lock_verifier_evidence.py`. That helper invokes a trusted
detached-signature verifier over the exact canonical discovery and manifest,
their exact signatures, and the policy-bound keyring; it then validates the
bounded exit/status result and OpenPGP primary/subkey semantics. The capability
binds the policy, keyring, signer, signed-payload and signature hashes/sizes,
and accepted hash-algorithm IDs. It cannot be constructed by parsing JSON.

`userspace-lock-verifier-evidence-v1.schema.json` describes a bounded audit
record exposed by the capability. The audit record is useful for diagnostics
and cross-consumer fixtures, but is deliberately not proof or authorization:
neither a caller-provided `status=authenticated` string nor a structurally valid
record may be fed back into the planner. The verifier invocation and capability
must remain within one trusted consumer process.

Core then derives, rather than accepts, the discovery and discovery-signature
locations and the immutable release root. The plan contains four metadata
requests followed by every manifest payload in manifest order. Each request
fixes its role, portable filename, exact HTTPS URL, size, and SHA-256. The plan
also fixes the policy hash, release tag and sequence, origin, request count,
aggregate expected bytes, bounded URL metadata, and `redirects=false`.
`requestCount` is exactly four plus the manifest file count;
`aggregateExpectedBytes` is the sum of every request's `expectedSize`; and
`aggregateMetadataBytes` is the sum of the ASCII byte lengths of `filename`,
`path`, and `url` across every request. No consumer may reinterpret these as
character counts, payload-only totals, or post-redirect locations.
Traversal, percent encoding, queries/fragments, mutable refs, alternate origins,
case collisions, missing/extra/duplicate requests, unauthenticated documents,
and any caller-edited URL fail exact recomputation. This contract performs no
network access, follows no redirects, carries no credentials, and does not
configure a production endpoint or signer.

`lib/generate_userspace_lock_request_plan_fixtures.py` emits the deterministic
cross-consumer acceptance matrix. Its payloads, signatures, hashes, and
`.invalid` origin are synthetic test data.

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

Manifest file records describe immutable data and intentionally have no mode
field. Consumers normalize every stored regular payload/document to mode `0400`
and every immutable generation/payload directory to mode `0500`; executable
payloads are forbidden. Source transport modes are neither trusted nor identity.
`MAX_GENERATION_BYTES` is the sum of `files[].size` only. A consumer admitting a
complete cache generation uses the larger logical envelope
`MAX_GENERATION_STORAGE_BYTES`: payload plus maximum discovery, manifest, two
detached signatures, and bounded local trust record. Filesystem allocation,
directory metadata, temporary staging, and the safety reserve remain additional
consumer-local admission requirements rather than signed payload identity.

## Acceptance and activation invariants

Both consumers apply the same semantic order:

1. Snapshot and authenticate the discovery descriptor with the installed trust
   root before parsing identities.
2. Require a supported closed schema, exact authority and compatibility, and a
   sequence above the durable high-water mark. A fresh consumer with high-water
   zero and no active generation requires a locally installed bootstrap
   checkpoint containing a minimum sequence and exact manifest hash. It may
   activate that exact signed manifest or traverse forward from it. Every
   existing consumer starts from its active authenticated manifest.
3. Select an exact target without fallback or approximation.
4. Download only the named manifest/signature and selected manifest-owned files.
5. Reauthenticate the manifest and verify every file's name, role, size, hash,
   package signature, lock membership, and target identity.
6. Stage privately, fsync, publish an immutable local generation, then atomically
   switch the consumer-local active marker while preserving previous and
   high-water state.
7. Acknowledge health only after that consumer's full validation succeeds;
   otherwise restore the independently revalidated last-known-good generation.

The predecessor always names the immediately preceding *published* generation;
sequence values themselves may have gaps. A consumer that missed published
generations obtains their immutable discovery/manifest/signature records and
authenticates a chain from its active manifest (or bootstrap checkpoint) to the
current manifest. Each record must have a strictly increasing sequence, exact
authority, a matching discovery/manifest pair, and a predecessor hash equal to
the preceding manifest. One activation evaluates at most 64 intermediate
generations. A larger gap is processed in bounded authenticated segments and
never authorizes the newest generation directly. Missing, tampered, forked, or
excessive lineage fails closed.

Consumer-local durable state records both `sequence` and `manifestSha256` for
the active and last-known-good generations, plus a separate monotonic
high-water sequence. Successful activation advances the high-water mark and
retains the prior healthy active identity as last known good. Rollback may move
the active identity back, but never lowers the high-water mark. Consequently a
rolled-back generation and every older sequence remain replay-rejected; a later
generation may be reached through its authenticated lineage without treating a
failed generation as healthy. Core defines these state transitions, while each
consumer owns its own atomic persistence and compare-and-swap implementation.

Unknown authority/schema, replay or downgrade, target mismatch, partial or
changed input, ENOSPC/inode exhaustion, cancellation, failed health validation,
or cleanup failure cannot modify the active generation or high-water mark.
Network unavailability is never permission to weaken trust or discard a valid
cached generation.

A detached signature and `publishedAt` alone do not prove freshness to a new
consumer: a mirror can replay an older correctly signed descriptor. The local
bootstrap checkpoint is therefore part of the installed trust policy, not data
supplied by discovery. No production checkpoint is configured by this inactive
contract.

Every manifest-owned filename is its cross-platform identity. Schema 1 permits
only portable ASCII basenames, rejects colons, trailing dots, path separators,
and case-insensitive Windows device names such as `CON`, `NUL`, `COM1`, and
`LPT1` (including names with extensions). Filenames are also unique under
case-insensitive comparison, and payload names cannot collide with the
generation manifest or its signature. Consumers must not create an unbound
host-specific filename mapping.

## Migration gate

This document freezes the intended field names for cross-project planning but
does not activate production updates. Core must still publish a dedicated
reviewed public keyring and policy, publisher evidence, and installed-device
networking. Legacy embedded locks and both consumers' existing paths remain
until unit, integration, cancellation, cleanup, failure-injection, power-loss,
and cross-repository equivalence tests pass. The implemented inactive surfaces
exercise these contracts; this design does not activate them for production.

## Inactive installed-device implementation

`bootstrap/generationctl.sh` dispatches to the Core-owned
`lib/device_generation_lifecycle.py`. It stores reviewed lock generations under
the device-local `/var/lib/opemos/userspace-lock-generations` by default; this
is not the desktop binary-update store and is never shared with OPEMOS.EXE.

The inactive implementation supports `activate`, `activate-downloaded`,
`status`, `check`, `acknowledge-health`, `rollback`, and `prune`. Activation
accepts a locally staged closed generation plus optional authenticated lineage, verifies both
detached discovery/manifest signatures with `gpgv`, applies the schema and Core
activation policy, verifies the exact manifest-owned payload, publishes one
immutable generation create-only, and atomically updates durable state.
Canonical signed discovery and sequence-specific manifest filenames are
preserved. Each intermediate lineage input is a closed document-only directory
containing those two documents and detached signatures, so a device that missed
generations does not need their obsolete payloads.
`activate-downloaded --manifest-sha256 <sha256>` is the explicit bridge from
the separate device download cache into this same transaction. It accepts no
caller-selected path: while holding the lifecycle lock it resolves only that
closed cache identity, revalidates all immutable bytes and metadata,
reauthenticates both signatures against the currently installed policy, and
reruns target, lineage, checkpoint, replay, and high-water authorization before
publishing into the activation cache. Optional cached lineage is named only by
repeated `--lineage-manifest-sha256` identities. Downloaded bytes remain
separate and immutable. Core binds both the download-cache directory and the
selected generation's device, inode, ownership, mode, and directory metadata
across copy and publication; a replacement or mutation aborts and reconciles
the pending transaction. Cancellation, insufficient space, or a crash cannot
make them active, and locked restart reconciliation removes an uncommitted
activation while retaining the authenticated download for a safe retry.
Durable selection uses two alternating revisioned state markers whose embedded
state hashes are verified before choosing the highest revision. Legacy
`state.json` is migrated after the first successful marker commit. Locked
restart reconciliation removes only bounded stale temporary markers and rejects
a cache sequence newer than the selected durable high-water; it never guesses
that such an ambiguous generation was or was not activated.
Before staging, marker-first retention removes only unprotected generations and
preserves active/pending-active and LKG. Admission then requires the exact
generation's logical bytes plus a fixed cache reserve and, on filesystems with
finite inode accounting, its bounded node count plus an inode reserve. Btrfs's
explicit dynamic-inode report is treated as not applicable rather than as zero
free inodes. Cleanup is descriptor-relative, fixed-depth, and rejects links,
hardlinks, special files, excessive nodes, and excessive logical bytes.
Unacknowledged active data blocks another activation. Health acknowledgement
requires a canonical root-controlled evidence document bound to the exact
active sequence and manifest hash, generation integrity, and recovery
readiness. Rollback reauthenticates last known good and never lowers high-water
state. Retention preserves active and last known good plus bounded recent
generations.

Production defaults require root-owned policy, keyring, and checkpoint files
under `/etc/opemos`; none is shipped yet. Therefore `update` and
`update-or-repair` fail with `device_generation_network_inactive`. The current
local activation surface exists for contract and lifecycle testing only. It
does not enable device networking, replace the legacy embedded lock, or reuse
the desktop binary updater.

`lib/device_generation_contract.py` is authoritative for the closed lifecycle
result, durable-state, and health-evidence semantics.
`lib/generate_device_generation_fixtures.py` publishes deterministic accepted
and rejected cases for empty, pending, healthy, rolled-back, malformed,
duplicate, excessive, and cross-record identity states. Consumers should match
the stable expected acceptance decision without freezing human messages.
