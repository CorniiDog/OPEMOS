#!/usr/bin/env python3
"""Installed-system recovery status and fail-closed profile tests."""

import json
import fcntl
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import payload_receipt  # noqa: E402
STATUS = ROOT / "lib/recovery_status.py"
POLICY = ROOT / "lib/recovery_policy.py"
CONTROL = ROOT / "bootstrap/recoveryctl.sh"
GRUB = ROOT / "lib/update_recovery_grub_args.py"
TRANSACTION = ROOT / "lib/recovery_transaction.py"
PLAN = ROOT / "lib/recovery_release_plan.py"
FALLBACK_STATE = ROOT / "lib/recovery_fallback_state.py"
STAGE = ROOT / "bootstrap/install_recovery_guardian_to_root.sh"
OPEN_CONTRACT = ROOT / "lib/open_opemos_contract.py"
PATH_VALIDATOR = ROOT / "lib/validate_recovery_install_path.py"
KERNEL = "6.16.12-valve24.5-1-neptune-616-test"
NVIDIA = "575.64.05"


def run(root, path, expected=None, require_receipt=False):
    arguments = [
        "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
    ]
    if expected is not None:
        arguments.extend(("--expected-nvidia", expected))
    if require_receipt:
        arguments.append("--require-payload-receipt")
    result = subprocess.run(
        arguments,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{path}:{os.environ['PATH']}"}, check=False,
    )
    return result.returncode, json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="opemos-recovery-") as temporary:
    root = Path(temporary) / "root"
    modules = (
        root / "usr/lib/modules" / KERNEL
        / "updates/open-gpu-kernel-modules-steamos"
    )
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

    code, document = run(root, mockbin, require_receipt=True)
    assert code == 0
    assert document["status"] == "recovery-required"
    assert document["reason"] == "payload_receipt_invalid"

    raw_payloads = {}
    for name in names:
        raw = f"authenticated {name} module fixture\n".encode()
        raw_payloads[name] = raw
        source = Path(temporary) / f"{name}.ko"
        source.write_bytes(raw)
        subprocess.run(
            ["zstd", "-q", "-f", str(source), "-o", str(modules / f"{name}.ko.zst")],
            check=True,
        )
    evidence_root = Path(temporary) / "receipt-evidence"
    evidence_root.mkdir()
    evidence = {
        name: evidence_root / filename for name, filename in (
            ("build_info", "BUILD-INFO.txt"),
            ("provenance", "PROVENANCE.json"),
            ("validation", "validation.json"),
            ("module_verification", "module-verification.json"),
            ("userspace_verification", "userspace-verification.json"),
            ("initramfs_verification", "initramfs-verification.json"),
        )
    }
    target = {
        "steamosVersion": "3.8.14", "kernelVersion": KERNEL,
        "nvidiaVersion": NVIDIA, "architecture": "x86_64",
    }
    evidence["build_info"].write_text("fixture=1\n", encoding="utf-8")
    evidence["provenance"].write_text(json.dumps({
        "schemaVersion": 1, "target": target,
        "trust": "locally-built-verified",
    }, sort_keys=True, separators=(",", ":")) + "\n")
    evidence["validation"].write_text(json.dumps({
        "schemaVersion": 1, "status": "verified", "target": target,
        "userspaceLock": {"name": "reviewed-userspace-lock.json", "sha256": "a" * 64},
    }, sort_keys=True, separators=(",", ":")) + "\n")
    module_records = []
    for name in names:
        module_name = name + ".ko"
        digest = hashlib.sha256(raw_payloads[name]).hexdigest()
        installed = modules / f"{module_name}.zst"
        module_records.append({
            "moduleName": module_name,
            "targetRelativePath": str(installed.relative_to(root)),
            "representation": ".ko.zst",
            "expectedPayloadSha256": digest,
            "actualPayloadSha256": digest,
            "expectedMode": "0644", "actualMode": "0644",
            "expectedUid": 0, "actualUid": 0,
            "expectedGid": 0, "actualGid": 0,
            "compressedSizeBytes": installed.stat().st_size,
            "decompressionStatus": "verified", "invalidFields": [],
        })
    evidence["module_verification"].write_text(json.dumps({
        "schemaVersion": 1, "status": "verified",
        "reason": "installed_modules_verified", "modules": module_records,
    }, sort_keys=True, separators=(",", ":")) + "\n")
    evidence["userspace_verification"].write_text(json.dumps({
        "schemaVersion": 1, "status": "verified",
        "packages": [{"packageName": "nvidia-utils"}],
    }, sort_keys=True, separators=(",", ":")) + "\n")
    evidence["initramfs_verification"].write_text(json.dumps({
        "schemaVersion": 1, "status": "verified", "kernelVersion": KERNEL,
    }, sort_keys=True, separators=(",", ":")) + "\n")
    payload_receipt.commit_receipt(SimpleNamespace(
        root=root, **evidence,
    ))
    code, document = run(root, mockbin, require_receipt=True)
    assert code == 0
    assert document["status"] == "healthy"

    changed_source = Path(temporary) / "changed-nvidia.ko"
    changed_source.write_bytes(b"different but metadata-compatible module\n")
    subprocess.run([
        "zstd", "-q", "-f", str(changed_source),
        "-o", str(modules / "nvidia.ko.zst"),
    ], check=True)
    code, document = run(root, mockbin, require_receipt=True)
    assert code == 0
    assert document["status"] == "recovery-required"
    assert document["reason"] == "module_payload_mismatch"
    source = Path(temporary) / "nvidia.ko"
    subprocess.run([
        "zstd", "-q", "-f", str(source),
        "-o", str(modules / "nvidia.ko.zst"),
    ], check=True)

    duplicate_directory = modules / "duplicate"
    duplicate_directory.mkdir()
    duplicate = duplicate_directory / "nvidia.ko.zst"
    duplicate.write_text("duplicate fixture\n")
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    duplicate.unlink()
    duplicate_directory.rmdir()

    unsafe_module = modules / "nvidia.ko.zst"
    unsafe_module.chmod(0o666)
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    unsafe_module.chmod(0o644)
    hardlink = Path(temporary) / "nvidia-hardlink.ko.zst"
    os.link(unsafe_module, hardlink)
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    hardlink.unlink()

    secondary = state / "nvidia-setup/nvidia-version"
    secondary.parent.mkdir()
    secondary.write_text("580.1.2\n")
    code, document = run(root, mockbin)
    assert code == 2
    assert document["status"] == "unknown"
    assert document["reason"] == "inspection_failed"
    secondary.unlink()

    code, document = run(root, mockbin, expected="580.1.2")
    assert code == 0
    assert document["status"] == "recovery-required"
    assert document["reason"] == "module_userspace_mismatch"

    installed_marker = state / "installed-nvidia.txt"
    installed_marker.unlink()
    code, document = run(root, mockbin, expected=NVIDIA)
    assert code == 0
    assert document["status"] == "recovery-required"
    assert document["reason"] == "module_userspace_mismatch"
    assert document["target"]["nvidiaVersion"] == NVIDIA
    installed_marker.write_text(NVIDIA + "\n")

    (state / "installed-nvidia.txt").write_text("not-a-version\n")
    code, document = run(root, mockbin)
    assert code == 2
    assert document["status"] == "unknown"
    (state / "installed-nvidia.txt").write_text(NVIDIA + "\n")

    installed_marker = state / "installed-nvidia.txt"
    installed_marker.chmod(0o666)
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    installed_marker.chmod(0o644)
    linked_marker = Path(temporary) / "linked-marker"
    os.link(installed_marker, linked_marker)
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    linked_marker.unlink()
    marker_payload = installed_marker.read_text()
    installed_marker.unlink()
    marker_target = Path(temporary) / "marker-target"
    marker_target.write_text(marker_payload)
    installed_marker.symlink_to(marker_target)
    code, document = run(root, mockbin)
    assert code == 2 and document["status"] == "unknown"
    installed_marker.unlink()
    installed_marker.write_text(marker_payload)

    policy = root / "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(NVIDIA + "\n")
    policy_result = subprocess.run(
        [
            "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
            "--expected-nvidia-file",
            "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{mockbin}:{os.environ['PATH']}"},
        check=False,
    )
    assert policy_result.returncode == 0
    assert json.loads(policy_result.stdout)["status"] == "healthy"
    policy.write_text("580.1.2\n")
    policy_result = subprocess.run(
        [
            "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
            "--expected-nvidia-file",
            "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{mockbin}:{os.environ['PATH']}"},
        check=False,
    )
    assert policy_result.returncode == 0
    mismatched_policy = json.loads(policy_result.stdout)
    assert mismatched_policy["status"] == "recovery-required"
    assert mismatched_policy["reason"] == "module_userspace_mismatch"
    policy.write_text(" \n")
    policy_result = subprocess.run(
        [
            "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
            "--expected-nvidia-file",
            "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{mockbin}:{os.environ['PATH']}"},
        check=False,
    )
    assert policy_result.returncode == 2
    assert json.loads(policy_result.stdout)["status"] == "unknown"
    policy.unlink()
    outside_policy = Path(temporary) / "outside-policy"
    outside_policy.write_text(NVIDIA + "\n")
    policy.symlink_to(outside_policy)
    policy_result = subprocess.run(
        [
            "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
            "--expected-nvidia-file",
            "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{mockbin}:{os.environ['PATH']}"},
        check=False,
    )
    assert policy_result.returncode == 2
    assert json.loads(policy_result.stdout)["status"] == "unknown"
    policy.unlink()
    policy_result = subprocess.run(
        [
            "python3", str(STATUS), "--root", str(root), "--kernel", KERNEL,
            "--expected-nvidia-file",
            "usr/lib/open-gpu-kernel-modules-steamos-support/nvidia-version",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{mockbin}:{os.environ['PATH']}"},
        check=False,
    )
    assert policy_result.returncode == 2
    assert json.loads(policy_result.stdout)["status"] == "unknown"

    recovery = state / "recovery"
    recovery.mkdir()
    (recovery / "state.json").write_text(
        '{"active":true,"profile":"console","schemaVersion":1}\n'
    )
    _, document = run(root, mockbin)
    assert document["status"] == "fallback-active"
    assert document["actions"] == ["disable-fallback"]

    hostile_states = (
        "not-json\n",
        '{"active":true,"active":false,"profile":"console","schemaVersion":1}\n',
        '{"active":true,"extra":1,"profile":"console","schemaVersion":1}\n',
        '{"active":1,"profile":"console","schemaVersion":1}\n',
        '{"active":true,"profile":"unknown","schemaVersion":1}\n',
        '{"active":true,"profile":{"name":"console"},"schemaVersion":1}\n',
        '{"active":true,"profile":"console","schemaVersion":NaN}\n',
        '{ "active": true, "profile": "console", "schemaVersion": 1 }\n',
    )
    for hostile_state in hostile_states:
        (recovery / "state.json").write_text(hostile_state)
        code, document = run(root, mockbin)
        assert code == 2
        assert document["status"] == "unknown"
        assert document["reason"] == "inspection_failed"

with tempfile.TemporaryDirectory(prefix="opemos-recovery-policy-") as temporary:
    policy_root = Path(temporary) / "policy"
    policy_root.mkdir(mode=0o755)
    revision_file = policy_root / "support-revision"
    nvidia_file = policy_root / "nvidia-version"
    revision_file.write_text("d" * 40 + "\n")
    nvidia_file.write_text(NVIDIA + "\n")
    revision_file.chmod(0o644)
    nvidia_file.chmod(0o644)
    policy_command = [
        sys.executable, str(POLICY), "--root", str(policy_root), "--test-owner",
    ]
    policy_result = subprocess.run(policy_command, text=True,
                                   stdout=subprocess.PIPE, check=True)
    assert json.loads(policy_result.stdout) == {
        "nvidiaVersion": NVIDIA,
        "schemaVersion": 1,
        "supportRevision": "d" * 40,
    }
    nvidia_file.chmod(0o666)
    unsafe_mode = subprocess.run(policy_command, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
    assert unsafe_mode.returncode != 0
    nvidia_file.chmod(0o644)
    linked_policy = Path(temporary) / "linked-policy"
    os.link(nvidia_file, linked_policy)
    unsafe_link = subprocess.run(policy_command, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
    assert unsafe_link.returncode != 0
    linked_policy.unlink()
    nvidia_file.write_text("575.64.05\nextra\n")
    malformed = subprocess.run(policy_command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    assert malformed.returncode != 0

with tempfile.TemporaryDirectory(prefix="opemos-fallback-state-") as temporary:
    state_root = Path(temporary) / "recovery"
    state_root.mkdir()
    fallback_state = state_root / "state.json"
    state_command = [sys.executable, str(FALLBACK_STATE)]
    subprocess.run(state_command + [
        "write", "--state", str(fallback_state), "--profile", "console",
    ], check=True)
    assert fallback_state.read_bytes() == (
        b'{"active":true,"profile":"console","schemaVersion":1}\n'
    )
    assert fallback_state.stat().st_mode & 0o777 == 0o644

    abandoned = state_root / ".fallback-state.tmp-abandoned"
    abandoned.write_bytes(b"partial")
    subprocess.run(state_command + [
        "write", "--state", str(fallback_state),
        "--profile", "igpu-desktop",
    ], check=True)
    assert not abandoned.exists()
    assert json.loads(fallback_state.read_text())["profile"] == "igpu-desktop"

    lock_path = state_root / ".fallback-state.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contended = subprocess.run(state_command + [
            "write", "--state", str(fallback_state), "--profile", "console",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert contended.returncode != 0
        assert b"another fallback state operation" in contended.stderr

    outside = Path(temporary) / "outside"
    outside.write_text("unchanged\n")
    fallback_state.unlink()
    fallback_state.symlink_to(outside)
    subprocess.run(state_command + [
        "write", "--state", str(fallback_state), "--profile", "console",
    ], check=True)
    assert not fallback_state.is_symlink()
    assert outside.read_text() == "unchanged\n"

    hardlink = Path(temporary) / "state-hardlink.json"
    os.link(fallback_state, hardlink)
    linked_remove = subprocess.run(state_command + [
        "remove", "--state", str(fallback_state),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert linked_remove.returncode != 0 and fallback_state.exists()
    hardlink.unlink()
    subprocess.run(state_command + [
        "remove", "--state", str(fallback_state),
    ], check=True)
    assert not fallback_state.exists()
    subprocess.run(state_command + [
        "remove", "--state", str(fallback_state),
    ], check=True)

    state_root.chmod(0o777)
    unsafe_parent = subprocess.run(state_command + [
        "write", "--state", str(fallback_state), "--profile", "console",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_parent.returncode != 0 and not fallback_state.exists()
    state_root.chmod(0o755)

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
assert 'if ! document="$(status_json)"' in control
assert "PROFILE=console YES=1 enable_fallback" in control
assert "recovery_fallback_state.py" in control
assert "state.json.tmp" not in control
assert "plan_tool remove" in control
assert "transaction_tool remove-terminal" in control
assert "transaction_tool retarget" in control
assert "transaction_tool reconcile-restored" in control
assert "reconcile_verified_transaction" in control
assert "trap restore_readonly EXIT" in control
assert "trap cancel_recovery HUP INT TERM" in control
assert "terminate_active_process_group" in control
assert 'run_cancellable sudo mkinitcpio -P' in control
assert 'run_cancellable "$SUPPORT_ROOT/bootstrap/online_install.sh" -y' in control
assert "trap restore_readonly EXIT INT TERM" not in control
cancel_gate = control.index(
    '[[ "$existing_phase" != cancelled ]] || die "Automatic recovery retries were cancelled."'
)
healthy_gate = control.index('if [[ "$recovery_status" == healthy ]]')
assert cancel_gate < healthy_gate
assert 'rm -f "$TRANSACTION"' not in control
assert 'rm -f "$RELEASE_PLAN"' not in control

with tempfile.TemporaryDirectory(prefix="opemos-guardian-", dir="/tmp") as temporary:
    guard_root = Path(temporary) / "root"
    guard_state = (
        guard_root / "var/lib/open-gpu-kernel-modules-steamos-support"
    )
    guard_state.mkdir(parents=True)
    (guard_state / "installed-nvidia.txt").write_text("ambiguous-invalid\n")
    guard_bin = Path(temporary) / "bin"
    guard_bin.mkdir()
    (guard_bin / "flock").write_text("#!/bin/sh\nexit 0\n")
    (guard_bin / "sudo").write_text("#!/bin/sh\nexec \"$@\"\n")
    (guard_bin / "flock").chmod(0o755)
    (guard_bin / "sudo").chmod(0o755)
    guard_policy = Path(temporary) / "policy"
    guard_policy.mkdir(mode=0o755)
    (guard_policy / "support-revision").write_text("e" * 40 + "\n")
    (guard_policy / "nvidia-version").write_text(NVIDIA + "\n")
    (guard_policy / "support-revision").chmod(0o644)
    (guard_policy / "nvidia-version").chmod(0o644)
    guard_environment = {
        **os.environ,
        "PATH": f"{guard_bin}:{os.environ['PATH']}",
        "PROJECT_TEST_MODE": "1",
        "PROJECT_TEST_ROOT": str(guard_root),
        "PROJECT_TEST_POLICY_ROOT": str(guard_policy),
    }
    unknown = subprocess.run(
        [str(CONTROL), "status", "--json"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=guard_environment, check=False,
    )
    assert unknown.returncode == 2
    assert unknown.stdout.strip(), (unknown.stdout, unknown.stderr)
    assert unknown.stdout.count("\n") == 1, repr(unknown.stdout)
    assert json.loads(unknown.stdout)["status"] == "unknown"
    assert not (ROOT / ".transaction.json.lock").exists()
    guarded = subprocess.run(
        [str(CONTROL), "guard", "--json"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=guard_environment,
        check=False,
    )
    assert guarded.returncode == 0, guarded.stderr
    result = json.loads(guarded.stdout)
    assert result == {
        "action": "console", "reason": "fallback_enabled",
        "schemaVersion": 1, "status": "fallback-active",
    }
    fallback_state = json.loads((
        guard_root
        / "var/lib/open-gpu-kernel-modules-steamos-support/recovery/state.json"
    ).read_text())
    assert fallback_state == {
        "schemaVersion": 1, "active": True, "profile": "console",
    }

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
    subprocess.run(base + ["remove-terminal", "--state", str(success_state)],
                   stdout=subprocess.PIPE, check=True)
    assert not success_state.exists()

    active_state = Path(temporary) / "active-removal.json"
    subprocess.run(base + [
        "begin", "--state", str(active_state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "c" * 40,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ], stdout=subprocess.PIPE, check=True)
    active_removal = subprocess.run(
        base + ["remove-terminal", "--state", str(active_state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert active_removal.returncode != 0 and active_state.is_file()
    next_kernel = KERNEL + "-next"
    retargeted = subprocess.run(base + [
        "retarget", "--state", str(active_state), "--kernel", next_kernel,
        "--nvidia", NVIDIA, "--support-revision", "d" * 40,
    ], text=True, stdout=subprocess.PIPE, check=True)
    retargeted_document = json.loads(retargeted.stdout)
    assert retargeted_document["target"]["kernelVersion"] == next_kernel
    assert retargeted_document["phase"] == "offline_waiting"
    assert retargeted_document["attempt"] == 0
    duplicate_retarget = subprocess.run(base + [
        "retarget", "--state", str(active_state), "--kernel", next_kernel,
        "--nvidia", NVIDIA, "--support-revision", "d" * 40,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert duplicate_retarget.returncode != 0
    subprocess.run(base + ["cancel", "--state", str(active_state)],
                   stdout=subprocess.PIPE, check=True)
    terminal_retarget = subprocess.run(base + [
        "retarget", "--state", str(active_state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "c" * 40,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert terminal_retarget.returncode != 0
    cancelled_reconciliation = subprocess.run(base + [
        "reconcile-restored", "--state", str(active_state),
        "--kernel", next_kernel, "--nvidia", NVIDIA,
        "--support-revision", "d" * 40,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert cancelled_reconciliation.returncode != 0
    linked_state = Path(temporary) / "active-removal-hardlink.json"
    os.link(active_state, linked_state)
    linked_removal = subprocess.run(
        base + ["remove-terminal", "--state", str(active_state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert linked_removal.returncode != 0 and active_state.is_file()
    linked_state.unlink()
    invalid_removal = subprocess.run(base + [
        "remove-terminal", "--state", str(active_state), "--reason", "ignored",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert invalid_removal.returncode != 0 and active_state.is_file()
    subprocess.run(base + ["remove-terminal", "--state", str(active_state)],
                   stdout=subprocess.PIPE, check=True)
    assert not active_state.exists()

    reconcile_state = Path(temporary) / "reconcile.json"
    subprocess.run(base + [
        "begin", "--state", str(reconcile_state), "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", "e" * 40,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ], stdout=subprocess.PIPE, check=True)
    subprocess.run(base + [
        "set", "--state", str(reconcile_state), "--phase", "downloading",
        "--reason", "exact_artifact_resolution",
    ], stdout=subprocess.PIPE, check=True)
    wrong_reconciliation = subprocess.run(base + [
        "reconcile-restored", "--state", str(reconcile_state),
        "--kernel", next_kernel, "--nvidia", NVIDIA,
        "--support-revision", "e" * 40,
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert wrong_reconciliation.returncode != 0
    reconciled = subprocess.run(base + [
        "reconcile-restored", "--state", str(reconcile_state),
        "--kernel", KERNEL, "--nvidia", NVIDIA,
        "--support-revision", "e" * 40,
    ], text=True, stdout=subprocess.PIPE, check=True)
    reconciled_document = json.loads(reconciled.stdout)
    assert reconciled_document["phase"] == "restored"
    assert reconciled_document["active"] is False
    assert reconciled_document["automaticRetry"] is False
    repeated_reconciliation = subprocess.run(base + [
        "reconcile-restored", "--state", str(reconcile_state),
        "--kernel", KERNEL, "--nvidia", NVIDIA,
        "--support-revision", "e" * 40,
    ], text=True, stdout=subprocess.PIPE, check=True)
    assert json.loads(repeated_reconciliation.stdout) == reconciled_document
    cancel_restored = subprocess.run(
        base + ["cancel", "--state", str(reconcile_state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert cancel_restored.returncode != 0

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
    canonical_plan = plan.read_bytes()
    assert plan.stat().st_mode & 0o777 == 0o600

    # Rebinding the exact immutable archive is idempotent.
    subprocess.run(base + ["bind-archive", "--plan", str(plan),
                           "--archive", str(archive)], check=True,
                   stdout=subprocess.PIPE)
    assert plan.read_bytes() == canonical_plan

    duplicate_create = subprocess.run(base + [
        "create", "--plan", str(plan), "--steamos", "3.8.14",
        "--nvidia", NVIDIA, "--kernel-tag", KERNEL,
        "--release-tag", "duplicate", "--asset-name", "duplicate.tar.gz",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert duplicate_create.returncode != 0
    assert plan.read_bytes() == canonical_plan

    plan.chmod(0o644)
    unsafe_mode = subprocess.run(base + ["show", "--plan", str(plan)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_mode.returncode != 0
    plan.chmod(0o600)

    linked_plan = Path(temporary) / "plan-hardlink.json"
    os.link(plan, linked_plan)
    unsafe_link = subprocess.run(base + ["show", "--plan", str(plan)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_link.returncode != 0
    unsafe_link_removal = subprocess.run(base + ["remove", "--plan", str(plan)],
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
    assert unsafe_link_removal.returncode != 0 and plan.exists()
    linked_plan.unlink()

    plan.write_bytes(canonical_plan.replace(
        b'{"archiveSha256":', b'{"schemaVersion":1,"archiveSha256":', 1,
    ))
    plan.chmod(0o600)
    duplicate_key = subprocess.run(base + ["show", "--plan", str(plan)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert duplicate_key.returncode != 0
    assert b"duplicate JSON key" in duplicate_key.stderr
    plan.write_text(json.dumps(first, indent=2) + "\n")
    plan.chmod(0o600)
    noncanonical = subprocess.run(base + ["show", "--plan", str(plan)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert noncanonical.returncode != 0
    plan.write_text('{"schemaVersion":NaN}\n')
    plan.chmod(0o600)
    nonfinite = subprocess.run(base + ["show", "--plan", str(plan)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert nonfinite.returncode != 0
    assert b"non-finite" in nonfinite.stderr
    plan.write_bytes(b"{" + b" " * (64 * 1024) + b"}\n")
    plan.chmod(0o600)
    excessive = subprocess.run(base + ["show", "--plan", str(plan)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert excessive.returncode != 0
    plan.write_bytes(canonical_plan)
    plan.chmod(0o600)

    archive.chmod(0o666)
    unsafe_archive = subprocess.run(base + [
        "bind-archive", "--plan", str(plan), "--archive", str(archive),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_archive.returncode != 0
    archive.chmod(0o644)
    linked_archive = Path(temporary) / "artifact-hardlink.tar.gz"
    os.link(archive, linked_archive)
    unsafe_archive = subprocess.run(base + [
        "bind-archive", "--plan", str(plan), "--archive", str(archive),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_archive.returncode != 0
    linked_archive.unlink()
    archive_link = Path(temporary) / "artifact-symlink.tar.gz"
    archive_link.symlink_to(archive)
    unsafe_archive = subprocess.run(base + [
        "bind-archive", "--plan", str(plan), "--archive", str(archive_link),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert unsafe_archive.returncode != 0

    invalid_plan = Path(temporary) / "invalid-plan.json"
    invalid_identity = subprocess.run(base + [
        "create", "--plan", str(invalid_plan), "--steamos", "3.8.14",
        "--nvidia", NVIDIA, "--kernel-tag", KERNEL,
        "--release-tag", "valid-tag", "--asset-name", "../escape.tar.gz",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert invalid_identity.returncode != 0 and not invalid_plan.exists()

    plan_lock = Path(temporary) / ".plan.json.lock"
    with plan_lock.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contended = subprocess.run(base + ["show", "--plan", str(plan)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert contended.returncode != 0
        assert b"another release plan operation" in contended.stderr

    archive.write_bytes(b"changed publication")
    changed = subprocess.run(base + ["bind-archive", "--plan", str(plan),
                                     "--archive", str(archive)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert changed.returncode != 0
    assert json.loads(plan.read_text()) == first
    subprocess.run(base + ["remove", "--plan", str(plan)],
                   stdout=subprocess.PIPE, check=True)
    assert not plan.exists()
    subprocess.run(base + ["remove", "--plan", str(plan)],
                   stdout=subprocess.PIPE, check=True)

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
    assert (persistent / "lib/recovery_policy.py").is_file()
    assert (persistent / "lib/recovery_fallback_state.py").is_file()
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
