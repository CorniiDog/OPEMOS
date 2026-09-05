Below is the consolidated project checklist based on our work so far. I’m treating the current repository state and the latest tests as authoritative where they supersede earlier failures.

# OPEMOS — Master Checklist

## Not yet resolved

This is the active index. New unchecked work belongs here first so maintainers
and agents do not need to scan the completed historical checklist. When an item
is completed, mark its detailed checklist entry below and remove it from this
index in the same commit.

### Immediate image-builder blockers

* [ ] Complete OPEMOS.EXE equivalence against the deterministic development-
  only userspace generation and Core's appliance consumer, then remove the
  builder's duplicated package-selection/lock translation only after its
  cross-repository tests prove identical fail-closed outcomes.
* [ ] Obtain maintainer decisions for the production generation signing key,
  canonical HTTPS channel, initial checkpoint, immutable release namespace,
  publication credentials/policy, rotation/revocation and state-loss recovery.
  Until then, production generation discovery and appliance consumption remain
  disabled; the synthetic development generation is not installable payload.
* [x] Define and fixture the minimal Core source-intent authorization contract
  for Automatic, exact published, reviewed exact-target build, reviewed project
  source and explicit upstream-development requests so OPEMOS.EXE can remove
  its remaining source-selection policy without guessing or fallback.

* [ ] Hardware-test `gaming-no-cuda-v1` and its complete-payload restoration on
  the exact SteamOS 3.8.14 / 575.64.05 / valve24.4 target before promotion
  beyond development trust.
* [ ] Exercise the deterministic `.ko` to `.ko.zst` repacker against a real
  authenticated published release, inspect the dry-run plan, then publish a
  create-only `-modules-zstd-r1` revision without changing the source release.

* [ ] Run `btrfs-zstd3` through the complete validator against a freshly mounted
  recovery overlay and confirm its structured storage result reproduces the
  standalone real-payload measurement within the documented safety policy.
* [ ] Run the implemented fail-closed compression mutation against a disposable
  recovery overlay and independently verify package records, files, modules,
  initramfs, final Btrfs state, rollback, and repeat execution. Synthetic
  activation/restoration, success, insufficient-space, cancellation, cleanup,
  and repeat-run coverage now passes. Never delete AMD/Mesa content, resize
  partitions, or credit archive estimates.
* [ ] After repinning this support commit, make the image builder deserialize
  and independently compare `provenanceSha256`, `userspaceLock`, compression
  authorization, and every extended package record field.
* [ ] Rerun the real Fedora/recovery-overlay mutation suite and verify ownership,
  repeat execution, rollback, initramfs output, Btrfs policy restoration, and
  mount cleanup before accepting an exported image.
* [ ] Independently validate recovery-image propagation through Valve's
  `repair_device.sh`, A/B slot behavior, hardware boot, and rollback before
  promoting `nvidia-mutation-valid` to `install-ready`.
  The support installer now commits an exact hash-bound receipt inside rootfs,
  separate from recovery `var-A`, so the image builder can require the same
  `receiptId` on the installed disk before independent payload verification.

### Release and hardware gates

* [ ] Complete a clean-stock SteamOS one-command certified installation.
* [ ] Re-test the 575 production release and its second-run idempotent path.
* [ ] Test uninstall/reinstall plus SteamOS kernel update and A/B rollback.
* [ ] Define Secure Boot/module-signing behavior before claiming support.

### Remaining engineering cleanup

* [ ] Make `lib/resolve_target.py` the sole release-compatibility policy
  implementation in OPEMOS.EXE. The support-owned versioned resolver schema and
  fixtures are published; the builder must now invoke and validate that result
  instead of independently parsing tags, selecting same-series fallbacks, or
  constructing canonical asset names.
* [ ] Make reviewed OPEMOS userspace locks the sole normal-build package source.
  OPEMOS.EXE must download only the exact filenames returned by the support
  contract; remove its production dynamic Arch newest-package and dependency
  selection after the locked path is fully integrated. Keep dependency
  discovery only in the support-owned maintainer audit workflow.
* [x] Publish the schema-1 reviewed userspace-lock data-generation contract:
  dedicated trust root, signed stable discovery descriptor, immutable signed
  generation manifest, deterministic compatibility fixtures, create-only
  publication evidence, replay/downgrade policy, and exact-target lock records.
  Routine same-schema/same-authority generations must be consumable without an
  OPEMOS.EXE, Core/CLI, or SteamOS image rebuild.
* [x] Implement the inactive installed-device consumer for reviewed lock generations.
  Core/CLI must independently discover, authenticate, download, validate,
  cache, atomically activate, health-acknowledge, retain, repair with, and roll
  back the same immutable identities consumed by OPEMOS.EXE. Keep its device
  cache and activation state separate from the host cache and preserve the
  last-known-good generation on every acquisition or activation failure.
  The inactive device implementation now supports an explicit
  manifest-hash-only promotion from its separate authenticated download cache;
  promotion reauthenticates and revalidates the generation under the lifecycle
  lock before entering the existing pending-health/LKG transaction. Production
  discovery, networking, signing authority, and bootstrap freshness remain
  intentionally unconfigured. Health acknowledgement and rollback now
  reauthenticate the selected cache generation against the complete current
  authority and reject policy rotation before changing LKG/active state.
* [x] Publish a deterministic OPEMOS installer-bundle manifest generator bound
  to an immutable support Git commit and every required path, role, mode, size,
  and SHA-256. It reads committed blobs rather than the mutable worktree,
  produces create-only canonical output, and validates the complete closed
  inventory.
* [x] Add a separate create-only immutable Core-bundle publisher with canonical
  tag, title, notes, manifest digest, dry-run plan, GitHub permission checks,
  existing-release refusal, and fail-closed origin/repository identity. It does
  not modify NVIDIA artifact releases; alternate destinations require the
  explicit development-only override.
* [ ] Replace OPEMOS.EXE's manually duplicated support-file inventory with a
  pin to the exact support commit plus canonical manifest hash, while retaining
  complete download verification, path confinement, and executable-mode checks
  in the builder.
* [x] Publish additive canonical schemas and fixtures for resolver schema 2 and
  installer-progress schema 1, including documented cross-record monotonicity.
* [x] Bind resolver exact-target build fallback to a reviewed, bundle-owned
  build-plan policy carrying the NVIDIA version, source repository/ref/commit,
  and known-good baseline hashes. Unreviewed targets and malformed/incomplete/
  duplicate publications expose no build authorization.
* [x] Publish a bounded strict-JSON resolver schema-2 compatibility corpus for
  malformed targets/publications, incomplete or duplicate assets, unreviewed
  exact targets, and the reviewed exact-target build action. Include it in the
  canonical Core bundle and execute every case in Core consumer tests.
* [x] Publish a deterministic bounded installer-result schema-1 compatibility
  matrix covering validation and mutation success, mandatory proof omissions,
  target/input identity failures, cleanup failure, hostile JSON, additive
  fields, and accepted bounded module/userspace failure diagnostics without
  success-only sibling proofs. Include its generator in the Core bundle and
  run every case through the authoritative consumer validator.
* [x] Publish a deterministic bounded installer-progress schema-1 stream
  matrix covering heartbeats, monotonic byte/item counters, legal phase and
  attempt resets, regressions, additive fields/phases, hostile JSON, and line/
  stream limits. Keep host operation/session binding and all visual progress
  presentation in OPEMOS.EXE.
* [x] Publish the complete additive installer-validation schema-1 proof and a
  deterministic bounded fixture matrix. Preserve direct/authenticated-bundle
  source identity through the terminal result and enforce closed records for
  cryptographic identities, packages, dependencies, modules, and reviewed
  gaming payloads.
* [x] Publish the mandatory module-verification schema-1 record and its bounded
  deterministic fixture matrix. Require the exact five normalized `.ko.zst`
  destinations, root-owned mode 0644, decompression proof, and payload-hash
  binding to the authenticated validation record; retain bounded failed
  mismatch records without accepting them as success proofs.
* [x] Publish the mandatory userspace-verification schema-1 record and its
  bounded deterministic fixture matrix. Bind package filenames, versions,
  hashes, dependency/provider arrays, reviewed lock/provenance identities,
  pacman consistency, payload confinement/hash/mode/ownership/link proofs, and
  exact-version GSP firmware to authenticated validation.
* [x] Define a bounded adapter from installer `STEAMOS_NVIDIA_PROGRESS` records
  to shared consumer-ready semantics. The 2026-09-04 ownership decision assigns
  the reusable canonical adapter to Core while frontends retain rendering,
  interaction, labels, accessibility, and platform presentation. Schema 1 emits
  integer-millionth current/overall progress for known phases and safely makes
  overall progress indeterminate for valid future phases. Focused malformed,
  duplicate/non-finite, unknown-schema, future-phase, heartbeat, invalid-range,
  and boundary-fraction tests passed through `heavy.sh`. See
  `docs/progress-semantics-handoff.md`; unrelated cleanup approval remains open.
  The adapter executable and output schema are now part of Core's immutable
  consumer-bundle inventory and the focused suite is wired into `tests/check.sh`.
  Adapter plus canonical bundle-contract tests passed through `heavy.sh` on
  2026-09-04.
  Terminal-result adaptation now reuses the complete authoritative validator
  and emits normalized succeeded/validated/failed/cancelled state, phase,
  reason, trust, and cleanup completeness. All accepted result-matrix cases and
  bundle-contract tests passed through `heavy.sh`; malformed and unsupported
  results produce no semantic output.
  A deterministic cross-consumer corpus now freezes known start/completion,
  indeterminate heartbeat, additive future-phase, fractional-rounding, and
  maximum-counter outputs. Its generator, fixture, adapter, and schema are in
  the immutable bundle; focused adapter and bundle tests passed via `heavy.sh`.
  A follow-up emitter audit corrected the known-phase table to every current
  Core validation/mutation phase. Exact closed-output and recursive separation
  assertions cover known/future/indeterminate progress and all normalized
  terminal states; schemas and outputs contain no frontend layout, widget,
  focus/window, animation, toolkit, rendering, interaction, accessibility, or
  platform behavior. Focused adapter and bundle tests passed via `heavy.sh`.
  A deterministic terminal-semantics corpus now links every accepted
  authoritative installer-result case to exact output and covers succeeded,
  validation-only, diagnostic failure, cancellation, and additive source
  fields. It exposed and fixed the prior corpus omission of accepted
  cancellation. Focused adapter and bundle tests passed through `heavy.sh`.
  Executable-boundary tests now require byte-exact canonical stdout for valid
  future progress and cancelled terminal results, and nonzero/empty stdout for
  duplicate-key, malformed, unsupported-schema, and oversized records. Focused
  adapter and bundle tests passed through `heavy.sh`.

* [ ] Hardware-validate the implemented no-input DRM/KMS boot interstitial on
  real SteamOS with simpledrm and intended iGPU paths, internal/external display
  handoff, hotplug, renderer crash/SIGKILL, power loss, watchdog expiry, and
  successful continuation into both the preselected Gaming and Desktop Mode.
  Portable model/rasterizer/progress/install tests and the Fedora build/KMS
  harness are implemented; normal-user delivery remains disabled until the
  exact Linux binary is bound to a reviewed signed release.

* [ ] Design and implement a fail-closed self-update contract for the native
  SteamOS companion using versioned A/B application generations. Download into
  private staging, authenticate an immutable release manifest and every file,
  enforce target architecture/version policy, fsync the complete generation,
  and atomically switch a `current` launcher pointer without overwriting the
  running executable. Retain one independently validated last-known-good
  generation and automatically roll back unless the new process records a
  bounded startup health acknowledgement. Add exclusive update locking,
  interrupted download, ENOSPC, signature/hash mismatch, crash-before-switch,
  crash-after-switch, power-loss simulation, rollback-loop prevention, and
  safe generation-retention tests. An unavailable or invalid update must leave
  the active generation untouched and must never weaken the boot guardian.
  The authenticated local generation manager, durable activation markers,
  rendered-health acknowledgement, bounded launcher, and crash-window rollback
  tests are implemented. Canonical manifest production, reviewed public-key
  policy onboarding, immutable input snapshots, deterministic dry-run plans,
  create-only publication, and cancellation cleanup are also implemented.
  Remaining work is maintainer creation and independent review of a production
  signing key, immutable network/release acquisition, installer delivery,
  retention limits, and real SteamOS power-loss testing.
* [ ] Enable **Settings → Pages → Build and deployment → GitHub Actions** in
  the canonical repository, then verify the first documentation deployment and
  published navigation links.
* [ ] Finish online-installer failure, signal, readonly-restoration, userspace
  rollback, and raw-`.ko`/`.ko.zst` idempotency coverage.
  * [x] Cover support-repository clone and pinned-revision fetch failures before
    Core trust loading. Both preserve the underlying nonzero status, clean
    partial cache-rooted `online-install.*` trees even with spaces in `HOME`,
    and stop before privileged or installation work. The isolated test passed
    through `heavy.sh` on 2026-09-04 and is included in `tests/check.sh`.
  * [x] Extend that pre-trust matrix through detached checkout failure after a
    successful clone and fetch. It preserves the checkout status, removes the
    partial cache tree, and never reaches Core trust or privileged work; the
    focused matrix passed through `heavy.sh` on 2026-09-04.
  * [x] Reject malformed pinned support revisions before cache creation or Git
    execution. Focused cases cover short, long, non-hex, and embedded-whitespace
    identities and prove the stable error plus absence of network/privileged
    setup; the full bootstrap failure matrix passed through `heavy.sh`.
  * [x] Hermetically remove each declared prerequisite (`git`, `curl`, `tar`,
    `sha256sum`, `zstd`, `modinfo`, `realpath`, and `python3`) in turn. Every
    case emits only the exact missing-command error and exits before HOME/cache
    creation or any external/network/privileged execution. The full focused
    bootstrap matrix passed through `heavy.sh` on 2026-09-04.
  * [x] Commit `e1bc00a4331d7caaffee71c27a519d7ae5f3919e`: exercise a real TERM during the fake-root installer's
    blocked initramfs phase. The process group exits 143, restores the prior
    module/state trees byte-for-byte, re-enables SteamOS read-only mode, removes
    temporary stages, and leaves real system modules unchanged. The complete
    `tests/transaction.sh` suite passed through `heavy.sh` on 2026-09-04.
  * [x] Make SteamOS read-only restoration fail closed before install/uninstall
    completion. Injected first-attempt `steamos-readonly enable` failures now
    produce nonzero results, roll back target and state bytes, retry restoration
    during cleanup, remove temporary stages, and leave real modules unchanged.
    The complete transaction suite passed through `heavy.sh` on 2026-09-04.
  * [x] Consolidate the online installer and non-sudo baseline onto Core's
    shared decompressed-module content hash helper. Focused tests passed through
    `heavy.sh` for raw `.ko` / equivalent `.ko.zst` equality, corrupt Zstd
    rejection, and unsupported suffix rejection. The broader `tests/check.sh`
    stopped earlier on an existing restrictive-umask desktop-update fixture, so
    no full-suite pass is claimed or repeated.
    The restrictive-umask blocker was subsequently fixed in Core's desktop
    generation writer: exact modes are applied after creation, and mode-setting
    failures remove partial create-only files. Its focused suite passes for
    umasks 0077/0027/0000, injected failure, cleanup, and retry. A fresh broader
    check passed this stage and later stopped independently in
    `device_generation_lifecycle.py` with `generation tree is too deep`; no
    complete full-suite pass is claimed.
* [ ] Resolve build caching, compiler/certification policy, reproducibility,
  backup retention, and safe Podman/bootstrap ownership decisions.
* [ ] Audit fresh-machine prerequisites, duplicated environment setup, stale
  terminology, unused variables, and intended remote/raw invocation paths.
  * [x] Document the exact pre-mutation Core CLI command prerequisites, safe
    missing-tool behavior, development-only rootless Podman requirement, and
    the separation from EXE host dependencies. `tests/documentation.py` now
    freezes this list and boundary wording; it passed through `heavy.sh` on
    2026-09-04. The same validation exposed and fixed missing required front
    matter on the preserved boundary-decision page.
  * [x] Correct the prerequisite contract to distinguish executable names from
    SteamOS package providers: the frozen command list now exactly names
    `modinfo`, `python3`, `realpath`, and `sha256sum`, while prose maps them to
    `kmod`, `python`, and GNU coreutils. Focused documentation validation passed
    through `heavy.sh` on 2026-09-04.

### Newly identified security gates

* [ ] Snapshot every authenticated installer input, add an exclusive per-target
  lifecycle lock, and preserve exact rootfs/EFI mount identity through cleanup.
* [ ] Require structured module, userspace, initramfs, and Holo-database
  verification before any schema-1 installer success result.
* [ ] Formalize result/progress compatibility, opaque-operation liveness, and a
  real phase-by-phase failure/cancellation matrix.
* [ ] Authenticate target-owned executable code and raise the public online
  bootstrap/release/certification path to the offline installer's trust level.
* [ ] Define authenticated archival/cache recovery and typed outage behavior for
  GitHub, Valve, and Arch Linux Archive dependencies.

The detailed unchecked items and their historical context remain below. Search
for `* [ ]` to enumerate them mechanically.


## Boundary decisions

* [x] 2026-09-04 user-authorized creator-owned artifact cleanup boundary:
  canonical SHA-256 `136d3572effa90c1b84bcf51002d7f9641c367132de20d54dd7173f68f13c6a8`,
  Git blob `68fd9553bb8fee79cee803a38f980a94b2d80e57`; focused local integrity
  assertions updated. EXE mirror commit and synchronized counterpart pin remain
  pending. See `docs/boundary-decision-2026-09-04.md`.
  Synchronization completed after that staged state: EXE mirrored the exact
  bytes at `064d1d54c7ef2eda3d56e80c67e9f8e78a554725`, and Core repinned the default integrity check to that
  commit. Default cross-repository and focused local checks pass; the preceding
  pin and staged validation remain recorded in the dated decision.

## Experimental Linux Core conformance evidence

* [x] Verify Core contract matrices on Ubuntu 24.04.4 LTS (2026-09-04):
  `boundary_policy`, `consumer_contracts`, `source_intent_contract`, and
  `appliance_generation_consumer` passed through the shared heavy wrapper.
  Fixed manifest creation under umask 0077 by explicitly setting required 0644
  permissions and transferring descriptor ownership before fallible writes.
  Regression cases cover umasks 0077/0027/0000, injected chmod failure,
  partial-file cleanup, and successful retry. Corrected the unsafe appliance
  output-directory fixture to explicitly retain 0755 under restrictive umasks.
  Production trust and governance are unchanged. EXE consumer equivalence,
  Debian/macOS execution, and hardware certification remain separate gates.
  See `docs/image-builder.md#experimental-ubuntudebian-consumer-checks`.

## Current project phase

**Status: development / active dogfooding**

The support infrastructure is now mature enough to use for real NVIDIA
development work on the primary SteamOS test system. Broad installer construction
is no longer the main task. The project should now be exercised through the same
public workflows intended for future users while the remaining validation gates
are completed.

### Milestone ladder

* [x] Development infrastructure is usable for active dogfooding.
* **Alpha** requires a clean-stock SteamOS one-command certified install.
* **Beta** requires install, idempotency, uninstall/reinstall, SteamOS update,
  and rollback verification.
* **Release candidate** requires repeated clean installs and additional NVIDIA
  hardware coverage.
* **Stable** requires reliable SteamOS upgrade/recovery behavior without manual
  shell repair during supported workflows.

### Current priority

The `Not yet resolved` index above is the single authoritative priority queue.
The detailed sections below retain implementation history and acceptance
criteria without duplicating that queue.

---

## 1. Core project architecture

### Core and frontend dependency boundary

Authority: [`BOUNDARIES.md`](BOUNDARIES.md). This checklist is an
implementation summary and must not redefine that read-only governance file.

* [x] Treat OPEMOS Core as the one lower-level policy and contract provider:
  exact-target resolution, trust, userspace locks, installation, verification,
  recovery state, progress records, and structured results belong here.
* [x] Treat the OPEMOS CLI, native SteamOS desktop companion, and no-input
  SteamOS DRM/KMS interstitial as sibling clients of OPEMOS Core. Packaging
  these clients in one support bundle is aggregation, not a dependency from the
  policy layer back into a frontend; the OPEMOS repository still owns their
  implementation, packaging, and conformance tests.
* [x] Treat OPEMOS.EXE as a separate host frontend and image orchestrator that
  consumes one exact authenticated OPEMOS bundle. OPEMOS must never import,
  invoke, build against, or require OPEMOS.EXE at runtime. The current host is
  macOS/Apple Silicon, but ownership remains cross-platform.
* [x] Keep presentation with the client that runs it: OPEMOS.EXE owns macOS
  menus, dialogs, image-build loading UI, cancellation, and export workflows;
  the desktop companion owns interactive SteamOS status/recovery UI; the
  interstitial owns temporary no-input SteamOS boot/update presentation; the
  CLI owns terminal presentation.
* [x] Share bounded contracts, phase identities, status semantics, design
  tokens, and canonical branding assets without sharing platform event loops,
  Tauri/DOM code, DRM/KMS code, macOS APIs, or executable dependencies between
  frontends.
* [x] Enforce a directed dependency graph with no frontend-to-frontend edge:
  `OPEMOS Core <- CLI`, `OPEMOS Core <- Desktop`,
  `OPEMOS Core <- Interstitial`, and `OPEMOS Core <- OPEMOS.EXE`.
* [x] For a live SteamOS workflow, keep the client thin and invoke a
  support-owned live-install/recovery contract. Any generally reusable logic
  currently embedded in OPEMOS.EXE must move down into OPEMOS Core; macOS VM,
  image, `diskutil`, Finder, USB, and Tauri behavior remains in OPEMOS.EXE.

#### Exact execution and agent boundary

* [x] OPEMOS Core names what may be downloaded and proves whether the bytes are
  trusted. OPEMOS.EXE owns the host network request, cache location, transfer
  transport, retry UX, and independently pinned manifest digest. A successful
  download is never equivalent to successful Core authentication.
* [x] Separate host networking, appliance networking, and installed-device
  networking. Installation appliances default to staged offline inputs; only
  an authorized exact-target build contract may request bounded egress.
* [x] Treat user-requested source mode as intent supplied by OPEMOS.EXE, while
  Core alone authorizes the exact source action or fails closed. Neither side
  may silently substitute a different branch, commit, or mode. `Automatic` is
  explicit intent to use only Core's reviewed production selection policy; it
  does not authorize a development source or approximation.
* [x] Keep recovery-image A/B discovery, pairing, and overlay mounts in
  OPEMOS.EXE; SteamOS owns base OS slot transitions; and Core owns NVIDIA
  guardian, receipt, repair, verification, and payload rollback afterward.
* [x] OPEMOS Core owns rollback inside a mounted target transaction: packages,
  modules, GRUB, initramfs, receipts, temporary mounts, and target state.
  OPEMOS.EXE owns the outer rollback boundary: disposable overlay retention or
  discard, VM shutdown, source-image preservation, export, and USB recovery.
* [x] Distinguish the interactive installation-media welcome application and
  guarded disk-selection bridge, which belong to OPEMOS.EXE, from the
  fullscreen no-input DRM/KMS boot/recovery/update UI, which belongs entirely
  to OPEMOS Core.
* [x] OPEMOS Core defines progress phase identities, counters, terminal states,
  and validation rules. OPEMOS.EXE assigns macOS labels, weights, layout,
  animation, accessibility, and controls. It must preserve indeterminate Core
  phases rather than inventing completion percentages.
* [x] Declare the sole UI exception: OPEMOS Core owns and implements the
  fullscreen no-input SteamOS UI. OPEMOS.EXE consumes it only as an
  authenticated OPEMOS-owned target payload, may deploy it and stage bounded
  Core state, and must not fork, import, link, or execute it in the host
  runtime. A Core-owned installed-device supervisor—not another frontend—may
  launch and monitor it after deployment.
* [x] The support-side agent changes Core policy, schemas, publishers, build and
  installer entry points, target-side clients, and Core tests in `OPEMOS`.
  It returns immutable commits and changed contracts; it does not edit or pin
  the macOS application.
* [x] The builder-side agent changes host acquisition, Core consumers, Tauri
  presentation, appliance/overlay orchestration, export, and independent image
  verification in `OPEMOS.EXE`. It must not recreate compatibility, signer,
  dependency, installation, or target-verification policy in Rust/JavaScript.
* [x] Test ownership follows execution ownership. Core runs contract, archive,
  Fedora build/transaction, target mutation, and device-client tests.
  OPEMOS.EXE runs macOS UI, download/transfer, VM lifecycle, overlay, export,
  USB, and independent final-image tests. Maintainers own real SteamOS/NVIDIA
  hardware certification and cross-repository release approval.

* [x] Keep the support/build/install tooling in:

  * `CorniiDog/OPEMOS`
* [x] Keep NVIDIA source history/patch branches in:

  * `CorniiDog/open-gpu-kernel-modules-steamos`
* [x] Use NVIDIA upstream as the pristine source baseline:

  * `NVIDIA/open-gpu-kernel-modules`
* [x] Maintain the known-good 575 source branch:

  * `nvidia/575.64.05`
* [x] Preserve the known-good SteamOS 3.8.16 / NVIDIA 575.64.05 release.
* [x] Separate production/certified behavior from development behavior.
* [x] Separate pristine-upstream testing from patched-project testing.
* [x] Document that architecture clearly in the README.
* [x] Document which repository owns:

  * source history,
  * patches,
  * builds,
  * releases,
  * userspace setup,
  * module installation.

---

# 2. Known-good 575.64.05 baseline

* [x] Build NVIDIA open kernel modules for SteamOS 3.8.16.
* [x] Build against:

  * `6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45`
* [x] Produce all five NVIDIA modules:

  * `nvidia.ko`
  * `nvidia-drm.ko`
  * `nvidia-modeset.ko`
  * `nvidia-peermem.ko`
  * `nvidia-uvm.ko`
* [x] Verify exact kernel vermagic.
* [x] Verify NVIDIA 575.64.05 runtime.
* [x] Verify `/proc/driver/nvidia/version`.
* [x] Verify `modinfo` points to project-installed modules.
* [x] Verify Xwayland and Gaming Mode actually use the RTX 2060.
* [x] Verify Gaming Mode works with the project modules.
* [x] Verify no NVIDIA Xid failures in that working state.
* [x] Publish known-good 575 release:

  * `steamos-3.8.16-nvidia-575.64.05-k6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45`
* [x] Preserve backward compatibility with that release format.
The active production-release gate includes re-testing after installer changes
and verifying the old 575 release's idempotent path.
* [x] Verify the installer accepts and normalizes the old raw-`.ko` 575 release format.

---

# 3. NVIDIA 580.119.02 pristine-upstream baseline

## Resolution

* [x] Implement explicit pristine-upstream development mode.
* [x] Resolve:

  * `--use-upstream 580`
  * to `580.119.02`
* [x] Resolve matching NVIDIA userspace packages:

  * `nvidia-utils-580.119.02-1-x86_64.pkg.tar.zst`
  * `lib32-nvidia-utils-580.119.02-1-x86_64.pkg.tar.zst`
* [x] Display:

  * `Selection mode: upstream-development`
  * `Reference: upstream:580`

## Source/build

* [x] Fetch exact NVIDIA upstream tag.
* [x] Resolve exact upstream commit.
* [x] Checkout pristine source detached at that commit.
* [x] Verify `version.mk` matches requested NVIDIA version.
* [x] Support detached HEAD builds for pristine upstream.
* [x] Build all five 580.119.02 modules against the current Neptune kernel.
* [x] Treat objtool `naked return ... MITIGATION_RETHUNK` messages as nonfatal.
* [x] Treat missing `MODULE_DESCRIPTION` MODPOST warnings as nonfatal.
* [x] Verify exact vermagic.

## Runtime

* [x] Install pristine upstream 580.119.02 modules.
* [x] Boot successfully.
* [x] Verify:

  * `nvidia-smi` = 580.119.02
  * kernel module version = 580.119.02
  * `/proc/driver/nvidia/version` = open kernel module 580.119.02
* [x] Verify Gaming Mode is using the RTX 2060.
* [x] Verify Xwayland/Steam/Mangoapp processes use the GPU.
* [x] Verify installed path:

  * `/lib/modules/.../updates/open-gpu-kernel-modules-steamos/nvidia.ko.zst`
* [x] Verify no NVIDIA `NVRM: Xid` faults.
* [x] Distinguish the Realtek `XID 541` line from NVIDIA Xid faults.
* [x] Establish 580.119.02 pristine upstream as the control case.
* [x] Accept that graphics bugs may still exist in this control case.
* [x] Preserve a source-identifiable 580 pristine build artifact for regression testing.

---

# 4. Resolver modes

## Certified production

* [x] Normal invocation resolves published project releases.
* [x] `./bootstrap/setup_nvidia.sh --resolve-only`
* [x] Current result:

  * SteamOS 3.8.16
  * kernel exact match
  * NVIDIA 575.64.05
  * `Selection mode: certified`
* [x] Production mode remains anchored to the published known-good project release.
* [x] Use one shared certified-release selector for userspace setup and online module installation.
* [x] Prefer exact SteamOS certification.
* [x] Allow fallback only to the newest older SteamOS patch in the same major/minor series.
* [x] Require the exact running kernel during certified fallback.
* [x] Never automatically select a newer SteamOS certification.
* [x] Remove the separate online fuzzy-selection algorithm.
* [x] Retain `--fuzzy` only as a compatibility alias for the bounded certified fallback policy.
* [x] Automatically pass SteamOS compatibility to `install.sh` only when the selected certification differs from the running SteamOS patch.

## Development mode

* [x] Rename confusing `--driver` flag.
* [x] New name:

  * `--development`
* [x] `--development 580 --resolve-only` resolves 580.119.02.
* [x] Change reference from:

  * `explicit:580`
  * to `development:580`
* [x] Change selection label from:

  * `explicit`
  * to `development`
* [x] Keep development mode distinct from pristine-upstream mode.
* [x] Make runtime output more self-documenting.
* [x] Explicitly print what development mode does to kernel modules.
* [x] Explicitly print that it is intended for project/patched-module development.
* [x] Warn when `--development` changes NVIDIA userspace while the currently resolved kernel module is a different NVIDIA version.
* [x] Explicitly warn that kernel modules are not replaced by development userspace setup.

## Upstream development

* [x] `--use-upstream 580`
* [x] Resolve newest matching 580.x userspace.
* [x] Fetch pristine upstream source.
* [x] Build pristine modules.
* [x] Install pristine modules.
* [x] Mark:

  * `source_provider=upstream`
  * `project_patches=0`
* [x] Add polished `--help` descriptions for all modes.

## Mode semantics target

* [x] Normal:

  * certified project release.
* [x] `--development VERSION`:

  * development/userspace selection for project work.
* [x] `--use-upstream VERSION`:

  * pristine upstream control build.
* [x] Document all three side by side in README.

---

# 5. SteamOS root filesystem storage problem

## Discovery

* [x] Determine root filesystem is only about 5 GiB.
* [x] Determine `/var` is a separate tiny partition.
* [x] Determine `/home` has hundreds of GiB available.
* [x] Determine moving files from `/var` does **not** free `/`.
* [x] Determine `/usr/lib/modules` consumes root filesystem space.
* [x] Identify raw project NVIDIA modules were approximately 111 MiB.
* [x] Identify compressed modules are approximately 30 MiB.
* [x] Treat `df /` as authoritative for available root capacity.

## Backups

* [x] Move old installer backups from `/var` to `/home`.
* [x] Preserve old backups rather than deleting them.
* [x] Recreate empty state backup directory under `/var`.
* [x] Change new transactional backups to `$HOME/.cache/...`.
* [x] Define and implement a bounded age/count retention policy for old backup
  generations.
* [x] Make backup generation names collision-safe for sequential operations in the same second.

---

# 6. Module compression

* [x] Require `zstd` in the installer.
* [x] Compress built raw `.ko` modules before copying them to `/usr`.
* [x] Install as `.ko.zst`.
* [x] Verify SteamOS/kmod resolves compressed modules correctly.
* [x] Reduce installed module footprint from roughly 111 MiB to roughly 30 MiB.
* [x] Successfully boot compressed modules.
* [x] Verify `modinfo` resolves compressed project modules.
* [x] Audit every script for assumptions that installed modules end in `.ko`.
* [x] Test compressed release archives if future release packaging moves to `.ko.zst`.
The active canonical-repack gate will determine whether future release archives
standardize on raw `.ko` or `.ko.zst`; the installer accepts both safely.

---

# 7. Installer root-space preflight

* [x] Stage install data on `/home`.
* [x] Calculate size of new module set.
* [x] Calculate available target-filesystem bytes.
* [x] Calculate reclaimable bytes from existing target directory.
* [x] Add 64 MiB safety reserve.
* [x] Evaluate:

  * `effective = available + reclaimable`
  * `required = new + safety`
* [x] Refuse installation if effective space is insufficient.
* [x] Account for replacement of the existing project module directory.
* [x] Successfully install 580 with the new preflight logic.
* [x] Add a test for deliberately insufficient root space.
* [x] Make preflight output especially clear when replacement space is what makes installation possible.

---

# 8. Temporary storage policy

## Common helpers

* [x] Add `project_cache_root()`.
* [x] Add `project_mktemp_dir()`.
* [x] Add `project_mktemp_file()`.
* [x] Default project work storage to:

  * `$HOME/.cache/open-gpu-kernel-modules-steamos-support`
* [x] Use helpers in scripts that already source `common.sh`.

## Converted scripts

* [x] `compile.sh`
* [x] `install.sh`
* [x] `install_upstream.sh`
* [x] `setup_nvidia.sh`

## Bootstrap script issue discovered

* [x] Notice online bootstrap scripts cannot use `project_mktemp_dir` before downloading/sourcing `common.sh`.
* [x] Fix `online_install.sh` to create its bootstrap temp directory directly under `$HOME/.cache/...`.
* [x] Fix `compile_online.sh`.
* [x] Fix `online_commit.sh`.
* [x] Fix `online_dev.sh`.
* [x] Audit helper call ordering.
* [x] Verify every remaining `project_mktemp_*` call occurs after `common.sh` is sourced.

## Container `/tmp` distinction

* [x] Initially change build header download away from `/tmp`.
* [x] Discover this was incorrect because the header logic runs **inside the Fedora build container**.
* [x] Observe failure:

  * `PROJECT_NAME: unbound variable`
* [x] Determine container `/tmp` is backed by rootless Podman graph storage under `/home`.
* [x] Revert the `build.sh` header archive back to container `/tmp`.
* [x] Re-run syntax validation after revert.
* [x] Re-run temp audit and explicitly allow the container `/tmp` usage.
* [x] Add a comment explaining why container `/tmp` is intentional so it is not “fixed” again later.

## Cache ownership regression tests

* [x] Verify project temp and staging directories are owned by the invoking `deck`/user account.
* [x] Verify non-root `zstd` can create and validate output in project staging.
* [x] Scan the complete current project cache for root/foreign-owned paths that could poison later non-root runs.
* [x] Exercise that cache scan after each fake-root forced-failure install/uninstall test.
* [ ] Re-run a build, local validation, and install preflight after a sudo-backed operation to catch ownership regressions.

---

# 9. Fedora/rootless Podman build environment

* [x] Add `setup_build_env.sh`.
* [x] Detect missing native SteamOS kernel build environment.
* [x] Use Fedora container for build environment.
* [x] Install/pull required build tooling.
* [x] Verify rootless Podman GraphRoot is under `/home`.
* [x] Avoid filling the tiny SteamOS root filesystem with container layers.
* [x] Resolve SteamOS Neptune header package.
* [x] Find exact Valve header package for current kernel.
* [x] Download/extract headers inside build container.
* [x] Build NVIDIA modules against exact Neptune kernel headers.
* [x] Pin/record immutable container digest during successful build.
* [ ] Replace or tightly constrain the host-mutating
  `pacman -Sy --needed --noconfirm podman` bootstrap path.
* [ ] Cache authenticated headers and the build environment by exact identity.
* [x] Fix grep warning:

  * `grep: warning: stray \ before "`
* [x] Audit Valve repository discovery in both build paths and accept only
  bounded safe `jupiter-*` directory components from the untrusted mirror index.

---

# 10. `build.sh`

* [x] Build all five NVIDIA open kernel modules.
* [x] Verify vermagic.
* [x] Work with detached HEAD upstream source.
* [x] Use Fedora build environment when needed.
* [x] Record build-container information.
* [x] Revert header download to container `/tmp`.
* [x] Add comment explaining `/tmp` is container-local.
* [x] Add an automated detached-HEAD regression test:

  * pristine upstream records/accepts `source_branch=HEAD`,
  * an empty `git branch --show-current` result is treated as detached HEAD,
  * it must not fail with `Source branch is ; expected HEAD`.
* [ ] Define one owner for build-environment preparation and remove duplicated
  setup between callers and `build.sh`.
* [ ] Verify no host-root temp writes remain for large data.

---

# 11. `install_upstream.sh`

## Existing working behavior

* [x] Require exact NVIDIA version.
* [x] Verify installed NVIDIA userspace matches requested exact version.
* [x] Clone/fetch upstream source.
* [x] Checkout exact tag/commit detached.
* [x] Verify source version.
* [x] Build pristine modules.
* [x] Package modules and BUILD-INFO.
* [x] Call trusted `install.sh`.
* [x] Preserve/restore state file.
* [x] Move upstream work directory into `/home` cache.
* [x] Move state backup into `/home` cache.
* [x] Request sudo privileges early.

## Bugs found

* [x] Discover duplicate:

  * `setup_build_env.sh`
  * `setup_build_env.sh`
* [x] Plan/remove one duplicate call.
* [x] Notice indentation regression around `STATE_BACKUP`.
* [x] Plan/fix indentation.

## Build-only mode

* [x] Design `--build-only`.
* [x] Add `BUILD_ONLY` state.
* [x] Add argument parsing.
* [x] Make confirmation text distinguish build-only from install.
* [x] Persist build-only artifact under:

  * `~/.cache/open-gpu-kernel-modules-steamos-support/upstream-builds`
* [x] Ensure build-only exits before `install.sh`.
* [x] Print artifact/checksum paths.
* [x] Resolve the build-only blocker caused by using `${PROJECT_NAME}` inside the Fedora container.
* [x] Revert container header path.
* [x] Retry:

  * `./bootstrap/install_upstream.sh --build-only 580.119.02`
* [x] Confirm archive persists after cleanup trap.
* [x] Confirm checksum persists and validates from its persistent location.
* [x] Confirm installed modules are completely untouched by `--build-only`.
* [x] Add actual `--help` handling.
* [x] Add usage text documenting positional version and `--build-only`.
* [x] Avoid requesting sudo in build-only mode when Podman is already installed.

---

# 12. `install.sh` transactional installer

## Validation

* [x] Validate archive checksum.
* [x] Validate tar traversal safety.
* [x] Require BUILD-INFO.
* [x] Validate SteamOS version.
* [x] Validate kernel exactness.
* [x] Validate NVIDIA exactness.
* [x] Support explicit fuzzy SteamOS install where intended.
* [x] Validate module vermagic.
* [x] Validate module NVIDIA version.
* [x] Handle `/lib` versus `/usr/lib` canonicalization.
* [x] Run `depmod`.
* [x] Rebuild initramfs.
* [x] Write installation state.
* [x] Restore SteamOS read-only state.

## Storage redesign

* [x] Extract archive under `/home`.
* [x] Stage compressed modules under `/home`.
* [x] Back up existing installation under `/home`.
* [x] Avoid root-owned cache staging.
* [x] Compress modules before target replacement.
* [x] Add replacement-aware root-space preflight.
* [x] Successfully install 580 with this architecture.

## Transaction safety

* [x] Track whether target directory was touched.
* [x] Maintain rollback mechanism.
* [x] Restore read-only filesystem state on cleanup.
* [x] Audit rollback after all backup-location and compression changes.
* [x] Test a fake-root forced failure after old target removal.
* [x] Test a fake-root forced failure midway through module copy.
* [x] Verify rollback restores the previous compressed module state byte-for-byte.
* [x] Verify rollback also restores a legacy raw installed target byte-for-byte.
* [x] Verify state metadata rollback after an injected partial state write.
* [x] Verify rollback re-runs mocked `depmod` and `mkinitcpio` after failure.
* [x] Verify no orphaned stage directories remain after injected failures.
* [x] Add checksum verification after final target copy.

---

# 13. Online installer idempotency

## Old behavior

* [x] `already_installed()` existed.
* [x] Validate archive SHA.
* [x] Validate archive traversal.
* [x] Extract archive into check directory.
* [x] Compare BUILD-INFO against installed state.
* [x] Verify `modinfo -n nvidia` resolves into project target.
* [x] Hash archive modules versus installed modules.
* [x] Fast path:

  * `Already installed, healthy, and current.`
  * `Nothing to do.`
* [x] Avoid unnecessary initramfs rebuild/reboot when nothing changed.

## Compression bug

* [x] Discover old code assumed:

  * archive `*.ko`
  * installed `*.ko`
* [x] Recognize installed modules are now `*.ko.zst`.
* [x] Add `zstd` requirement.
* [x] Add `modinfo` requirement.
* [x] Add `realpath` requirement.
* [x] Add `module_content_sha256()`.
* [x] Hash raw `.ko` directly.
* [x] Decompress `.ko.zst` to stdout before hashing.
* [x] Make comparison independent of storage compression.
* [x] Allow archive side to be `.ko` or `.ko.zst`.
* [x] Allow installed side to be `.ko` or `.ko.zst`.
* [x] Normalize basename by removing `.zst`.
* [x] Prefer installed `.ko.zst`, fall back to `.ko`.
* [x] Add `checked_modules` counter.
* [x] Reject empty `modules/` directory.
* [x] Syntax-check new logic.
* [x] `git diff --check` new logic.
* [x] Build a new pristine 580 package from the exact upstream source identity.
* [x] Run `online_install.sh --local` against that rebuilt package.
* [x] Confirm the rebuilt package correctly requests repair when binary content or BUILD-INFO differs from the installed build.
* [x] Verify raw archive `.ko` versus installed `.ko.zst` hits the idempotent fast path using an exact-content fixture.
* [x] Verify no module replacement occurs on the idempotent fast path.
* [x] Verify no `mkinitcpio` on the idempotent fast path.
* [x] Verify no reboot prompt on the idempotent fast path.
* [ ] Verify a deliberately modified installed module causes repair path.
* [x] Verify altered module content causes the repair path.
* [x] Verify a deliberately altered BUILD-INFO causes repair path.
* [x] Verify an invalid checksum causes failure.
* [ ] Verify raw archive ↔ compressed installed comparison with old 575 release.
* [x] Verify compressed archive ↔ compressed installed comparison if future releases use compressed archives.
* [x] Require exactly the five expected NVIDIA modules in install and health validation.

---

# 14. Online/bootstrap entry points

* [x] Identify scripts that bootstrap before `common.sh` exists.
* [x] `online_install.sh`
* [x] `compile_online.sh`
* [x] `online_commit.sh`
* [x] `online_dev.sh`
* [x] Add a standalone pinned online entry point for `--development` and `--use-upstream` workflows.
* [x] Do not call common helper functions before downloading/cloning support repo.
* [x] Give these scripts direct cache-root temp creation.
* [x] Keep temp storage on `/home`.
* [x] Add a small comment in each explaining why it does not use `project_mktemp_dir`.
* [x] Test each entry-point help path from a working directory outside the
  repository, without pre-sourcing project helpers.
* [x] Test scripts via their intended remote/raw invocation, not only from the local checkout.

  * 2026-09-04: `tests/online_bootstrap_failures.py` pipes all five standalone online entry points into `bash -s -- --help`; the online installer also runs from stdin through exact pinned clone dispatch, verifies deterministic clone failure propagation, and removes its cache-rooted partial tree. `heavy.sh python3 tests/online_bootstrap_failures.py` passed without network access.

---

# 15. `setup_nvidia.sh`

## Resolver

* [x] Resolve certified releases.
* [x] Resolve explicit development branch/version prefix.
* [x] Resolve pristine upstream branch/version prefix.
* [x] Resolve exact `nvidia-utils`.
* [x] Resolve exact `lib32-nvidia-utils`.
* [x] Verify all three `--resolve-only` paths.

## Naming

* [x] Rename `--driver` → `--development`.
* [x] Rename `explicit:` → `development:`.
* [x] Rename selection display → `development`.
* [x] Rename remaining internal `DRIVER_SPEC` state to `DEVELOPMENT_SPEC`.
* [x] Explain mode responsibilities in help and runtime output.

## Userspace/integration

* [x] Successfully install 580.119.02 NVIDIA userspace.
* [x] Keep userspace and kernel module exact-version matching.
* [x] Configure NVIDIA DRM modeset/fbdev environment.
* [ ] Audit fresh-machine prerequisites.
* [x] Keep normal certified mode from silently falling back to upstream source.

## Mode-boundary regression tests

* [ ] Test `--development` semantics:

  * resolves a newer explicitly requested NVIDIA userspace version,
  * leaves installed kernel modules unchanged,
  * does not fetch, build, or install pristine upstream modules.
* [ ] Test `--use-upstream` semantics:

  * resolves userspace matching the selected upstream NVIDIA version,
  * fetches pristine NVIDIA source at the exact tag/commit,
  * builds and installs the upstream modules,
  * records `source_provider=upstream` and `project_patches=0`.

---

# 15A. NVIDIA userspace transaction safety

Kernel-module installation now has substantially stronger rollback behavior than
online NVIDIA userspace setup. Three gates capture the remaining asymmetry:

* [ ] Snapshot replaced NVIDIA package versions plus project-owned modprobe and
  GRUB state before an online userspace transaction.
* [ ] Define atomic rollback for partial pacman success or later configuration
  failure, including reliable SteamOS read-only restoration.
* [ ] Add fake-root downgrade, upgrade, partial-package, broken-install, and
  post-configuration failure coverage.

---

# 16. Sudo/password UX

* [x] Add early:

  * `sudo -v`
  * to upstream install workflow.
* [x] Explain that no password prompt can occur if sudo credential timestamp is already cached.
* [ ] Define one top-level privilege owner and prevent nested password prompts.
* [ ] Test cold and expired sudo timestamps during short and long builds, then
  decide whether a bounded keepalive is necessary.

---

# 17. Reboot ownership

Desired design:

* [x] Avoid automatic reboot from low-level installer.
* [x] Allow caller to own user-facing reboot behavior.
* [x] Current upstream flow successfully completed without automatic reboot.
* [x] Audit current scripts to confirm no duplicate reboot prompts.
* [x] Make `setup_nvidia.sh` default to no reboot prompt.
* [x] Support explicit `--offer-reboot` for standalone upstream-development use.
* [x] `install_upstream.sh` does not independently own reboot prompting.
* [x] `online_install.sh` owns the normal production reboot prompt.
* [x] Confirm idempotent install never offers reboot.
* [ ] Confirm changed install offers reboot exactly once.

---

# 18. Uninstall

* [x] Remove project target directory.
* [x] Run `depmod`.
* [x] Resolve fallback NVIDIA module after removal.
* [x] Preserve fallback DKMS modules.
* [x] Rebuild initramfs.
* [x] Restore read-only filesystem.
* [ ] Re-test uninstall with `.ko.zst` project installation.
* [x] Verify fake-root uninstall rollback after a post-removal `depmod` failure.
* [x] Verify fake-root uninstall does not remove unrelated NVIDIA files outside the project target.
* [x] Verify fake-root reinstall after successful uninstall.

---

# 19. SteamOS read-only filesystem handling

* [x] Detect/handle SteamOS read-only mode.
* [x] Disable when required for system modifications.
* [x] Restore afterward.
* [x] Verify current system returned to:

  * `steamos-readonly status: enabled`
* [x] Test cleanup path if script receives SIGINT.

  * 2026-09-04: `tests/setup_nvidia_signal.py` interrupts the real setup entry point during a mocked `pacman -U`, verifies exit 130, exact disable/enable read-only restoration, and temporary-workspace removal. Focused validation passed via `heavy.sh python3 tests/setup_nvidia_signal.py`. The registered `heavy.sh ./tests/check.sh` run passed shell syntax and preceding contract checks, then stopped at the separately frozen post-owner-death transport cleanup approval case (`device_generation_store_invalid`: generation tree too deep). Commit recorded below.
* [x] Test cleanup paths for injected install/uninstall failures in a fake root.
* [x] Test cleanup path on checksum failure.

  * 2026-09-04: `tests/transaction.sh` now supplies a mismatched archive checksum and verifies failure occurs before every privileged/read-only operation, preserves fake module and state content byte-for-byte, leaves read-only enabled, and creates no install stage. `heavy.sh ./tests/transaction.sh` passed all install/uninstall rollback cases and confirmed the real module tree was untouched.
* [x] Test cleanup path after fake-root target replacement.
* [x] Ensure every script that disables readonly reliably restores it.

  * 2026-09-04: audited every `steamos-readonly disable` site. Existing install/uninstall transaction, recovery-controller, guardian-installer, setup-build-environment, and NVIDIA userspace cleanup traps remain in place. Added a missing scoped EXIT/INT/TERM restoration trap to `compile.sh`; `tests/compile_readonly_signal.py` interrupts the real entry point during mocked GitHub CLI package installation and verifies exit 130 plus exact disable/enable ordering. Focused validation passed via `heavy.sh python3 tests/compile_readonly_signal.py`.

---

# 20. Release metadata / BUILD-INFO

* [x] Record:

  * build time
  * SteamOS version
  * kernel version
  * NVIDIA version
  * source repository
  * source branch
  * source commit
  * dirty state
  * upstream commit
  * support repository
  * source provider
  * project patch state
* [x] Upstream build records:

  * `source_provider=upstream`
  * `project_patches=0`
* [x] Persist installed BUILD-INFO.
* [x] Ensure `support_commit` is populated rather than `unknown` during normal builds.
* [ ] Add build container digest to all relevant BUILD-INFO variants if not already consistent.
* [x] Add `schema_version=1` to newly generated BUILD-INFO files.
* [x] Record module SHA entries in BUILD-INFO and structured provenance.

---

# 21. Build/release reproducibility

* [x] Exact SteamOS version recorded.
* [x] Exact kernel recorded.
* [x] Exact NVIDIA version recorded.
* [x] Exact upstream NVIDIA commit recorded.
* [x] Build container digest determined during compile.
* [x] Exact Valve kernel header package found.
* [x] Record exact header package URL/repository in BUILD-INFO for new builds.
* [x] Record exact header package SHA256 for new builds.
* [x] Record support repo commit correctly for new upstream builds.
* [x] Record compiler/GCC version for new builds.
* [x] Record binutils version for new builds.
* [x] Record make version for new builds.
* [x] Record build target, paths, and parallelism for new builds.
* [x] Runtime-test the expanded metadata with a fresh build-only artifact.
* [x] Distinguish deterministic source identity from potentially nondeterministic binary output.
* [x] Prevent build-cache hits for dirty source/support trees and require matching support commit for clean cache hits.
* [x] Detect and record target/build compiler versions and major-version match.
* [ ] Prefer an installed matching-major compatibility compiler automatically;
  validate this path with Fedora `gcc15` after the successful GCC 16.2.1
  development build against Valve GCC 15.1.1.
* [ ] Decide whether certification requires compiler-major equality or an exact
  compiler build after comparative module/runtime testing.

---

# 22. Release selection / SteamOS compatibility

* [x] Exact release preferred.
* [x] Same SteamOS major/minor bounded fallback concept implemented.
* [x] Avoid arbitrary cross-version fallback.
* [x] Require exact kernel when selecting published module artifacts.
* [x] Normal resolver correctly chooses SteamOS 3.8.16 575 release.
* [x] Lock the release-selection policy into automated offline tests:

  * exact SteamOS release is preferred,
  * fallback is bounded to the same SteamOS major/minor series,
  * exact running kernel is always required,
  * exact NVIDIA version comes from the release selected by the resolver,
  * certified mode never selects a newer NVIDIA driver merely because one exists,
  * newer drivers require explicit `--development` or `--use-upstream` mode.
* [x] Remove nearest-distance fuzzy ranking in favor of one bounded certified policy.
* [x] Ensure certified fallback never moves forward to a newer SteamOS patch.
* [x] Ensure `setup_nvidia.sh` and `online_install.sh` use the same release selector.
* [x] Cover explicit 3.8.15–3.8.18 release-policy cases:

  * 3.8.15
  * 3.8.16
  * 3.8.17
  * 3.8.18
* [x] Ensure no old-kernel artifact can be selected after a kernel update.
* [x] Return a typed resolver result when no compatible release exists.

---

# 23. SteamOS updates / persistence

This remains a major future area, represented by three distinct gates:

* [x] Add a persistent boot-time guardian that detects the newly active slot's
  running kernel before the display manager without background polling. Treat
  missing, malformed, policy-mismatched, or conflicting installed NVIDIA
  identities as an inspection failure and enter console fallback even when the
  status subprocess itself fails. Snapshot status inputs through confined
  descriptors and reject symlinked, hardlinked, writable, replaced, or
  unexpected-owner identity records. Require the guardian's pinned NVIDIA
  policy file and reject absence, malformed content, or disagreement with the
  independently observed installed identity rather than falling back to that
  identity. Require one unambiguous owner-controlled module candidate, bind its
  identity across metadata inspection, and never substitute pathname ordering
  when the live depmod identity cannot be resolved.
* [x] Add a bounded UI-neutral recovery status/action contract shared by the
  canonical one-line install and image-builder deployments.
* [x] Require installed recovery health, repair completion, and fallback
  disablement to revalidate the rootfs payload receipt and freshly hash all
  five canonical compressed module payloads. Reject a missing, mismatched,
  replaced, noncanonical, or metadata-only module set instead of trusting
  `modinfo` version/vermagic alone.
* [x] Add mutually exclusive console, validated-iGPU, and explicitly authorized
  experimental Nouveau fallback profiles; never select Nouveau automatically.
  Treat persistent fallback state as a closed canonical record and reject
  duplicate keys, unknown profiles, non-boolean activation, or extra fields.
  Publish and remove it only through the locked, fsync-backed Core mutator;
  clean bounded abandoned temporaries and never follow a stale state symlink.
* [x] Bind online repair to the installed support revision and the ordinary
  exact-kernel published-artifact policy; leave fallback active on failure.
* [ ] Add an authenticated, explicitly approved on-device exact-kernel source
  build path for cases where no published artifact exists. A mutable source
  branch must never be treated as certified merely because compilation passes.
* [x] Persist exact target-bound delayed repair state outside the replaceable
  rootfs, wake on NetworkManager connectivity changes, and use a bounded timer
  fallback without making network availability a boot dependency. Read the
  NVIDIA/support policy from the persistent guardian snapshot rather than the
  replaceable active slot, and atomically retarget an active repair only after
  the independently observed running kernel changes; remove any old immutable
  release plan before publishing the replacement transaction.
* [x] Make cancellation disable retries without disabling recovery graphics or
  mutating a verified slot.
* [x] Install recovery cancellation/readonly traps before live-root mutation,
  terminate and reap isolated long-running GRUB, initramfs, and online-repair
  process groups, and exit 130 instead of returning into a cancelled mutation.
* [x] Require an active fallback before removal and repeat the complete
  receipt-bound target/module check after both recovery locks are held. Reject
  a target or module change between prompt and mutation without touching the
  fallback state.
* [x] Treat the boot guardian's pre-lock failure as provisional. Acquire both
  recovery mutation locks and repeat receipt-bound verification before enabling
  console fallback, avoiding a stale fallback transition when another
  serialized operation restored the exact payload while the guardian waited.
* [x] Serialize guardian, fallback, repair, and cancellation workflows without
  deadlocking the nested canonical installer. Keep delayed-repair state closed,
  bounded, canonical, single-linked, mode-0600, descriptor-locked, fsync-backed,
  and constrained to reviewed phase transitions; reject concurrent or unsafe
  state without replacing the last durable record. Remove terminal transaction
  state and immutable release plans only through their locked, validated,
  identity-bound, parent-fsynced helpers; never unlink active state or bypass
  those helpers from the recovery shell workflow. Reconcile a crash after exact
  installation but before transaction finalization from independent exact-module
  verification, without reviving a cancelled transaction; remove an orphaned
  immutable plan before beginning any new transaction. Reject operation fields
  that are irrelevant to the selected transaction/plan command, and stress
  concurrent deterministic reads, lock contention, repeated retarget/cancel,
  plus SIGTERM/SIGKILL lock-owner death without corrupting durable state.
* [ ] Connect the reviewed authenticated-cache bundle to on-device repair and
  test exact cached repair with GitHub, Valve, and Arch endpoints unavailable.
* [ ] Run delayed-network fault injection for absent/flapping connectivity,
  captive portal, DNS/TLS failure, reboot mid-wait/download, and identity drift.
* [x] Freeze the selected release identity and first downloaded archive hash for
  an active recovery transaction so a publication cannot be spliced into it.
  Keep that plan closed, canonical, size-bounded, mode-0600, single-linked,
  descriptor-read, identity-stable, and serialized across direct invocations;
  hash only a stable owner-controlled archive and publish create-only or by an
  exact prior-plan identity.
* [ ] Test publication races: an exact release appearing during offline wait,
  wrong-kernel/version releases, mid-download publication, and a certified
  equivalent appearing after a locally-built verified repair.
* [ ] Stage and independently verify the guardian assets and systemd enablement
  in OPEMOS.EXE-generated rootfs payloads.
* [ ] Test a 3.8.x kernel upgrade, missing-release behavior, and rollback to the
  previous slot without ever copying old-kernel modules into a new tree.
* [x] Define the versioned Open OPEMOS view model with fixed, enumerated
  recoveryctl actions and no arbitrary shell or device-path input.
* [ ] Finish the themed native Open OPEMOS frontend, Desktop Mode transition,
  persistent signed/package delivery through the canonical one-line installer,
  and keyboard/accessibility behavior without moving privileged policy out of
  recoveryctl. This is the installed target-device application, not the
  separate installation-media welcome UI owned by OPEMOS.EXE.
* [x] Design and implement a bounded full-screen OPEMOS boot interstitial that temporarily
  enters a dedicated no-input recovery session instead of Gaming or Desktop
  Mode, displays authenticated guardian/install progress, and then resumes the
  originally requested systemd target. It must render without depending on a
  working NVIDIA stack (prefer validated simpledrm/iGPU or software rendering),
  never accept arbitrary input or shell content, and fail open to the safe
  console/boot target under a watchdog timeout. Specify crash/reboot recovery,
  target-transition idempotency, accessibility, display hotplug, missing DRM,
  corrupt progress, and power-loss tests before enabling it during boot. The
  direct DRM/KMS software renderer, bounded root-owned progress document,
  fail-open systemd ordering, optional exact-ELF installation, macOS browser
  simulation, and Fedora VM build/KMS harness are implemented. Physical
  SteamOS display and power-loss validation remains in the top unresolved list.

---

# 24. Fresh-stock installation

The active clean-stock/hardware gate in the top index uses these acceptance
criteria:

* Start from stock SteamOS with no project files or supported NVIDIA state.
* Run the intended one-line online installer and resolve the exact certified
  release, userspace, modules, and initramfs.
* Reboot once into Gaming Mode and verify `nvidia-smi`, `modinfo`,
  `/proc/driver/nvidia/version`, GPU use, Desktop Mode, and Gaming Mode.
* Require no manual recovery steps.
* Require an idempotent second install and an uninstall that restores a usable
  fallback.

---

# 25. NVIDIA userspace exact-version policy

* [x] Enforce userspace/kernel version alignment.
* [x] Upstream 580 module install required userspace 580.119.02.
* [x] Production 575 release resolves userspace 575.64.05.
* [x] Test mismatch detection intentionally with a 575 module archive and installed 580 userspace.
Newer, older, broken, and incomplete userspace cases are covered by the active
online userspace transaction-safety gates above and the exact reviewed-lock
offline installer contract.

---

# 29. CI / automated checks

* [x] Run `bash -n` on all shell scripts locally and in CI.
* [x] Run `git diff --check` locally and in CI.
* [x] Run error-level ShellCheck in CI.
* [x] Test resolver parsing.
* [x] Test release-tag parsing and canonical publisher identity.
* [x] Test archive traversal rejection across installer and publisher inputs.
* [x] Test checksum rejection.
* [x] Test BUILD-INFO parsing and embedded/sidecar agreement.
* [x] Test raw `.ko` idempotency.
* [x] Test `.ko.zst` idempotency.
* [x] Test empty/incomplete module-set rejection.
* [x] Test wrong-kernel rejection.
* [x] Test wrong NVIDIA version rejection.
* [x] Test wrong SteamOS series and patch-selection behavior.
* [x] Test bounded certified fallback selection.
* [x] Test the release-selection policy matrix from section 22, including exact-kernel rejection.
* [x] Test detached-HEAD upstream semantics, including the empty-branch-name case.
* [x] Test current cache/stage ownership and non-root `zstd` operation.
* [x] Test `--development` and `--use-upstream --resolve-only` boundaries with a failing sudo shim.
* [x] Test cache ownership immediately after mocked/forced sudo-backed failure paths.
* [x] Add a reusable non-sudo pre/post-reinstall baseline report.
* [x] Run fake-root install/uninstall rollback transactions in the local/CI check suite.
* [x] Test temp-helper bootstrap ordering.
* [ ] Test all scripts for unbound variables under `set -u`.
* [x] Test `--help` exits 0.
* [x] Test mutually exclusive arguments.
* [ ] Test build-only mode.

---

# 30. CLI / self-documentation

* [x] Recognize current commands were insufficiently self-commenting.
* [x] Move the comprehensive engineering reference under `docs/`, create a
  task-oriented GitHub Pages site, and retain a concise root README with native
  Steam Deck terminal commands.
* [x] Add developer tutorials, image-builder integration guidance, API-style
  command/result/progress documentation, security policy, navigation, and
  per-page contents.
* [x] Add CI validation for documentation front matter, navigation, links,
  anchors, command paths, workflow permissions, and README size.
* [ ] Enable and verify the GitHub Pages deployment in repository settings.
* [x] Rename `--driver` to `--development`.
* [x] Add proper `usage()` to `install_upstream.sh`.
* [x] Make `--help` work everywhere.
* [ ] Run one final CLI consistency audit so every mode uses standard formatting
  and explains:

  * purpose,
  * userspace behavior,
  * module behavior,
  * source provider,
  * whether project fixes are applied,
  * consistent `certified`, `development`, `upstream-development`, and
    `project-patched` terminology.
* [x] Add examples in README for:

  * certified install
  * development selection
  * pristine upstream baseline
  * build-only
  * local artifact install
  * in-code build/install.
* [x] Make destructive operations visibly distinct from resolution/build-only operations.

---

# 31. Code cleanup

* [x] Centralize project temp helpers where usable.
* [ ] Centralize bootstrap cache-root creation without violating pre-bootstrap
  ordering, then remove redundant hardcoded cache-path creation.
* [x] Audit duplicate `setup_build_env.sh` invocation.
* [x] Audit stale `DRIVER_*` names after `--development` rename.
* [x] Audit README for `--driver`.
* [ ] Audit unused variables plus stale mode terminology in comments,
  release scripts, and action entry points.
* [x] Audit all direct `/tmp` usages and classify:

  * host `/tmp` → generally avoid for large work.
  * container `/tmp` → allowed/intended.
* [x] Add comments where this distinction matters.

---

# 33. Offline SteamOS image-builder integration

## Responsibility boundary

The support repository owns exact Valve header acquisition/authentication,
NVIDIA source selection, exact-target compilation, module validation, archive
format, reviewed userspace locks and trust, package-level payload profiles,
offline installation, target `depmod` and initramfs generation, structured
post-install verification, recovery/device clients, machine-readable results,
provenance, Fedora transaction tests, build cleanup, and eventual
certified-artifact publication.

It also owns canonical bundle contents, manifests, schemas, safe next-action
semantics, target-transaction rollback, and the installed-system Desktop
Companion and DRM/KMS interstitial. It does not own host HTTP transport, macOS
application updates, recovery-image partition selection, or final image export.

The image builder owns recovery-image inspection, active boot-kernel selection,
Valve A/B and installer-layout handling, appliance lifecycle, progress and
cancellation UX, authenticated host acquisition and appliance transfer,
exclusive writable-overlay ownership, invoking the pinned support contract,
independent final-image validation, safe export/USB workflows, and preservation
of the original input. It must not independently decide release compatibility,
userspace dependency closure, signer policy, or installer mutation semantics.
OPEMOS owns authenticated cache/bundle semantics; OPEMOS.EXE owns where and how
those authenticated objects are acquired, retained, and handed to appliances.
The OPEMOS.EXE installation-media welcome application is builder-owned and is
separate from Core's installed-system no-input interstitial.

The patched NVIDIA source repository owns versioned `nvidia/<version>` branches,
SteamOS-specific patches, and an unambiguous driver-version-to-source-commit
mapping. It must never silently fall back to pristine upstream.

Maintainers/CI own real NVIDIA hardware boot tests, certification/publication,
trusted Valve key management, SteamOS update testing, and Secure Boot/module
signing policy.

## Completed support-side contract

* [x] Add versioned offline-target JSON resolution in `lib/resolve_target.py`.
* [x] Publish `contracts/schemas/resolver-result-v2.schema.json` and an
  executable compatible/no-match/invalid-target consumer fixture.
* [x] Publish `contracts/schemas/installer-progress-v1.schema.json`, preserving
  additive fields while documenting determinate/indeterminate record shape and
  the stream validator's monotonicity responsibility.
* [x] Add `lib/installer_bundle_manifest.py`, with a fixed support-owned
  inventory, immutable Git-blob/mode reads, deterministic bundle identity,
  create-only output, validation against the exact commit, and hostile-input
  tests. The first real inventory validation covers 55 files.
* [x] Accept target SteamOS version, exact kernel, and ELF architecture rather
  than inspecting the Fedora appliance host identity.
* [x] Apply the normal bounded same-series SteamOS certification fallback while
  still requiring an exact kernel match.
* [x] Treat no compatible artifact as a normal fail-closed resolution result.
* [x] Advertise the managed x86_64 exact-target build contract as an additive
  `nextAction` only for `no_compatible_release`; never offer it for incomplete,
  ambiguous, or otherwise invalid publication metadata.
* [x] Require the expected release archive, SHA256 sidecar, and provenance
  sidecar to be advertised before returning a compatible published artifact.
* [x] Keep publication separate from certification and require consumers to
  verify external/embedded provenance before preserving its trust classification.
* [x] Add native x86_64 Fedora exact-target compilation in
  `bootstrap/build_for_target.sh`.
* [x] Derive the exact Neptune headers filename from the full target kernel.
* [x] Support automatic Valve-repository discovery, an exact Valve URL, or a
  pinned local headers package.
* [x] Require the exact target kernel build-tree path; do not accept the first
  unrelated `/usr/lib/modules/*/build` directory.
* [x] Validate Arch `.PKGINFO` package name, version, and architecture.
* [x] Reject unsafe header-archive paths.
* [x] Reject duplicate header members, special device/stream entries, escaping
  symlink/hardlink targets, absent hardlink targets, and excessive archive or
  metadata sizes before extraction.
* [x] Require a prepared kernel tree with `autoconf.h` and `Module.symvers`.
* [x] Build against explicit `SYSSRC`/`SYSOUT`, never the Fedora guest kernel.
* [x] Require exactly the five expected NVIDIA modules.
* [x] Validate exact vermagic and x86_64 ELF architecture for every module.
* [x] Emit the existing installer-compatible archive, checksum, and build-info
  layout.
* [x] Add JSON `--resolve-only` build-plan output.
* [x] Make the shared module-set validator usable by macOS Bash 3.2 checks.
* [x] Make portable macOS path/checksum helpers explicit.
* [x] Make `tests/check.sh` clearly skip Bash-4-dependent transaction tests on
  macOS while keeping them mandatory for Fedora/Linux validation.

## First real-build gate

* [x] Complete the dedicated x86_64 Fedora/QEMU build for SteamOS 3.8.14,
  NVIDIA 575.64.05, and kernel
  `6.16.12-valve24.4-1-neptune-616-gfe145653a794`.
* [x] Confirm Valve still serves the derived historical package:
  `linux-neptune-616-headers-6.16.12.valve24.4-1-x86_64.pkg.tar.zst`.
* [x] Confirm the package contains the exact full kernel-release build path.
* [x] Confirm NVIDIA 575.64.05 compiles without target-header reconstruction.
* [x] Confirm all five output modules report the exact target vermagic and
  NVIDIA version.
* [x] Confirm the offline-root validator and installer accept the generated
  archive unchanged.
* [x] Preserve the complete build log and generated build metadata from this
  first integration run.

## Authentication and trust

* [x] Add Valve package detached-signature verification using a caller-supplied,
  reviewed keyring or equally strong trusted checksum provenance.
* [x] Record signing/primary fingerprint and signature verification result.
* [x] Treat post-download SHA256 as provenance/integrity metadata only;
  authenticated headers require a detached signature from an active pinned
  signer.
* [x] Pin the expected Valve package origin and reject redirects to untrusted
  origins.
* [x] Pin the reviewed `holo-keyring` package hash, extracted keyring hash, and
  exact historical-header signer fingerprint in a versioned trust manifest.
* [x] Define key rotation and revocation through explicit reviewed manifest
  entries; only signers marked `active` are accepted by builds or keyring prep.
* [x] Keep unsigned-header or override builds labeled
  `development-unverified`.
* [x] Use `locally-built-verified` only for authenticated exact headers, clean
  pinned source, complete provenance, and all structural checks.
* [ ] Reserve `certified-published` for maintainer-published artifacts that have
  also passed the required hardware test matrix.
* [x] Never silently promote a local successful compilation to certified.

## Machine-readable results and typed failures

* [x] Add a versioned final build-result JSON document, not only build-plan
  output and human-readable logs.
* [x] Include result status, trust classification, target identity, output
  names/hashes, and a reference to provenance-bearing build info without
  private host paths.
* [x] Provide stable typed failure reasons for:

  * `invalid_target`
  * `unsupported_architecture`
  * `headers_not_found`
  * `header_download_failed`
  * `header_signature_missing`
  * `header_signature_invalid`
  * `header_identity_mismatch`
  * `header_tree_incomplete`
  * `source_branch_missing`
  * `source_version_mismatch`
  * `dependency_install_failed`
  * `compiler_policy_mismatch`
  * `compilation_failed`
  * `module_set_incomplete`
  * `module_metadata_invalid`
  * `module_version_mismatch`
  * `module_architecture_mismatch`
  * `vermagic_mismatch`
  * `packaging_failed`
  * `cancelled`

* [x] Emit stable phase-specific reasons for normal build execution and early
  argument/target validation.
* [x] Keep missing headers/source and incompatibility as safe, actionable
  outcomes rather than selecting a nearby build.
* [x] Separate concise bounded user-facing messages from structured maintainer
  diagnostics in build, validation, and installation results.

## Complete provenance

* [x] Record support repository URL, exact commit, and dirty state.
* [x] Record NVIDIA source repository, branch/provider, exact commit, dirty
  state, and upstream base commit.
* [x] Record Fedora appliance version and architecture.
* [x] Record GCC, binutils, make, kmod, and relevant build-tool versions.
* [x] Record target SteamOS, exact kernel, NVIDIA version, header package
  identity, header URL/local origin, and SHA256.
* [x] Record header signer/signature state.
* [x] Validate the pinned Valve keyring and historical header signature end to
  end in the Fedora appliance; dearmor Valve's published key collection before
  passing it to `gpgv`.
* [x] Record separate build start/end timestamps; build mode is recorded.
* [x] Record every module filename, SHA256, NVIDIA version, ELF architecture,
  and full vermagic in machine-readable form.
* [x] Include versioned `PROVENANCE.json` in the archive and publish the same
  document as a sidecar referenced by the final result contract.
* [ ] Image builder must copy the referenced provenance into its image manifest.

## Canonical publication

* [x] Provide one Bash-3.2-compatible publisher for archive, checksum,
  build-info, and provenance assets.
* [x] Validate canonical names, checksum, target/trust/release identity, clean
  complete support/source commits, and embedded metadata before GitHub mutation.
* [x] Provide machine-readable `--dry-run` and fail-closed `--create-only` modes.
* [x] Pin live publication to the canonical support repository unless an
  explicit development-only repository override is supplied.
* [x] Make `compile.sh --auto-upload` delegate to the canonical publisher.
* [x] Cover malformed input, canonical notes/title, asset ordering, and
  existing-release refusal without touching a live release.
* [x] Add a deterministic authenticated raw-module repacker with separate
  payload/representation hashes, pinned encoding, non-mutating JSON dry-run,
  revisioned release identity, and create-only canonical publication.
* [x] Stream repack payloads and publication validation through bounded files,
  require the claimed clean support checkout, reject duplicate options, and
  clean partial create-only output sets.

## Optional reviewed gaming payload

* [x] Define the schema-1 `gaming-no-cuda-v1` preservation and ownership policy.
* [x] Expose a stable exact-target resolver capability and authenticate optional
  installer profile metadata against support-owned policy and lock hashes.
* [x] Keep capability disabled when no reviewed exact-target package set exists;
  never ask the image builder to remove userspace paths heuristically.
* [x] Produce the first reviewed deterministic derived package set for the exact
  SteamOS 3.8.14 / 575.64.05 / valve24.4 target; pin every source signature,
  omission, output package hash, ownership record, and saved-byte total.
* [x] Publish the standalone closed schema-1 proof and bounded deterministic
  compatibility matrix. Distinguish structural parsing from terminal binding
  to the reviewed policy, exact profile, userspace lock, target, and packages.
* [ ] Hardware-test the reduced profile and its complete-payload restoration on
  the exact target before promoting either path beyond development trust.

## Reviewed userspace-lock data generations

The design handoff is recorded in
`contracts/userspace-lock-generations.md`. This is a data channel, not the
desktop companion/Core binary update channel. A routine reviewed lock must not
require reinstalling Core/CLI, updating a binary, or reimaging SteamOS.

### Producer and publication

* [x] Keep dependency-closure discovery in the maintainer-only Core audit and
  require create-only candidate/final review before a lock becomes production
  input.
* [x] Publish the inactive closed schema-1 discovery and generation-manifest
  structures, Core semantic validator, and bounded deterministic compatibility
  matrix. Cover exact authority/compatibility, fresh bootstrap, active
  predecessor continuity, bounded authenticated catch-up, replay/downgrade,
  targets, lock/file equality, portable filenames, aggregate bounds, strict
  canonical JSON, durable active/last-known-good identities, rollback-stable
  high-water state, and hostile inputs without configuring a production
  signer, bootstrap checkpoint, or network path.
* [x] Freeze the schema-1 consumer interpretation for externally authenticated
  discovery: canonical discovery/signature basenames, OpenPGP v4 detached
  signatures with SHA-256/384/512, separately installed authority/checkpoint,
  data-only mode normalization (`0400` files and `0500` directories), and a
  complete logical storage-envelope ceiling distinct from payload bytes and
  filesystem safety reserve. Publish these constants in the semantic module
  and compatibility handoff without changing the closed wire documents. Use a
  standalone deterministic status matrix and one shared parser to enforce
  exact primary/subkey fingerprint semantics, one signature, strong hashes,
  bounded output, adverse-status rejection, and verifier cancellation cleanup.
* [x] Publish the inactive closed bootstrap-policy and minimum-checkpoint
  schemas, semantic validator, and deterministic hostile compatibility matrix.
  Separate stable binary trust/endpoint/schema/replay policy from mutable
  active/LKG/high-water generation state. Test endpoints use `.invalid`; no
  production key, keyring, checkpoint, endpoint, or activation is configured.
* [x] Publish a deterministic immutable request-plan schema, semantic planner,
  and hostile compatibility matrix. Derive discovery, detached-signature,
  manifest, and every payload request only from the installed bootstrap policy
  plus exact independently authenticated bytes; pin origin, release sequence,
  names, hashes, sizes, ordering, redirects=false, and aggregate bounds without
  implementing an HTTP client or configuring production trust.
* [x] Replace caller-asserted authentication JSON at the planner boundary with
  a verifier-created, immutable, non-serializable capability bound to the exact
  policy, keyring, discovery, manifest, signatures, OpenPGP primary/subkey
  identities, and accepted hash algorithms. Publish its bounded JSON audit
  record schema and hostile matrix while explicitly forbidding deserialization
  from recreating authorization.
* [ ] Create a dedicated reviewed data-generation signer policy and binary
  verification keyring. Do not reuse Arch, Valve, NVIDIA, commit-signing, or
  desktop-binary update keys.
* [ ] Implement a deterministic create-only publisher that emits the descriptor,
  descriptor signature, manifest, manifest signature, and every manifest-owned
  asset in canonical order. Record release/tag/asset digests and signer
  evidence without mutating an existing generation.

### Two independent consumers

* [x] Implement the inactive installed-device physical cache and local
  activation lifecycle independently from OPEMOS.EXE and desktop binary
  updates. Include create-only immutable generations, fsync-backed atomic
  state, exclusive locking, active/LKG identities, rollback-stable high-water,
  health acknowledgement, bounded retention, exact payload verification,
  cancellation, ENOSPC, abandoned-stage and post-commit crash recovery tests.
  Keep production trust and networking disabled.
* [x] Publish a bounded deterministic installed-device result/state/health
  compatibility matrix and use one Core semantic validator in the lifecycle
  and tests. Cover healthy/LKG equality, pending-health separation,
  rollback-stable high-water, exact-active evidence binding, closed fields,
  duplicate keys, non-finite values, malformed records, and size bounds.
* [x] Replace the inactive device cache's single state marker with an internal
  alternating revisioned journal. Reconcile restart state against confined
  immutable cache sequences, clean bounded abandoned marker temporaries,
  migrate the legacy marker, bind the lifecycle lock to its opened inode, and
  fail closed instead of lowering high-water when marker loss is ambiguous.
* [x] Confine inactive device-cache retention and cleanup to opened directory
  descriptors and the canonical two-level generation shape. Preserve active,
  pending-active, and LKG before pruning; bound nodes, depth, and logical bytes;
  reject symlinks, hardlinks, and special entries; and require conservative
  byte/finite-inode admission before staging a new immutable generation.
* [x] Journal the activation intent before publishing an immutable cache
  generation. On restart, distinguish a committed activation from an orphaned
  publication using the exact prior revision/state hash, verify the cached
  candidate, and either clear the completed intent or remove the uncommitted
  generation through confined marker-first deletion. Cover state ENOSPC,
  SIGTERM, and SIGKILL on both sides of the durable state commit.
* [x] Add an inactive installed-device acquisition substrate for `update` and
  `update-or-repair`. Core derives phased immutable request plans from installed
  bootstrap policy and authenticated discovery/manifest snapshots; a
  development-only injected transport receives exact URLs and identities but
  cannot select either. Core rejects missing, extra, renamed, oversized, or
  substituted output, rechecks policy/keyring/checkpoint identity between
  phases using same-descriptor trust snapshots, streams exact payload hashes
  into private staging without generation-sized memory accumulation, and
  publishes only an exact authenticated generation to the separate download
  cache. Cover forged audit JSON, plan/directory replacement, stale trust files,
  hardlink/symlink/special output, outage, timeout, ENOSPC, cancellation,
  hostile/partial staging cleanup, repeat download, and the invariant that
  acquisition never activates a generation. Production transport remains
  unconfigured and fail-closed.
* [x] Contain injected transport descendants with a separate Core watchdog and
  close-on-exec owner-liveness pipe. Parent SIGKILL closes the pipe; the
  watchdog terminates and reaps the isolated transport process group before
  stale output can be consumed. Cover portable pipe-loss/descendant teardown,
  normal exit propagation, catchable cancellation, and a conditional Linux
  lifecycle-parent SIGKILL integration case with restart cleanup.
* [x] Bind installed generation trust snapshots to owner-controlled,
  non-writable files and containing trust directories for the complete
  lifecycle operation. Reject unsafe initial modes/owners and file or directory
  replacement between acquisition phases before cache publication or state
  mutation.
* [x] Specify the stable canonical discovery location in the separately
  authenticated bootstrap policy, without placing URLs or redirects inside the
  signed descriptor. Core's immutable request planner now consumes that policy
  in the inactive installed-device acquisition path. OPEMOS.EXE owns host
  transport/cache; installed Core/CLI owns device transport/cache. Both consume
  the same identities, schemas, fixtures, and trust root without importing one
  another's updater implementation.
* [ ] Add the reviewed installed-device transport and stable discovery location
  to `update` and one-line safe `update-or-repair`; integrate the active
  reviewed lock with exact-target repair while keeping networking outside
  boot-critical paths. The production CLI still fails these commands closed.
* [x] Bind generation health acknowledgement and rollback to an independently
  observed current SteamOS/kernel/NVIDIA target before recovery integration.
  Core now observes SteamOS, running kernel, architecture, and its installed
  NVIDIA identity separately from health evidence, rejects ambiguous or unsafe
  observations, verifies the current rootfs's six-file payload receipt rather
  than trusting a persistent `/var` marker alone, binds every receipt file read
  to one opened and identity-stable directory descriptor, and requires an exact
  target lock before LKG advancement or rollback. A private two-phase health marker
  durably binds LKG to that target and receipt across cancellation, SIGKILL,
  restart, and A/B transitions. The schema-1 health record remains
  generation-only evidence. Health/check/rollback also require the receipt's
  reviewed userspace-lock filename and SHA-256 to match the selected
  generation's exact target-lock record; same-target generations are not
  interchangeable.
* [ ] Validate the device lifecycle under Fedora and real SteamOS, including
  inode exhaustion, power loss at every durable boundary, health timeout,
  filesystem corruption, kernel-observed watchdog behavior, and service/
  recovery integration.
* [ ] Keep the legacy embedded/pinned userspace-lock path until Core unit,
  Fedora/SteamOS integration, cancellation, cleanup, fault-injection,
  last-known-good rollback, and cross-repository equivalence tests all pass.
  OPEMOS.EXE must likewise retain its existing path until it proves the same
  schema/fixture decisions and independently validates transferred assets.

### Policy still requiring maintainer approval

* [ ] Choose and review the dedicated data-generation signing key and rotation/
  revocation process. An unknown key or policy version fails closed and key
  rotation is not a routine data update.
* [ ] Pin an initial signed generation checkpoint in each consumer's installed
  trust policy and define checkpoint advancement. A fresh consumer must not
  infer freshness from `publishedAt` or accept any historically valid signature.
* [ ] Define authoritative sequence allocation, state-loss recovery, and an
  explicitly signed emergency downgrade procedure. Ordinary discovery never
  permits an equal or lower sequence than the consumer's durable high-water
  mark.
* [ ] Define the installed-device discovery retry/backoff and health evidence
  required before activation is acknowledged. Network timing is operational;
  accepted identities and rollback semantics remain Core policy.

## Cancellation, cleanup, and caching

* [x] Define SIGINT/SIGTERM cancellation suitable for the Rust appliance manager.
* [x] Ensure cancellation terminates `make` and all descendant compiler jobs.
* [x] Remove temporary source clones, header downloads, extracted trees,
  modules, and partial archives after cancellation or failure.
* [x] Never leave a final-named archive after failure; publish staged output only
  after package construction and hashing succeed.
* [x] Emit a machine-readable `cancelled` result after cleanup.
* [ ] Preserve bounded diagnostic logs without credentials or private host paths.
* [ ] Cache only authenticated headers/artifacts for normal reuse.
* [ ] Key caches by exact header identity/hash, source commit, support commit,
  NVIDIA version, target kernel, architecture, and toolchain/appliance identity.
* [x] Detect and remove abandoned inactive build sessions on the next run.

## Fedora/Linux validation

* [x] Run `tests/check.sh` under the same Fedora appliance used for builds.
* [x] Run the complete fake-root install/uninstall transaction suite under
  Fedora Bash 5; no transaction-test skips are permitted for release validation.
* [x] Add fixtures for wrong `.PKGINFO`, wrong full kernel directory, missing
  prepared-tree files, unsafe archive paths, and extraction-root escape.
* [x] Add file-backed fixtures for duplicate/missing modules, metadata-command
  failures, wrong NVIDIA version, wrong ELF architecture, and wrong vermagic.
* [x] Test header network failure and successful-but-truncated downloads with
  typed failure results and no final artifacts.
* [x] Test cancellation during both header download and compilation, including
  descendant termination, temporary-tree cleanup, and a cancelled result.
* [x] Test output publication with an injected `ENOSPC` failure and verify the
  typed packaging failure, removal of partial final names, and work-tree cleanup.
* [ ] Test both automatically downloaded and pinned-local header inputs.
* [x] Verify artifact installation, idempotency, failure rollback, and cleanup
  against fake mounted targets before modifying a real recovery-image overlay.
* [x] Define the x86_64-only explicit-root installer CLI with authenticated
  `nvidia-utils`/`lib32-nvidia-utils` inputs, `--validate-only`, and a structured
  result that cannot report success while mounts remain active.
* [x] Add fail-closed validation for target identity, artifact provenance/module
  hashes, userspace signatures/version agreement, safe package paths, and GSP
  firmware presence.
* [x] Pin active package-specific Arch signer fingerprints for `nvidia-utils`
  and `lib32-nvidia-utils`, while recording the prepared binary keyring hash in
  every validated/successful result.
* [x] Add target-root pacman installation, module compression/configuration,
  offline depmod, target mkinitcpio, reverse-order bind-mount cleanup, and
  structured success/failure/cancellation results.
* [x] Require SteamOS's populated `/usr/lib/holo/pacmandb` package database,
  use it explicitly for installation and ownership queries, and never create an
  empty `/var/lib/pacman` fallback.
* [x] Keep rootfs `/boot` visible for mkinitcpio, require `efi-A` at `/efi`, and
  update its SteamOS GRUB Linux entries atomically and idempotently with the
  project NVIDIA kernel arguments.
* [x] Recognize and preserve Valve's `steamenv_boot linux ...` command prefix
  while applying the same exact idempotent kernel-argument policy.
* [x] Require `/efi` to be a distinct FAT mount before mutation, reject symlinks
  in every installer-owned and package-member target path, and bound the exact
  canonical module archive's compressed and decompressed sizes.
* [x] Set bounded 1 GiB compressed/member and 2 GiB total module-archive limits
  compatible with the observed 632.5 MB development artifact while retaining
  the 1 MiB metadata cap.
* [x] Parse and validate confined Holo package name/version records, reject
  duplicates, and require the SteamOS base `filesystem`, `glibc`, and `pacman`
  records rather than trusting only a nonzero directory count.
* [x] Permit unrelated real Holo records without `%ISIZE%`, while requiring a
  unique numeric installed size for every package receiving replacement credit
  and emitting the package directory plus invalid field names on failure.
* [x] Resolve the authenticated userspace packages' complete version-checked
  dependency closure against incoming and installed Holo package/provides data.
* [x] Accept repeated signed, locally staged Arch dependency packages, include
  them in one offline transaction and storage result, and return typed missing
  dependency/requester fields without performing network resolution.
* [x] Emit throttled schema-1 validation progress on stderr with bounded retry
  correlation, fixed phases, byte/item counters, and no paths or free-form text.
* [x] Add a maintainer-only, non-mutating dated Arch snapshot closure audit that
  verifies repository databases and packages with a pinned full keyring and
  emits all missing package/signer reviews in one candidate lock.
* [x] Require normal offline installation to use a reviewed schema-1 userspace
  lock, minimal reviewed keyring, and an exact package set with no extras.
* [x] Authenticate the audit keyring and dated repository databases with
  support-owned hash manifests; add create-only lock finalization and executable
  recursive/negative audit tests.
* [x] Confine authenticated archive extraction, bound repository records and
  relations, reject redirects and partial downloads, and use symlink-safe
  atomic/create-only writers for audit, lock, validation, and result contracts.
* [x] Produce the reviewed SteamOS 3.8.14/NVIDIA 575.64.05 six-package lock and
  its four-signer minimal binary keyring from the real exported Holo database.
* [x] Emit conservative root/var/EFI available and required bytes, declared
  package size, final module size, replacement credits, and initramfs reserve;
  fail before mutation with `target_space_insufficient` when any mount cannot fit.
* [x] Detect and report Btrfs compression while crediting zero speculative
  savings, so compression context cannot silently override a space failure.
* [x] Add synthetic success, repeated-execution, corrupt-input, injected
  initramfs failure, and mounts-released result coverage.
* [x] Require exclusive target-Btrfs mounting for filesystem-wide compression,
  reject NOCOW/NOCOMPRESS destinations, report compression-policy restoration
  independently, and verify every locked userspace package's exact installed
  version and authenticated payload after the offline transaction; independently
  revalidate the exact installed five-module set before depmod/initramfs.
* [x] Make malformed and duplicate installer arguments return a bounded schema-1
  `invalid_arguments` result whenever `--result-json` is discoverable.
* [x] Bound signed userspace package files/listings/member counts and reject
  duplicate/noncanonical paths, special entries, malformed links, and excessive
  expansion before target-root pacman execution; verify the real staged NVIDIA
  and egl-gbm packages remain accepted.
* [x] Return exact provenance and reviewed-lock hashes plus the complete verified
  package metadata needed by the image-builder consumer.
* [x] Bound cancellation with TERM-to-KILL escalation, cover the process-group
  creation race, reap children, and repeatedly exercise validation/mutation
  cancellation without stale mounts.
* [x] Add a `btrfs-zstd3` scratch measurement and mutation path using exact
  authenticated payloads and Btrfs allocated-byte deltas, with bounded inputs,
  structured physical/storage fields, exact no-op-only replacement credit,
  verified target policy activation/restoration, and
  success/failure/cancellation/cleanup/repeat tests; keep release readiness
  blocked pending independent final-root validation.
* [x] Reconcile pacman's logical `CheckSpace` gate with authenticated physical
  Btrfs admission using an exact-validation, live-policy-verified, temporary
  confined config; preserve normal CheckSpace and signature/offline policy for
  every other transaction, with cleanup and negative regression coverage.
* [x] Establish and independently verify confined recursive `/dev`, `/proc`,
  and `/sys` target mounts before pacman hooks, retain them through mkinitcpio,
  and recursively clean them on every terminal path. Normalize raw/compressed
  modules to explicit root:root 0644 `.ko.zst` destinations and preserve
  aggregate five-module mismatch diagnostics in the installer result.
* [x] Add a private appliance-backed `/var/tmp` bind before pacman hooks, retain
  it through explicit mkinitcpio, require confined mode-1777 target semantics
  plus bounded byte/inode capacity, report typed workspace failures, record all
  four runtime mounts in progress/results, and detect post-transaction hook
  failure independently from pacman's exit status. Treat an all-zero dynamic
  inode report as unknown rather than exhausted: require a bounded 4,096-file
  allocation-and-cleanup probe and record its capacity basis.
* [x] Inspect the mounted `var-A` `/var/tmp` during validate-only, report a
  missing mountpoint as preparation-required without mutation, safely create
  only that missing root-owned mode-1777 directory before pacman hooks, and
  reject links, wrong types/modes, or target byte/inode exhaustion.
* [x] Emit schema-1 mutation progress for pacman policy, runtime mounts, exact
  package/module installation and verification counts, GRUB, depmod,
  indeterminate initramfs generation, installation state, and cleanup; retain
  the caller's bounded attempt and never expose paths or command output.
* [x] Preserve bounded typed scratch-measurement failures through validation and
  the final installer result, including phase, safe command identity, exit
  status, and sanitized stderr for dependency, mkfs, mount, extraction, Zstd,
  Btrfs usage, ENOSPC, and cleanup failures.
* [x] Run the exact reviewed six-package/module payload through real x86_64
  Fedora scratch Btrfs: 528,154,624 allocated payload bytes and 757,461,736
  bytes including current initramfs/safety reserves, fitting the observed root
  with a 151,039,256-byte margin; verify real cancellation leaves no mount.
* [ ] Rerun the real Fedora/recovery-overlay mutation suite with the Holo pacman
  database contract and verify ownership, repeat execution, failure rollback,
  initramfs output, and mount cleanup before accepting an exported image.

## Known cross-project edge cases

* [ ] After repinning this support commit, make the image builder deserialize
  and independently compare `provenanceSha256`, `userspaceLock`, and every
  extended package record field; its current consumer safely ignores these
  additive schema-1 fields and validates the older strict subset only.
* [ ] Image builder must determine the actual boot kernel when multiple module
  trees exist; support-side compilation must receive one explicit target.
* [x] Image builder can launch an x86_64 TCG build appliance on Apple Silicon;
  the first complete build succeeded in 30m15s.
* QEMU x86_64 software-emulation slowness is a progress/UX concern, never
  permission to weaken compatibility checks.
* [x] Require and stage the exact reviewed NVIDIA userspace closure and matching
  GSP firmware; successful `.ko` compilation alone is insufficient.
* [ ] Validate offline `depmod` and target initramfs output through Valve A/B
  slots, installer-copy behavior, and first-boot ordering.
* [ ] Define Secure Boot/module-signing behavior before claiming supported
  Secure Boot installations.
* [ ] Verify SteamOS updates and slot changes do not silently leave stale modules.
* [ ] Preserve a recovery route if NVIDIA initialization fails on first hardware
  boot.

---

# Newly identified installer and publication hardening

## Immutable inputs and exclusive target lifecycle

* [x] Copy every validated archive, package, signature, keyring, lock, and
  provenance document into a private immutable staging directory. Validate and
  mutate only from those copies, rejecting any source that changes during
  snapshot creation.
* [x] Add an exclusive per-target lifecycle lock to `install_to_root.sh` so
  concurrent validation or mutation of one mounted root fails before mutation.
* [x] Record and repeatedly verify rootfs and EFI mount identities between
  validation, every destructive phase, and cleanup. Abort on replacement,
  unexpected remount, or filesystem-identity drift.

## Mandatory structured post-install verification

* [x] Make `moduleVerification` mandatory for success and require all five
  modules to pass exact payload-hash, ownership, mode, architecture, NVIDIA
  version, and vermagic verification.
* [x] Add mandatory structured `userspaceVerification` covering every locked
  package, owned file/link/library, and matching GSP firmware result.
* [x] Add mandatory structured `initramfsVerification`; inspect the exact
  generated initramfs and require the intended NVIDIA modules and configuration
  rather than trusting only mkinitcpio's exit status. Keep `nvidia-peermem`
  rootfs-only and return the explicit four-module early-boot contract.
* [x] Publish the standalone bounded initramfs-verification schema-1 contract
  and deterministic cross-frontend matrix, including exact target binding,
  early-boot/rootfs-only separation, path confinement, and hostile inputs.
* [x] Publish the standalone bounded payload-receipt schema-1 contract and
  deterministic cross-frontend matrix. Require the exact six role-specific
  records, recomputed canonical receipt identity, exact target binding, and
  mounted-root evidence verification before final-image trust.
* [x] Publish the standalone bounded initramfs-workspace schema-1 contract and
  deterministic cross-frontend matrix. Preserve target preparation states,
  enforce finite/dynamic/bind-target inode semantics, and bind successful
  mutation capacity to the validated initramfs reserve.
* [x] Verify the target Holo pacman database after mutation, including package
  records, ownership, dependency state, and database consistency.

## Schema, liveness, failure, and target-code trust

* [x] Formalize installer-result/progress schema evolution and mandatory versus
  additive schema-1 fields. The installer-result schema requires all structured
  post-install proofs, while the executable consumer validator enforces their
  target, package, and module-hash cross-record equality. OPEMOS.EXE still needs
  its own generated/fixture-tested Rust consumer before the broader schema item
  above can close.
* [ ] Add a real phase-by-phase failure/cancellation matrix for pacman hooks,
  userspace verification, module extraction/compression/copy/verification,
  GRUB, depmod, mkinitcpio, state writing, compression restoration, and
  recursive mount cleanup.
* [ ] Emit bounded heartbeats or safe subphase records during opaque pacman and
  mkinitcpio subprocesses without exposing paths, credentials, or unbounded
  output.
* [ ] Authenticate or allowlist target-owned pacman hooks and mkinitcpio code,
  or require a verified official-recovery-image attestation from the image
  builder before executing target-controlled programs.
  * [x] Provide a disabled-by-default serial controller, reviewed provenance
    manifest contract, bounded decompressor, read-only recovery inspection, and
    deterministic guest-local A/B fixture; no Valve checksum/signature is
    currently available to populate the real-media manifest honestly.

## Public online-installer trust and certification

* [ ] Replace production curl bootstrap from mutable `main` with an immutable,
  authenticated release, tag, or commit bootstrap.
* [ ] Require a signed or independently authenticated online release manifest
  binding archive, checksum, build info, provenance, userspace version, source
  commit, and certification identity.
* [ ] Define a machine-readable hardware-certification attestation bound to
  exact artifact hashes, tested GPUs, SteamOS/kernel versions, test date/result,
  and maintainer identity before assigning `certified-published`.

## Availability and authenticated recovery

* [ ] Define archival/recovery policy for historical Valve headers, reviewed
  userspace packages/signatures, minimal keyrings, provenance, and certified
  release assets; hash manifests alone are not backups.
  Detached-signature Valve header packages now have an atomic, bounded offline
  export/import format that revalidates the reviewed signer and exact keyring;
  Userspace/certified package sets now have an atomic bounded multi-artifact
  format binding every detached signature to the exact signer, reviewed keyring,
  policy, and provenance, plus concurrent Fedora/Arch verified-cache-only VM
  coverage. Long-term replicated archival ownership remains unresolved.
* [ ] Test GitHub, Valve, and Arch Linux Archive outages. Reuse only exact
  previously authenticated cache entries and otherwise return an actionable
  typed failure.
  * [x] Integrate verified userspace/certified bundle generations into the
    offline-root input selector with an explicit no-fallback source mode,
    exact target/policy/provenance/keyring/package binding, private snapshots,
    cache-ID result provenance, and Fedora/Arch offline-only guest coverage.
  * [x] Add concurrency-safe authenticated-cache retention with active installer
    leases, explicit protected generations, exact count/byte budgets, rollback
    on interruption, unsafe/partial-generation cleanup, and schema-1 decisions.

---

# Overall state

The project has crossed the bring-up threshold. SteamOS 3.8.16 has successfully
booted into Gaming Mode on the RTX 2060 with the known-good project NVIDIA
575.64.05 path and pristine upstream NVIDIA 580.119.02 as a control.

The infrastructure should now be actively dogfooded. The next major validation
gate is a completely clean-stock one-command certified installation.
