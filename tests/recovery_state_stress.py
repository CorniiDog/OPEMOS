#!/usr/bin/env python3
"""Repeatable concurrency and crash-release stress for recovery state helpers."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSACTION = ROOT / "lib/recovery_transaction.py"
PLAN = ROOT / "lib/recovery_release_plan.py"
META = ROOT / "lib/validate_github_meta.py"
KERNEL = "6.16.12-valve24.5-1-neptune-616-stress"
NVIDIA = "575.64.05"
REVISION = "f" * 40

# A later certified publication must not replace an already healthy exact local
# repair. Freeze the recovery command's no-action return before all networking.
control = (ROOT / "bootstrap/recoveryctl.sh").read_text(encoding="utf-8")
healthy_gate = control.index('if [[ "$recovery_status" == healthy ]]')
network_probe = control.index('curl -fsS --connect-timeout 10 --max-time 20')
assert '| python3 "$SUPPORT_ROOT/lib/validate_github_meta.py"' in control[network_probe:]
healthy_block = control[healthy_gate:network_probe]
assert healthy_gate < network_probe
assert 'reconcile_verified_transaction "$kernel" "$nvidia" "$revision"' in healthy_block
assert 'emit_result restored exact_nvidia_already_healthy no_action' in healthy_block
assert "exit 0" in healthy_block
assert "online_install.sh" not in healthy_block

# Connectivity failures are deliberately normalized: a captive response, DNS
# failure, TLS failure, and an absent link cannot establish trusted network
# availability. None may advance the durable transaction to downloading.
network_failure = control[network_probe:control.index(
    'transaction_tool set --phase downloading', network_probe
)]
assert 'transaction_tool set --phase retry_scheduled --reason network_unavailable_or_untrusted' in network_failure
assert 'emit_result offline_waiting network_unavailable retry_scheduled' in network_failure
assert "exit 75" in network_failure


def run(arguments, *, success=True):
    result = subprocess.run(
        [sys.executable, *map(str, arguments)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return result


def validate_meta(payload, *, success):
    result = subprocess.run(
        [sys.executable, str(META)], input=payload,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert (result.returncode == 0) is success, result.stderr.decode(errors="replace")


# Future additive fields are safe; malformed, portal, ambiguous, and excessive
# evidence must not establish trusted connectivity.
validate_meta(b'{"hooks":["192.30.252.0/22"],"future":{"value":1}}', success=True)
for invalid_meta in (
    b"", b"<html>captive portal</html>", b"{}", b'{"hooks":[]}',
    b'{"hooks":["192.30.252.1/22"]}', b'{"hooks":[7]}',
    b'{"hooks":["192.30.252.0/22"],"hooks":["185.199.108.0/22"]}',
    b" " * (64 * 1024 + 1),
):
    validate_meta(invalid_meta, success=False)


def assert_archive_identity_drift_rejected(archive):
    program = r"""
import importlib.util
import pathlib
import sys
spec = importlib.util.spec_from_file_location("release_plan_drift", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
original = helper.identity
calls = 0
def changed(value):
    global calls
    calls += 1
    result = original(value)
    return result if calls != 2 else (*result[:-1], result[-1] + 1)
helper.identity = changed
try:
    helper.hash_archive(pathlib.Path(sys.argv[2]))
except ValueError as error:
    assert "changed while it was hashed" in str(error)
else:
    raise AssertionError("archive identity drift was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(PLAN), str(archive)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode == 0, result.stderr


def hold_lock(module, function, path):
    program = """
import importlib.util
import pathlib
import sys
import time
spec = importlib.util.spec_from_file_location("recovery_stress_helper", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
with getattr(helper, sys.argv[2])(pathlib.Path(sys.argv[3])):
    print("locked", flush=True)
    time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(module), function, str(path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


with tempfile.TemporaryDirectory(prefix="opemos-recovery-stress-") as temporary:
    root = Path(temporary)
    state = root / "transaction.json"
    transaction = [TRANSACTION]
    begin = [
        *transaction, "begin", "--state", state, "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", REVISION,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ]
    run(begin)
    canonical_state = state.read_bytes()

    readers = [subprocess.Popen(
        [sys.executable, str(TRANSACTION), "show", "--state", str(state)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) for _ in range(16)]
    outputs = []
    rejected = 0
    for reader in readers:
        output, error = reader.communicate(timeout=10)
        if reader.returncode == 0:
            outputs.append(output)
        else:
            rejected += 1
            assert "another recovery transaction operation" in error
    assert outputs and len(outputs) + rejected == len(readers)
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0])["target"]["kernelVersion"] == KERNEL
    assert state.read_bytes() == canonical_state

    for arguments in (
        [*transaction, "show", "--state", state, "--reason", "ignored"],
        [*transaction, "set", "--state", state, "--phase", "downloading",
         "--reason", "exact_artifact_resolution", "--kernel", KERNEL],
        [*transaction, "cancel", "--state", state, "--reason", "ignored"],
    ):
        run(arguments, success=False)
        assert state.read_bytes() == canonical_state

    holder = hold_lock(TRANSACTION, "transaction_lock", state)
    run([*transaction, "show", "--state", state], success=False)
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait(timeout=10)
    assert holder.returncode == -signal.SIGKILL
    run([*transaction, "show", "--state", state])

    # Model independent probe processes and service restarts against the
    # persisted transaction. The named cases differ below the Core boundary,
    # but all untrusted outcomes must retain the exact target and retry safely.
    for fault in ("absent", "captive_portal", "dns_failure", "tls_failure"):
        fault_state = root / f"network-{fault}.json"
        run([
            *transaction, "begin", "--state", fault_state, "--kernel", KERNEL,
            "--nvidia", NVIDIA, "--support-revision", REVISION,
            "--phase", "offline_waiting", "--reason", "network_not_verified",
        ])
        # A separate invocation represents the next boot/service process.
        persisted = json.loads(run([
            *transaction, "show", "--state", fault_state,
        ]).stdout)
        assert persisted["phase"] == "offline_waiting"
        assert persisted["target"] == {
            "kernelVersion": KERNEL, "nvidiaVersion": NVIDIA,
        }
        failed_probe = json.loads(run([
            *transaction, "set", "--state", fault_state,
            "--phase", "retry_scheduled",
            "--reason", "network_unavailable_or_untrusted",
        ]).stdout)
        assert failed_probe["phase"] == "retry_scheduled"
        assert failed_probe["automaticRetry"] is True
        assert failed_probe["attempt"] == 1

    # Flapping connectivity may alternate between an attempted download and a
    # scheduled retry without losing identity or exhausting the transaction.
    flapping = root / "network-flapping.json"
    run([
        *transaction, "begin", "--state", flapping, "--kernel", KERNEL,
        "--nvidia", NVIDIA, "--support-revision", REVISION,
        "--phase", "offline_waiting", "--reason", "network_not_verified",
    ])
    for attempt in range(3):
        run([
            *transaction, "set", "--state", flapping, "--phase", "downloading",
            "--reason", "exact_artifact_resolution",
        ])
        # Re-read from a new process before recording the dropped connection;
        # this also exercises reboot/resume while a download is active.
        resumed = json.loads(run([
            *transaction, "show", "--state", flapping,
        ]).stdout)
        assert resumed["phase"] == "downloading"
        assert resumed["attempt"] == attempt * 2 + 1
        run([
            *transaction, "set", "--state", flapping,
            "--phase", "retry_scheduled",
            "--reason", "network_unavailable_or_untrusted",
        ])
    flapping_document = json.loads(run([
        *transaction, "show", "--state", flapping,
    ]).stdout)
    assert flapping_document["phase"] == "retry_scheduled"
    assert flapping_document["attempt"] == 6

    # An independently observed target change wins over a stale in-flight
    # download and atomically returns to an exact, fresh offline transaction.
    drifted_kernel = KERNEL + "-identity-drift"
    run([
        *transaction, "retarget", "--state", flapping,
        "--kernel", drifted_kernel, "--nvidia", NVIDIA,
        "--support-revision", "e" * 40,
    ])
    drifted = json.loads(run([
        *transaction, "show", "--state", flapping,
    ]).stdout)
    assert drifted["phase"] == "offline_waiting"
    assert drifted["reason"] == "target_changed"
    assert drifted["attempt"] == 0
    assert drifted["target"] == {
        "kernelVersion": drifted_kernel, "nvidiaVersion": NVIDIA,
    }
    assert drifted["supportRevision"] == "e" * 40

    for index in range(12):
        cycle = root / f"cycle-{index}.json"
        run([
            *transaction, "begin", "--state", cycle, "--kernel", KERNEL,
            "--nvidia", NVIDIA, "--support-revision", REVISION,
            "--phase", "offline_waiting", "--reason", "network_not_verified",
        ])
        run([
            *transaction, "retarget", "--state", cycle,
            "--kernel", f"{KERNEL}-{index}", "--nvidia", NVIDIA,
            "--support-revision", REVISION,
        ])
        run([*transaction, "cancel", "--state", cycle])
        document = json.loads(cycle.read_text(encoding="utf-8"))
        assert document["phase"] == "cancelled"
        assert document["automaticRetry"] is False

    plan = root / "release-plan.json"
    archive = root / "artifact.tar.gz"
    plan_command = [PLAN]
    create = [
        *plan_command, "create", "--plan", plan,
        "--steamos", "3.8.14", "--nvidia", NVIDIA,
        "--kernel-tag", KERNEL, "--release-tag", "stress-release",
        "--asset-name", "stress-artifact-x86_64.tar.gz",
    ]
    run(create)
    unbound_plan = plan.read_bytes()
    run([*plan_command, "bind-archive", "--plan", plan, "--archive", archive], success=False)
    assert plan.read_bytes() == unbound_plan
    archive.write_bytes(b"immutable stress artifact")
    run([*plan_command, "bind-archive", "--plan", plan, "--archive", archive])
    canonical_plan = plan.read_bytes()
    wrong_target = [
        *plan_command, "create", "--plan", plan,
        "--steamos", "3.8.99", "--nvidia", NVIDIA,
        "--kernel-tag", "wrong-kernel", "--release-tag", "wrong-release",
        "--asset-name", "wrong-artifact-x86_64.tar.gz",
    ]
    run(wrong_target, success=False)
    assert plan.read_bytes() == canonical_plan
    assert_archive_identity_drift_rejected(archive)
    assert plan.read_bytes() == canonical_plan
    for arguments in (
        [*plan_command, "show", "--plan", plan, "--archive", archive],
        [*plan_command, "bind-archive", "--plan", plan, "--archive", archive,
         "--steamos", "3.8.14"],
    ):
        run(arguments, success=False)
        assert plan.read_bytes() == canonical_plan

    plan_readers = [subprocess.Popen(
        [sys.executable, str(PLAN), "show", "--plan", str(plan)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) for _ in range(16)]
    plan_outputs = []
    plan_rejected = 0
    for reader in plan_readers:
        output, error = reader.communicate(timeout=10)
        if reader.returncode == 0:
            plan_outputs.append(output)
        else:
            plan_rejected += 1
            assert "another release plan operation" in error
    assert plan_outputs and len(plan_outputs) + plan_rejected == len(plan_readers)
    assert len(set(plan_outputs)) == 1

    holder = hold_lock(PLAN, "plan_lock", plan)
    run([*plan_command, "show", "--plan", plan], success=False)
    holder.terminate()
    holder.wait(timeout=10)
    assert holder.returncode == -signal.SIGTERM
    run([*plan_command, "show", "--plan", plan])
    assert plan.read_bytes() == canonical_plan

print("Recovery state stress checks passed.")
