#!/usr/bin/env python3
"""Installed-system recovery status and fail-closed profile tests."""

import json
import fcntl
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "lib/recovery_status.py"
CONTROL = ROOT / "bootstrap/recoveryctl.sh"
GRUB = ROOT / "lib/update_recovery_grub_args.py"
TRANSACTION = ROOT / "lib/recovery_transaction.py"
PLAN = ROOT / "lib/recovery_release_plan.py"
STAGE = ROOT / "bootstrap/install_recovery_guardian_to_root.sh"
OPEN_CONTRACT = ROOT / "lib/open_opemos_contract.py"
PATH_VALIDATOR = ROOT / "lib/validate_recovery_install_path.py"
KERNEL = "6.16.12-valve24.5-1-neptune-616-test"
NVIDIA = "575.64.05"


def run(root, path):
    result = subprocess.run(
        ["python3", str(STATUS), "--root", str(root), "--kernel", KERNEL],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{path}:{os.environ['PATH']}"}, check=False,
    )
    return result.returncode, json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="opemos-recovery-") as temporary:
    root = Path(temporary) / "root"
    modules = root / "usr/lib/modules" / KERNEL / "updates/opemos"
    state = root / "var/lib/open-gpu-kernel-modules-steamos-support"
    mockbin = Path(temporary) / "bin"
    modules.mkdir(parents=True)
    state.mkdir(parents=True)
    mockbin.mkdir()
    (state / "installed-nvidia.txt").write_text(NVIDIA + "\n")
    mock = mockbin / "modinfo"
    mock.write_text(f'''#!/bin/sh
case "$2" in
  vermagic) printf '%s SMP preempt mod_unload\\n' '{KERNEL}' ;;
  version) printf '%s\\n' '{NVIDIA}' ;;
  *) exit 1 ;;
esac
''')
    mock.chmod(0o755)

    code, document = run(root, mockbin)
    assert code == 0
    assert document["status"] == "recovery-required"
    assert document["reason"] == "missing_exact_modules"
    assert document["fallback"]["automaticProfile"] == "console"
    assert document["fallback"]["nouveauAutomatic"] is False
    assert "repair-exact-kernel" in document["actions"]

    names = ("nvidia", "nvidia-drm", "nvidia-modeset", "nvidia-uvm", "nvidia-peermem")
    for name in names:
        (modules / f"{name}.ko.zst").write_text("fixture\n")
    code, document = run(root, mockbin)
    assert code == 0
    assert document["status"] == "healthy"
    assert document["reason"] == "exact_nvidia_ready"
    assert len(document["moduleVerification"]["records"]) == 5
    assert all(item["exactKernel"] and item["exactUserspace"]
               for item in document["moduleVerification"]["records"])

    recovery = state / "recovery"
    recovery.mkdir()
    (recovery / "state.json").write_text(
        '{"schemaVersion":1,"active":true,"profile":"console"}\n'
    )
    _, document = run(root, mockbin)
    assert document["status"] == "fallback-active"
    assert document["actions"] == ["disable-fallback"]

    (recovery / "state.json").write_text("not-json\n")
    code, document = run(root, mockbin)
    assert code == 2
    assert document["status"] == "unknown"
    assert document["reason"] == "inspection_failed"

control = CONTROL.read_text(encoding="utf-8")
assert "--allow-nouveau" in control
assert "Nouveau requires --allow-nouveau" in control
assert "moduleVerification" in control
assert "SUPPORT_REVISION" in control
assert "rollback-plan" in control
assert "set-default multi-user.target" not in control  # selected through the profile expression
assert "acquire_recovery_operation_lock" in control
assert 'exec 8>"$lock_file"' in control
assert 'flock -n 8' in control

with tempfile.TemporaryDirectory(prefix="opemos-recovery-grub-") as temporary:
    grub = Path(temporary) / "grub"
    grub.write_text(
        'GRUB_CMDLINE_LINUX_DEFAULT="quiet rd.driver.blacklist=nouveau '
        'modprobe.blacklist=nouveau nvidia-drm.modeset=1 nvidia-drm.fbdev=1 splash"\n'
    )
    subprocess.run(["python3", str(GRUB), "--config", str(grub)], check=True)
    result = grub.read_text()
    assert "blacklist=nouveau" not in result
    assert "nvidia-drm" not in result
    assert "quiet" in result and "splash" in result

with tempfile.TemporaryDirectory(prefix="opemos-recovery-transaction-") as temporary:
    state = Path(temporary) / "transaction.json"
    base = [sys.executable, str(TRANSACTION)]
    begin = subprocess.run(base + [
        "begin", "--state", str(state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "a" * 40,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ], text=True, stdout=subprocess.PIPE, check=True)
    document = json.loads(begin.stdout)
    assert document["automaticRetry"] is True and document["attempt"] == 0
    stat_mode = state.stat().st_mode & 0o777
    assert stat_mode == 0o600
    original = state.read_bytes()
    duplicate_begin = subprocess.run(base + [
        "begin", "--state", str(state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "a" * 40,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert duplicate_begin.returncode != 0
    assert state.read_bytes() == original
    invalid_transition = subprocess.run(base + [
        "set", "--state", str(state), "--phase", "restored",
        "--reason", "exact_nvidia_restored",
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert invalid_transition.returncode != 0
    assert state.read_bytes() == original
    scheduled = subprocess.run(base + [
        "set", "--state", str(state), "--phase", "retry_scheduled",
        "--reason", "network_unavailable_or_untrusted",
    ], text=True, stdout=subprocess.PIPE, check=True)
    assert json.loads(scheduled.stdout)["attempt"] == 1
    cancelled = subprocess.run(base + ["cancel", "--state", str(state)],
                               text=True, stdout=subprocess.PIPE, check=True)
    document = json.loads(cancelled.stdout)
    assert document["phase"] == "cancelled"
    assert document["active"] is False and document["automaticRetry"] is False
    repeated = subprocess.run(base + ["cancel", "--state", str(state)],
                              text=True, stdout=subprocess.PIPE, check=True)
    assert json.loads(repeated.stdout) == document
    terminal_set = subprocess.run(base + [
        "set", "--state", str(state), "--phase", "downloading",
        "--reason", "exact_artifact_resolution",
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert terminal_set.returncode != 0

    canonical = state.read_text(encoding="utf-8")
    parsed = json.loads(canonical)
    parsed["unexpected"] = True
    state.write_text(json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n")
    state.chmod(0o600)
    extra = subprocess.run(base + ["show", "--state", str(state)],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert extra.returncode != 0

    state.write_text(canonical.replace('"active":false', '"active":false,"active":false'))
    state.chmod(0o600)
    duplicate = subprocess.run(base + ["show", "--state", str(state)],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert duplicate.returncode != 0
    assert "duplicate JSON key" in duplicate.stderr

    state.write_text(json.dumps(document, indent=2) + "\n")
    state.chmod(0o600)
    noncanonical = subprocess.run(base + ["show", "--state", str(state)],
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert noncanonical.returncode != 0
    assert "canonical JSON" in noncanonical.stderr

    state.write_text(canonical)
    state.chmod(0o644)
    unsafe_mode = subprocess.run(base + ["show", "--state", str(state)],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_mode.returncode != 0
    state.chmod(0o600)

    hardlink = Path(temporary) / "transaction-copy.json"
    os.link(state, hardlink)
    linked = subprocess.run(base + ["show", "--state", str(state)],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert linked.returncode != 0
    hardlink.unlink()

    lock = Path(temporary) / ".transaction.json.lock"
    lock.touch(mode=0o600, exist_ok=True)
    lock.chmod(0o600)
    with lock.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contended = subprocess.run(base + ["show", "--state", str(state)],
                                   text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        assert contended.returncode != 0
        assert "another recovery transaction operation" in contended.stderr

    success_state = Path(temporary) / "successful.json"
    subprocess.run(base + [
        "begin", "--state", str(success_state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "b" * 40,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ], stdout=subprocess.PIPE, check=True)
    route = (
        ("downloading", "exact_artifact_resolution"),
        ("installing", "canonical_exact_kernel_install"),
        ("verifying", "exact_module_verification"),
        ("restored", "exact_nvidia_restored"),
    )
    for phase, reason in route:
        completed = subprocess.run(base + [
            "set", "--state", str(success_state), "--phase", phase,
            "--reason", reason,
        ], text=True, stdout=subprocess.PIPE, check=True)
    successful = json.loads(completed.stdout)
    assert successful["phase"] == "restored" and successful["active"] is False
    assert successful["attempt"] == len(route)

    hostile_state = Path(temporary) / "hostile.json"
    hostile_state.write_text('{"schemaVersion":NaN}\n')
    hostile_state.chmod(0o600)
    nonfinite = subprocess.run(base + ["show", "--state", str(hostile_state)],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert nonfinite.returncode != 0
    assert "non-finite" in nonfinite.stderr
    hostile_state.write_bytes(b"{" + b" " * (64 * 1024) + b"}\n")
    hostile_state.chmod(0o600)
    oversized = subprocess.run(base + ["show", "--state", str(hostile_state)],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert oversized.returncode != 0
    assert "excessive" in oversized.stderr

with tempfile.TemporaryDirectory(prefix="opemos-recovery-plan-") as temporary:
    plan = Path(temporary) / "plan.json"
    archive = Path(temporary) / "artifact.tar.gz"
    archive.write_bytes(b"authenticated exact artifact fixture")
    base = ["python3", str(PLAN)]
    subprocess.run(base + [
        "create", "--plan", str(plan), "--steamos", "3.8.14",
        "--nvidia", NVIDIA, "--kernel-tag", KERNEL,
        "--release-tag", "steamos-3.8.14-nvidia-575.64.05-kfixture",
        "--asset-name", "nvidia-open-fixture-x86_64.tar.gz",
    ], check=True, stdout=subprocess.PIPE)
    subprocess.run(base + ["bind-archive", "--plan", str(plan),
                           "--archive", str(archive)], check=True,
                   stdout=subprocess.PIPE)
    first = json.loads(plan.read_text())
    assert len(first["archiveSha256"]) == 64
    archive.write_bytes(b"changed publication")
    changed = subprocess.run(base + ["bind-archive", "--plan", str(plan),
                                     "--archive", str(archive)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert changed.returncode != 0
    assert json.loads(plan.read_text()) == first

with tempfile.TemporaryDirectory(prefix="opemos-recovery-stage-") as temporary:
    target = Path(temporary) / "root"
    target.mkdir()
    subprocess.run([
        str(STAGE), "--root", str(target), "--support-revision", "b" * 40,
        "--nvidia", NVIDIA,
    ], env={**os.environ, "PROJECT_TEST_MODE": "1"}, check=True)
    persistent = target / "home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"
    assert (persistent / "support-revision").read_text().strip() == "b" * 40
    assert (persistent / "nvidia-version").read_text().strip() == NVIDIA
    assert (persistent / "lib/open_opemos_contract.py").is_file()
    assert (persistent / "lib/desktop_update_generations.py").is_file()
    assert (persistent / "bootstrap/launch_desktop_companion.sh").is_file()
    assert json.loads((persistent / "trust/desktop-update-signers.json").read_text())["status"] == "unconfigured"
    assert (target / "etc/systemd/system/opemos-nvidia-guardian.service").is_file()
    assert (target / "etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service").is_symlink()
    assert (target / "etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair").stat().st_mode & 0o111

with tempfile.TemporaryDirectory(prefix="open-opemos-contract-") as temporary:
    status_path = Path(temporary) / "status.json"
    status_path.write_text(json.dumps({
        "schemaVersion": 1, "status": "fallback-active",
        "moduleVerification": {"status": "failed"},
        "fallback": {"active": True, "profile": "console"},
        "transaction": {"active": True, "phase": "offline_waiting"},
    }))
    result = subprocess.run(["python3", str(OPEN_CONTRACT), "--status", str(status_path)],
                            text=True, stdout=subprocess.PIPE, check=True)
    view = json.loads(result.stdout)
    assert view["title"] == "Open OPEMOS"
    assert view["phaseLabel"] == "Waiting for a trusted network"
    assert view["privilegeBoundary"] == "recoveryctl-fixed-actions-only"
    action_ids = {action["id"] for action in view["actions"]}
    assert action_ids == {"refresh", "repair", "cancel", "restore-graphics", "igpu", "nouveau"}
    assert all(not any("/dev/" in argument or ";" in argument
                       for argument in action["command"])
               for action in view["actions"])

with tempfile.TemporaryDirectory(prefix="opemos-path-confinement-") as temporary:
    root = Path(temporary) / "root"
    root.mkdir()
    (root / "home").mkdir()
    outside = Path(temporary) / "outside"
    outside.mkdir()
    (root / "home/.steamos").symlink_to(outside, target_is_directory=True)
    rejected = subprocess.run([
        "python3", str(PATH_VALIDATOR), "--root", str(root), "--test-owner",
        "--path", "home/.steamos/opemos/recoveryctl.sh",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert rejected.returncode != 0

with tempfile.TemporaryDirectory(prefix="opemos-link-confinement-") as temporary:
    root = Path(temporary) / "root"
    wants = root / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True)
    link = wants / "opemos.service"
    link.symlink_to("../opemos.service")
    command = [
        "python3", str(PATH_VALIDATOR), "--root", str(root), "--test-owner",
        "--path", "etc/systemd/system/opemos.service",
        "--expected-symlink",
        "etc/systemd/system/multi-user.target.wants/opemos.service=../opemos.service",
    ]
    subprocess.run(command, check=True)
    link.unlink()
    link.symlink_to("/tmp/untrusted.service")
    assert subprocess.run(command, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).returncode != 0

print("Installed-system recovery contract checks passed.")
