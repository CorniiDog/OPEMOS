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
