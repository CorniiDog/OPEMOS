# Abrupt update recovery acceptance (not executed)

Run only in a separately admitted disposable OMEN VM with its original immutable
base and retained per-run overlay. Never hard-stop the host, old R11/R14 guest,
or a user guest. Keep the current source, exact kernel/slot/receipt/trust inputs,
queue unit and PID/start identities, and all five target modules in the receipt.

The existing device generation regression exercises the real producer process,
then SIGKILL at AFTER_SEAL and AFTER_RENAME, and ordinary `check` twice. Both
must leave the prior active state and clear only the uncommitted candidate using
existing confined cleanup. The fsync observation test invokes actual OS fsync;
it is not cold-reboot evidence.

For cold-reboot acceptance, use the same development-only publication pause
hooks (`OPEMOS_GENERATION_TEST_PAUSE_AFTER_SEAL` and
`OPEMOS_GENERATION_TEST_PAUSE_AFTER_RENAME`). Retain the phase observation, then
stop exactly the admitted QEMU process without guest shutdown or updater cleanup.
Cold boot the SAME overlay, run ordinary generation `check` twice and validate
all active/LKG payload hashes, permissions, target compatibility and receipts.
Repeat with existing activation/state/health-intent pause hooks. Interrupt the
restart reconciliation itself, cold boot again and verify idempotence. Capture
actual QEMU/guest command, exit/wait/reap, raw boot logs, and filesystem evidence.
An atomic rename or SIGKILL success cannot stand in for this result.

This checkpoint does NOT implement power-loss recovery for bootstrap/install.sh
or install_to_root.sh. Their module replacement, depmod, initramfs, GRUB, hooks
and installation-state mutations still need a persistent per-target prestate and
reconciliation transaction, including retained boot assets and read-only mode.
Do not run abrupt mutation tests against those paths or claim whole-device safety
until that implementation and exact guest admission are separately reviewed.

## Installed artifact prototype checkpoint

`tests/installed_update_transaction.py` authors a real CLI producer/consumer
transaction on a private disposable root: SIGKILL after prepared intent, first
activation and pending health, interrupt rollback, then reconcile twice. It also
refuses a foreign changed artifact. No method under test is substituted. Run it
only inside an admitted OMEN VM. The prototype is development-only and cannot
operate on `/`; legacy opaque writers now refuse before target mutation.

This is not a working installed update producer: real package/hooks/depmod output,
complete staged boot images, target receipt/current authority, and health commit
still need to be joined. Existing files alone may be restored; absent artifacts
are refused instead of deleting unknown data. Original/candidate files and intent
are retained. A snapshot hash is not prior boot/driver health or signature proof.
Cold-reboot acceptance must use exact target/slot identity after reboot, validate
all five modules plus boot/GRUB/app artifacts, and retain the full owned closure.

## 2026-10-15 corrective source draft — NOT admitted or executed

The current draft separates durable `prepare` from `commit_prepared`: complete
old/new snapshots and the intent precede publishing `current.json`, which
precedes changing read-only mode. Startup restores the saved read-only policy,
refuses foreign bytes/modes, and reconciles partial activation/recovery.
The first distinct boot records an attempt; a subsequent unacknowledged boot
rolls back. Health requires that actual first boot, exact kernel/version,
selected on-disk hashes, and loaded nvidia/modeset/drm source identities. These
are authored checks, not measured boot or GPU evidence.

`tests/installed_update_transaction_normal.py` is a NEW ordinary real CLI
transaction and necessary changed fault cases. Run only in an admitted OMEN VM
as actual root. It executes actual prepare/activation/startup/recovery process
boundaries, genuine other-UID refusal, schema/hash refusal, repeated recovery,
foreign-data preservation, and same-boot health refusal. It uses a disposable
fixture root and therefore does NOT verify the production candidate producer.
No methods under test are substituted. Historical SIGKILL tests remain in Git
and on disk; they are not part of the new user-requested acceptance.

The production path is explicitly disabled pending the bundled source findings
in `docs/installed-update-corrective-source-audit.md`. Do not remove that gate
as a fixture accommodation. In particular the existing `install_to_root.sh`
gate, fallback producer, and guardian refresh remain incomplete. No normal
production producer-to-consumer result or complete writer set is claimed.

After source completion and independent full-graph review, the actual guest
acceptance must bind its externally admitted immutable inputs to:

* owning queue/grant/unit, exact VM ID and overlay/base lineage, all process
  start identities, current 10-CPU/32-GiB/no-swap pool reservation and literal
  60-GB per-VM envelope;
* immutable Core commit and all changed source bytes, exact authenticated
  materialization/archive/provenance/checksum/build-info, exact three Valve
  target identities and all five modules, current root/slot identity, existing
  installed policy/receipt and compatible previously verified boot artifacts;
* exact ordinary installed `bootstrap/install.sh --archive FILE --checksum FILE
  --cache DIRECTORY -y` producer, ordinary startup guardian and
  `bootstrap/recoveryctl.sh installed-health` consumer; complete raw stdout,
  stderr, commands, numeric exits, retained hashes/modes, read-only policy and
  actual process closure (not a local adapter or overridden caller).

First perform one normal update/new-boot/health transaction through those actual
functions. Then cover incompatible target/authority, unknown effect, full disk,
foreign artifact, unavailable loaded identity and corrupt retained candidate
without claiming these are compiler failures. Preserve initial failures.

For abrupt-off acceptance, expose only development observation pauses at durable
`prepared`, `activated:N`, `pending-health`, and `restore:N`. An independently
admitted controller may abruptly stop ONLY the exact exclusively owned QEMU VM,
then reap it and cold boot the SAME overlay. Never send a process crash signal
to the updater as a substitute for VM off/restart. Preserve each interrupted
intent/boot log. Run the ordinary startup consumer, interrupt a recovery once
by the same VM-off mechanism, restart again and reconcile twice. Validate
usable boot, all five selected module files, complete boot/config/GRUB/receipt
set, exact restored/acknowledged generation, source/app guardian integrity and
read-only policy. No display/GPU/hardware acceptance is inferred. Do not repeat
unchanged successful expensive stages. No admission, guest control, runtime or
compiler authority is conveyed by this document.

## Composed continuation: current VM procedure (authored, NOT executed)

The draft-disable statements above describe the preserved ad1db4f checkpoint.
The composed source now joins the installed and unpublished-offline profiles as
listed in `docs/installed-update-corrective-source-audit.md`. Source availability
is not runtime admission. Do not use old SIGKILL tests as this acceptance.

Freeze the final reviewed Core commit and independently pin every source byte.
The owning VM controller must bind queue/grant, guest UUID, immutable base and
same disposable overlay, all CPU/RAM/overhead/no-swap/60GB budgets, no conflicting
owner, actual process identities, raw serial/output and complete descendant
closure before and after each run. DMI checks in a guest are only an accidental
physical-host guard, never evidence of that grant. Reuse previous successful
stages only when their exact bytes and preconditions remain applicable.

Fill the strict schema consumed by `tests/vm/installed_update_acceptance.py`:
`schemaVersion=1`, unique32hex `runId`, actual `guestUUID`, reviewed40hex
`coreCommit`, exact `kernel` (one of the three Valve releases in the program),
`nvidia=575.64.05`, absolute `archive/checksum/provenance/cache/output` paths,
SHA256 for archive/checksum/provenance, `driverSourceCommit` equal to
4a616372ce3c1f1d0a10c04960107a1db51b6fc0, exact compressed-module `moduleSHA256`
map for all five names, and `installedCoreSHA256` map for the distributed managed
bootstrap/lib files. Cache is mandatory for cached-repair/fallback repair cases.
Output's final basename includes runId. Do not invent source/signature provenance,
copy a historical d8 product, fill dummy digests or call this manifest complete
until actual authenticated bytes and admission are present. Preserve every run.

Inside that admitted guest, invoke the real functions/processes, for example:

```
python3 tests/installed_update_transaction_normal.py
python3 tests/offline_generation_transaction.py --output EXACT_NEW_OUTPUT
python3 tests/recovery_readonly_consumer.py --output EXACT_NEW_OUTPUT
python3 tests/vm/installed_update_acceptance.py qualify --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py refuse-bad-checksum --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py refuse-corrupt-cache --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py update --manifest EXACT_MANIFEST
# Owning controller: ordinary reboot SAME overlay, retain commands/exits/closure.
python3 tests/vm/installed_update_acceptance.py after-boot --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py verify-readonly-status --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py enable-console-fallback --manifest EXACT_MANIFEST
# Owning controller: ordinary reboot SAME overlay.
python3 tests/vm/installed_update_acceptance.py verify-console-boot --manifest EXACT_MANIFEST
python3 tests/vm/installed_update_acceptance.py disable-console-fallback --manifest EXACT_MANIFEST
# Owning controller: ordinary reboot SAME overlay.
python3 tests/vm/installed_update_acceptance.py verify-disabled-fallback-boot --manifest EXACT_MANIFEST
```

Confirm the first two tests' argument interfaces before scheduling. They are
actual CLI/UID/marker regressions, not substitutes for real installed production.
The normal acceptance requires a genuinely changed driver archive; five modules
matching an unchanged baseline is insufficient. Capture normal startup and health
service ordering, current root/slot receipt, real permission/mode/size behavior,
read-only restoration and all retained files. Health proves file/loaded identities,
not GPU display output or game rendering. Desktop Rust checks/build also remain
VM-only and must validate pending-state presentation with the final exact bytes.

For each changed abrupt case, start from a separately retained qualified baseline,
new run/output identity and coherent manifest; never rearm an old job or overwrite
its logs. Run `update --fault-component app|driver --pause prepared|activated:0|pending-health`.
Retain the actual phase line. The external controller abruptly powers off ONLY
that exclusively owned VM, waits/reaps it, verifies complete closure, and cold
boots the SAME overlay. For partial activation run `verify-restored`; for fully
pending activation on first new boot run `after-boot`, then separately exercise a
second boot without health (health timer deliberately masked by an exact owned
VM test setting) and confirm rollback. Do not let the scheduled health race the
unacknowledged-second-boot scenario; record exact test setting and restoration.

Interrupt restore using `arm-recovery-pause --pause restore:0`, exact VM off/restart,
then `remove-recovery-pause` (exact owned drop-in only) and `verify-restored`, then `verify-restored --repeat-observation`
under disjoint output labels. For heal, after real positive health run
`corrupt-owned-module`, optionally arm `heal:0`, then exact VM off/restart; remove
pause, restart and run `verify-healed`, then `verify-healed --repeat-observation`. Its preserved foreign bytes must match
corruption evidence and final all-five hashes must match the selected generation.
Do not infer usable old modules merely from hashes: retain actual new boot,
userspace and loaded identity checks for restored NVIDIA state too.

Necessary additional production refusal cases use disposable test-created inputs
only: malformed/oversized cache, unsupported nested mount, unknown upper effect,
real destination ENOSPC within its quota, changed foreign shared GRUB, unavailable
loaded identity, changed authority/root and corrupt retained candidate. Preserve
initial failures; stop only affected integrity/lifecycle work. No fake adapters,
clock/sysfs overrides or method replacement can establish OS behavior. Full image
acceptance separately invokes the actual existing `install_to_root.sh` and guardian
image installer with authenticated inputs and independent final-image validation;
the pending-marker test alone does not establish full image success or discard
permission. Controller teardown/rollback/closure and hardware remain separate.

## Changed independent-review cases F1–F6 (NOT executed)

The installed profile now refuses missing target directories: start from the
existing authenticated offline-provisioned guardian layout. This source checkpoint
neither creates a new VM nor supplies live directory migration authority.
Before `update`, run `install-guardian` (actual ordinary installer, returns75 for
pending app), then `refuse-before-app-health` and verify driver files unchanged.
The external owner reboots the same overlay; run `guardian-after-boot` to record
actual permanent app health. Only then run `update` and the driver reboot/health
sequence above. For app abrupt cases invoke `install-guardian --pause ...` on its
qualified old-source baseline, not an already acknowledged idempotent app.

Run `python3 tests/guardian_immutable_inputs.py --output EXACT_NEW_OUTPUT` in the
admitted VM: real clean Git producer/freeze succeeds, each edited template or
engine is refused with retained stderr despite unchanged HEAD. Full ordinary app
installation still supplies the producer-to-systemd transaction proof.
Run `refuse-directory-mode-change` before the positive driver update: the actual
original module directory0700 must remain0700 while candidate0755 recreation is
refused, all five actual module files unchanged; exact test-owned mode is restored.
The raw-plan CLI test additionally refuses driver control-plane paths and absent
parent directories before creating an intent or live directory. Actual confined
hook-side unit/dispatcher writes must be refused by collection as well; retain that
candidate and initial failure, not a mocked collect result.

For F6 use a separately retained acknowledged console baseline without an existing
retry journal. `repair-verified-fallback` authors a real matching retry then calls
ordinary `repair-auto`; require75, selected generation pending, retry not restored,
progress not succeeded. After the same-overlay actual reboot invoke
`verify-disabled-fallback-boot`; inspect retry restored and progress success only
following actual replacement health. Preserve existing journals rather than rearm
or replace them. Do not repeat the earlier direct-disable stage unchanged.

F5 release failure is an actual network/control fault, not a fake curl executable.
The separately admitted owner must prepare current authenticated checkout/cache,
then remove ONLY that test guest's admitted network before ordinary discovery and
retain raw command/exit. Test exact retained cache, no cache and inherited unrelated
BUILD_OUT. Normal release discovery is still required once with genuine inputs;
source syntax and retained selector tests are not end-to-end network evidence.
