Below is the consolidated project checklist based on our work so far. I’m treating the current repository state and the latest tests as authoritative where they supersede earlier failures.

# SteamOS NVIDIA Open Kernel Module Support — Master Checklist


## Current project phase

**Status: development / active dogfooding**

The support infrastructure is now mature enough to use for real NVIDIA
development work on the primary SteamOS test system. Broad installer construction
is no longer the main task. The project should now be exercised through the same
public workflows intended for future users while the remaining validation gates
are completed.

### Milestone ladder

* [x] Development infrastructure is usable for active dogfooding.
* [ ] **Alpha:** complete a clean-stock SteamOS one-command certified install.
* [ ] **Beta:** verify install, idempotency, uninstall/reinstall, SteamOS update,
  and rollback behavior.
* [ ] **Release candidate:** verify a patched NVIDIA release across repeated
  clean installs and additional NVIDIA hardware.
* [ ] **Stable:** establish reliable SteamOS upgrade/recovery behavior with no
  manual shell repair required during supported workflows.

### Current priority queue

The image-builder integration now has a versioned offline-target JSON resolver
in `lib/resolve_target.py`. The Fedora appliance can supply identity discovered
from a mounted recovery image without confusing the guest kernel or OS for the
installation target.

An x86_64 Fedora appliance can also build a missing exact-kernel artifact with
`bootstrap/build_for_target.sh`. Real Valve-header discovery and compilation for
the recovery image remain integration-validation items for the image builder.

1. [ ] Re-test the published NVIDIA 575.64.05 certified release through the
   current public online installer.
2. [ ] Run the certified installer a second time and verify the idempotent fast
   path performs no module replacement, initramfs rebuild, or reboot prompt.
3. [ ] Test uninstall followed by a clean certified reinstall.
4. [ ] Reboot and verify the 575 runtime with `nvidia-smi`, `modinfo`,
   `/proc/driver/nvidia/version`, Gamescope, Xwayland, Steam, and Gaming Mode.
5. [ ] Perform a completely clean-stock SteamOS installation using only the
   intended public one-line workflow.
6. [ ] Test behavior across a SteamOS/kernel update and rollback.
7. [ ] Use the project itself for the NVIDIA 580 patch-development cycle instead
   of manually copying or installing modules.
8. [ ] Reproduce the NVIDIA 580 Gamescope graphical bug consistently.
9. [ ] Create or refresh `nvidia/580.119.02` from exact NVIDIA upstream.
10. [ ] Develop the smallest possible 580 compatibility patch and compare it
    directly against the pristine upstream control.

---

## 1. Core project architecture

* [x] Keep the support/build/install tooling in:

  * `CorniiDog/open-gpu-kernel-modules-steamos-support`
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
* [x] Verify Gamescope/Xwayland actually use the RTX 2060.
* [x] Verify Gaming Mode works with the project modules.
* [x] Verify no NVIDIA Xid failures in that working state.
* [x] Publish known-good 575 release:

  * `steamos-3.8.16-nvidia-575.64.05-k6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45`
* [x] Preserve backward compatibility with that release format.
* [ ] Re-test the 575 production release after all installer changes.
* [x] Verify the installer accepts and normalizes the old raw-`.ko` 575 release format.
* [ ] Verify idempotency against the old 575 release.

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
* [x] Verify Gamescope is using the RTX 2060.
* [x] Verify Xwayland/Steam/Mangoapp processes use the GPU.
* [x] Verify installed path:

  * `/lib/modules/.../updates/open-gpu-kernel-modules-steamos/nvidia.ko.zst`
* [x] Verify no NVIDIA `NVRM: Xid` faults.
* [x] Distinguish the Realtek `XID 541` line from NVIDIA Xid faults.
* [x] Establish 580.119.02 pristine upstream as the control case.
* [x] Accept that graphics bugs may still exist in this control case.
* [x] Preserve a source-identifiable 580 pristine build artifact for regression testing.
* [ ] Add automated comparison between pristine-upstream and project-patched 580 builds.

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
* [ ] Decide whether live userspace replacement remains the permanent development model or whether an isolated development userspace is ever worth implementing.

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
* [ ] Decide long-term cleanup/retention policy for old backups.
* [ ] Possibly cap number/age of retained backup generations.
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
* [ ] Decide whether release archives themselves remain raw `.ko` or become compressed.

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
* [ ] Add a test for deliberately insufficient root space.
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
* [ ] Revisit `pacman -Sy --needed --noconfirm podman` partial-upgrade risk.
* [ ] Potentially avoid host package mutation entirely if a safer SteamOS-compatible approach exists.
* [ ] Cache header package efficiently inside container/build storage.
* [ ] Reduce giant Fedora dependency-install verbosity if desired.
* [x] Fix grep warning:

  * `grep: warning: stray \ before "`
* [ ] Audit Valve repository discovery regex.
* [ ] Cache build environment so repeated builds do not repeatedly install hundreds of packages unnecessarily.

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
* [ ] Audit duplicate/environment setup logic between callers and `build.sh`.
* [ ] Decide whether `build.sh` itself or callers own environment preparation.
* [ ] Ensure headers are reused between builds where safe.
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
* [ ] Consider fsync/durability if you want stronger transactional guarantees.

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
* [ ] Test each script from a shell where the repo is not already sourced.
* [ ] Test scripts via their intended remote/raw invocation, not only from the local checkout.

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
* [ ] Audit all `/opt` development behavior and clarify its ownership.
* [ ] Explicitly document what `--development` places under `/opt`, if applicable.
* [ ] Verify `--development` never silently installs pristine upstream modules.
* [ ] Verify normal mode never silently falls back to upstream.
* [ ] Verify userspace downgrade/upgrade behavior.
* [ ] Verify partially installed NVIDIA userspace recovery.

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
NVIDIA userspace setup. This is the main remaining infrastructure asymmetry.

* [ ] Record the currently installed `nvidia-utils` and `lib32-nvidia-utils`
  package versions before changing them.
* [ ] Preserve project-managed modprobe configuration before replacement.
* [ ] Preserve relevant GRUB configuration before modification.
* [ ] Define rollback behavior if `pacman -U` succeeds but a later configuration
  step fails.
* [ ] Define rollback behavior if only one NVIDIA userspace package changes.
* [ ] Restore previous configuration when userspace setup aborts after modifying
  system files.
* [ ] Add mocked or fake-root failure coverage for userspace setup where practical.
* [ ] Verify every userspace failure path restores SteamOS read-only state.
* [ ] Test userspace downgrade and upgrade behavior explicitly.
* [ ] Test broken or partially installed NVIDIA userspace recovery.

---

# 16. Sudo/password UX

* [x] Add early:

  * `sudo -v`
  * to upstream install workflow.
* [x] Explain that no password prompt can occur if sudo credential timestamp is already cached.
* [ ] Decide whether top-level `setup_nvidia.sh` should request privileges immediately.
* [ ] Decide whether long builds need sudo timestamp keepalive.
* [ ] Avoid nested scripts prompting repeatedly.
* [ ] Keep privilege boundaries clear.
* [ ] Test from a cold sudo timestamp.
* [ ] Test after sudo timestamp expires during a long build.

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
* [ ] Verify fallback resolves correctly after 580 project modules.
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
* [ ] Test cleanup path if script receives SIGINT.
* [x] Test cleanup paths for injected install/uninstall failures in a fake root.
* [ ] Test cleanup path on checksum failure.
* [x] Test cleanup path after fake-root target replacement.
* [ ] Ensure every script that disables readonly reliably restores it.

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
* [ ] Ensure `support_commit` is populated rather than `unknown` during normal builds.
* [ ] Add build container digest to all relevant BUILD-INFO variants if not already consistent.
* [x] Add `schema_version=1` to newly generated BUILD-INFO files.
* [ ] Consider module SHA entries in BUILD-INFO.
* [ ] Consider explicit patch-series identifier.
* [ ] Consider `gamescope` version/build metadata for reproducibility.

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
* [ ] Make pristine 580 build bit-for-bit reproducible where feasible.
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
* [ ] Expand the release-policy fixture with additional explicit SteamOS patch cases such as:

  * 3.8.15
  * 3.8.16
  * 3.8.17
  * 3.8.18
* [ ] Ensure no old-kernel artifact can be installed after SteamOS kernel update.
* [ ] Improve resolver diagnostics when no compatible release exists.

---

# 23. SteamOS updates / persistence

This is still a major future area.

* [ ] Determine what happens to project modules after an A/B SteamOS update.
* [ ] Detect new active SteamOS slot/kernel.
* [ ] Detect that existing module release targets old kernel.
* [ ] Select/build/install exact matching module for new kernel.
* [ ] Never copy old-kernel module into new kernel tree.
* [ ] Determine proper SteamOS update hook mechanism.
* [ ] Avoid polling if a reliable lifecycle hook exists.
* [ ] Preserve NVIDIA userspace compatibility across updates.
* [ ] Preserve project configuration across A/B slot switches.
* [ ] Test upgrade from one 3.8.x kernel to another.
* [ ] Test rollback to previous SteamOS slot.
* [ ] Test missing release for new kernel.
* [ ] Provide safe fallback behavior if no compatible project build exists.

---

# 24. Fresh-stock installation

This still needs an end-to-end test.

* [ ] Start from stock SteamOS with no project files.
* [ ] Start from stock SteamOS NVIDIA-incompatible/unsupported state.
* [ ] Run intended one-line online installer.
* [ ] Resolve correct certified release.
* [ ] Install correct NVIDIA userspace.
* [ ] Install exact matching project modules.
* [ ] Rebuild initramfs.
* [ ] Reboot once.
* [ ] Boot directly into Gamescope.
* [ ] Verify `nvidia-smi`.
* [ ] Verify `modinfo`.
* [ ] Verify `/proc/driver/nvidia/version`.
* [ ] Verify Gamescope uses the GPU.
* [ ] Verify desktop mode.
* [ ] Verify Steam Gaming Mode.
* [ ] Verify no manual recovery steps required.
* [ ] Verify installer can be run a second time and returns idempotent success.
* [ ] Verify uninstall restores a usable fallback.

---

# 25. NVIDIA userspace exact-version policy

* [x] Enforce userspace/kernel version alignment.
* [x] Upstream 580 module install required userspace 580.119.02.
* [x] Production 575 release resolves userspace 575.64.05.
* [x] Test mismatch detection intentionally with a 575 module archive and installed 580 userspace.
* [ ] Test installed userspace newer than project module.
* [ ] Test installed userspace older than project module.
* [ ] Test broken/incomplete `nvidia-utils`.
* [ ] Make error messages explain required remediation.

---

# 26. Actual Gamescope / NVIDIA graphics problem — ACTIVE

This is the ultimate reason for the project. The support infrastructure is now
mature enough that this work should proceed through the project workflows rather
than waiting for every infrastructure TODO to be completed.

## Already explored

* [x] Establish that NVIDIA SteamOS Gaming Mode can function at all.
* [x] Establish known-good 575 state.
* [x] Establish pristine 580 control state.
* [x] Test Gamescope/NVIDIA-related session changes experimentally.
* [x] Experiment with:

  * `--generate-drm-mode fixed`
* [x] Experiment separately with:

  * `--disable-color-management`
* [x] Inspect DRM connector state with `modetest`.
* [x] Observe `DP-1` disconnected in relevant test.
* [x] Distinguish basic driver bring-up from actual rendering/artifact bugs.

## Next graphics debugging work

* [ ] Reproduce the specific 580 graphical bug consistently.
* [ ] Use the public/development project workflows for 580 experiments instead of manual module copying.
* [ ] Record the exact project support commit used for each graphics experiment.
* [ ] Record the exact NVIDIA source commit used for each graphics experiment.
* [ ] Define exact visual symptom.
* [ ] Define exact startup sequence that triggers it.
* [ ] Capture Gamescope logs.
* [ ] Capture kernel NVIDIA/DRM logs.
* [ ] Capture Xwayland logs if relevant.
* [ ] Compare 575 working versus 580 broken.
* [ ] Compare NVIDIA module source deltas between 575 and 580.
* [ ] Determine whether issue is:

  * atomic modesetting,
  * explicit sync,
  * DRM leases,
  * color management,
  * HDR,
  * VRR,
  * modifier negotiation,
  * plane selection,
  * direct scanout,
  * cursor plane,
  * framebuffer,
  * PRIME,
  * Wayland/Xwayland synchronization,
  * Gamescope assumptions.
* [ ] Identify the smallest reproducible code path.
* [ ] Patch NVIDIA source branch for 580.
* [ ] Build patched 580 modules.
* [ ] Install patched modules using the hardened installer.
* [ ] Reboot and compare against pristine upstream.
* [ ] Keep only changes that affect the target bug.
* [ ] Bisect if necessary.
* [ ] Create clean patch commits.
* [ ] Document rationale for each patch.
* [ ] Publish patched 580 project release once stable.
* [ ] Verify project patches do not regress known-good behavior.

---

# 27. Gamescope project integration

* [x] Use `gamescope-nvidia` as a reference project for lifecycle/idempotency patterns.
* [x] Inspect Gamescope source for NVIDIA-specific/debug behavior.
* [x] Locate `g_bDebugLayers`.
* [ ] Decide whether NVIDIA kernel-module project needs any Gamescope patch at all.
* [ ] Prefer fixing the correct layer rather than permanently carrying unrelated Gamescope hacks.
* [ ] If Gamescope patch is necessary, isolate it as separate project/release concern.
* [ ] Record Gamescope commit/version used for every successful test.
* [ ] Test patched NVIDIA modules against stock Gamescope.
* [ ] Test patched Gamescope against pristine NVIDIA modules to separate causality.

---

# 28. Source branch strategy for 580

* [ ] Create/confirm project source branch:

  * likely `nvidia/580.119.02`
* [ ] Base it exactly on NVIDIA upstream 580.119.02 tag/commit.
* [ ] Preserve pristine base commit.
* [ ] Add project fixes as individual commits.
* [ ] Avoid squashing away useful bug-history while developing.
* [ ] Keep release patch set easy to rebase to future NVIDIA versions.
* [ ] Add source metadata connecting support release → source commit.
* [ ] Eventually test next 580.x release against the same fixes.

---

# 29. CI / automated checks

* [x] Run `bash -n` on all shell scripts locally and in CI.
* [x] Run `git diff --check` locally and in CI.
* [x] Run error-level ShellCheck in CI.
* [ ] Test resolver parsing.
* [ ] Test release-tag parsing.
* [ ] Test archive traversal rejection.
* [ ] Test checksum rejection.
* [ ] Test BUILD-INFO parsing.
* [ ] Test raw `.ko` idempotency.
* [ ] Test `.ko.zst` idempotency.
* [ ] Test empty modules directory rejection.
* [ ] Test wrong kernel rejection.
* [ ] Test wrong NVIDIA version rejection.
* [ ] Test wrong SteamOS version rejection.
* [ ] Test fuzzy selection.
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
* [ ] Potentially mock `modinfo`, `nvidia-smi`, GitHub APIs, etc. for non-destructive tests.

---

# 30. CLI / self-documentation

* [x] Recognize current commands were insufficiently self-commenting.
* [x] Rename `--driver` to `--development`.
* [x] Add proper `usage()` to `install_upstream.sh`.
* [x] Make `--help` work everywhere.
* [ ] Standardize formatting among scripts.
* [ ] Every mode should explain:

  * purpose,
  * userspace behavior,
  * module behavior,
  * source provider,
  * whether project fixes are applied.
* [x] Add examples in README for:

  * certified install
  * development selection
  * pristine upstream baseline
  * build-only
  * local artifact install
  * in-code build/install.
* [x] Make destructive operations visibly distinct from resolution/build-only operations.
* [ ] Use consistent terminology:

  * certified
  * development
  * upstream-development
  * project-patched.

---

# 31. Code cleanup

* [x] Centralize project temp helpers where usable.
* [ ] Consider common helper for bootstrap cache-root creation.
* [ ] Avoid duplicating hardcoded project cache path in four online scripts, while still respecting pre-bootstrap limitations.
* [ ] Audit redundant `mkdir -p "${HOME}/.cache/${PROJECT_NAME}"` calls now helpers do it.
* [x] Audit duplicate `setup_build_env.sh` invocation.
* [ ] Audit unused variables.
* [x] Audit stale `DRIVER_*` names after `--development` rename.
* [ ] Audit comments referring to old mode names.
* [x] Audit README for `--driver`.
* [ ] Audit release/action scripts for old naming.
* [x] Audit all direct `/tmp` usages and classify:

  * host `/tmp` → generally avoid for large work.
  * container `/tmp` → allowed/intended.
* [x] Add comments where this distinction matters.

---

# 32. Git / commits

Completed meaningful commit groups include work around:

* [x] Automatic SteamOS kernel build environment.
* [x] Installer storage hardening.
* [x] `/home` cache staging.
* [x] Module compression.
* [x] Root-space preflight.
* [x] Upstream-development workflow.
* [x] Development-mode rename.
* [x] Temp helper infrastructure.

Still to commit as a clean logical batch after testing:

* [ ] `.ko.zst`-aware online idempotency.
* [ ] Bootstrap temp-order fixes.
* [ ] `install_upstream.sh --build-only`.
* [ ] Duplicate build-env call removal.
* [ ] Container `/tmp` correction.
* [ ] Any resulting self-documentation/comments.

Before that commit:

```bash
for f in bootstrap/*.sh lib/*.sh; do
    bash -n "$f" || exit 1
done

git diff --check
```

---

# 33. Offline SteamOS image-builder integration

## Responsibility boundary

The support repository owns exact Valve header acquisition/authentication,
NVIDIA source selection, exact-target compilation, module validation, archive
format, machine-readable results, provenance, Fedora transaction tests, build
cleanup, and eventual certified-artifact publication.

The image builder owns recovery-image inspection, active boot-kernel selection,
Valve A/B and installer-layout handling, appliance lifecycle, progress and
cancellation UX, artifact caching, offline image injection, matching NVIDIA
userspace/GSP firmware, offline `depmod`, target initramfs work, final-image
validation, and preservation of the original input.

The patched NVIDIA source repository owns versioned `nvidia/<version>` branches,
SteamOS-specific patches, and an unambiguous driver-version-to-source-commit
mapping. It must never silently fall back to pristine upstream.

Maintainers/CI own real NVIDIA hardware boot tests, certification/publication,
trusted Valve key management, SteamOS update testing, and Secure Boot/module
signing policy.

## Completed support-side contract

* [x] Add versioned offline-target JSON resolution in `lib/resolve_target.py`.
* [x] Accept target SteamOS version, exact kernel, and ELF architecture rather
  than inspecting the Fedora appliance host identity.
* [x] Apply the normal bounded same-series SteamOS certification fallback while
  still requiring an exact kernel match.
* [x] Treat no compatible artifact as a normal fail-closed resolution result.
* [x] Require both the expected release archive and SHA256 sidecar to be
  advertised before returning a compatible published artifact.
* [x] Add native x86_64 Fedora exact-target compilation in
  `bootstrap/build_for_target.sh`.
* [x] Derive the exact Neptune headers filename from the full target kernel.
* [x] Support automatic Valve-repository discovery, an exact Valve URL, or a
  pinned local headers package.
* [x] Require the exact target kernel build-tree path; do not accept the first
  unrelated `/usr/lib/modules/*/build` directory.
* [x] Validate Arch `.PKGINFO` package name, version, and architecture.
* [x] Reject unsafe header-archive paths.
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
* [ ] Confirm the existing installer accepts the generated archive unchanged.
* [x] Preserve the complete build log and generated build metadata from this
  first integration run.
* [ ] Correct the support contract before image injection if any of these gates
  fail; do not approximate headers, source, kernel, or NVIDIA versions.

## Authentication and trust

* [x] Add Valve package detached-signature verification using a caller-supplied,
  reviewed keyring or equally strong trusted checksum provenance.
* [x] Record signing/primary fingerprint and signature verification result.
* [ ] Do not describe a post-download SHA256 calculated from the same transport
  as authentication.
* [x] Pin the expected Valve package origin and reject redirects to untrusted
  origins.
* [x] Pin the reviewed `holo-keyring` package hash, extracted keyring hash, and
  exact historical-header signer fingerprint in a versioned trust manifest.
* [ ] Define key rotation and revocation handling beyond explicit reviewed
  trust-manifest updates.
* [x] Keep unsigned-header or override builds labeled
  `development-unverified`.
* [x] Use `locally-built-verified` only for authenticated exact headers, clean
  pinned source, complete provenance, and all structural checks.
* [ ] Reserve `certified-published` for maintainer-published artifacts that have
  also passed the required hardware test matrix.
* [ ] Never silently promote a local successful compilation to certified.

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
* [ ] Separate concise user-facing messages from detailed maintainer diagnostics.

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
* [ ] Detect and remove abandoned inactive build sessions on the next run.

## Fedora/Linux validation

* [x] Run `tests/check.sh` under the same Fedora appliance used for builds.
* [x] Run the complete fake-root install/uninstall transaction suite under
  Fedora Bash 5; no transaction-test skips are permitted for release validation.
* [x] Add fixtures for wrong `.PKGINFO`, wrong full kernel directory, missing
  prepared-tree files, unsafe archive paths, and extraction-root escape.
* [x] Add file-backed fixtures for duplicate/missing modules, metadata-command
  failures, wrong NVIDIA version, wrong ELF architecture, and wrong vermagic.
* [ ] Test network failure, truncated downloads, cancellation during download,
  cancellation during compilation, and output-directory exhaustion.
* [ ] Test both automatically downloaded and pinned-local header inputs.
* [ ] Verify artifact installation, idempotency, uninstall, and rollback against
  a fake mounted target before modifying a real recovery-image working copy.

## Known cross-project edge cases

* [ ] Image builder must determine the actual boot kernel when multiple module
  trees exist; support-side compilation must receive one explicit target.
* [x] Image builder can launch an x86_64 TCG build appliance on Apple Silicon;
  the first complete build succeeded in 30m15s.
* [ ] Treat QEMU x86_64 software-emulation slowness as progress/UX concern, not
  permission to weaken compatibility checks.
* [ ] Image builder must install exactly matching NVIDIA userspace and GSP
  firmware; successful `.ko` compilation alone is insufficient.
* [ ] Image builder must handle offline `depmod`, the correct target initramfs,
  Valve A/B slots, installer-copy behavior, and first-boot ordering.
* [ ] Define Secure Boot/module-signing behavior before claiming supported
  Secure Boot installations.
* [ ] Verify SteamOS updates and slot changes do not silently leave stale modules.
* [ ] Preserve a recovery route if NVIDIA initialization fails on first hardware
  boot.

---

# 34. Immediate next actions

The project has moved from infrastructure bring-up into active dogfooding and
compatibility development.

1. [ ] Exercise the real published 575 certified path with the current online installer.
2. [ ] Verify the second run reaches the real idempotent fast path.
3. [ ] Test live uninstall and certified reinstall.
4. [ ] Verify the rebooted 575 Gaming Mode runtime.
5. [ ] Complete a clean-stock one-command SteamOS installation to reach **Alpha**.
6. [ ] Test SteamOS/kernel update and rollback behavior to progress toward **Beta**.
7. [ ] Use the project workflows for all new 580 module experiments.
8. [ ] Reproduce and characterize the remaining 580 Gamescope graphical bug.
9. [ ] Create or refresh `nvidia/580.119.02` from the exact upstream base.
10. [ ] Develop, build, install, and compare the smallest possible compatibility patch.
11. [ ] Publish a 580 project release only after the improvement is repeatable.
12. [ ] Expand testing to additional NVIDIA hardware before treating the project as stable.

## Non-blocking cleanup

These items are worthwhile but should not delay dogfooding or the 580 graphics
investigation:

* [ ] Decide backup retention policy.
* [ ] Decide whether release archives should remain raw `.ko` or become `.ko.zst`.
* [ ] Improve Fedora dependency/header caching.
* [ ] Reduce Fedora build dependency-install verbosity.
* [ ] Revisit safe Podman installation when Podman is absent.
* [ ] Continue CLI/style cleanup where useful.
* [ ] Pursue stronger bit-for-bit reproducibility only if it becomes operationally useful.

## Overall state

The project has crossed the bring-up threshold. SteamOS 3.8.16 has successfully
booted and run Gamescope on the RTX 2060 with both the known-good project
NVIDIA 575.64.05 path and pristine upstream NVIDIA 580.119.02 as a control.

The infrastructure should now be actively dogfooded. The next major validation
gate is a completely clean-stock one-command certified installation. In parallel,
the project can now be used for the actual NVIDIA 580 / Gamescope compatibility
work that motivated it.
