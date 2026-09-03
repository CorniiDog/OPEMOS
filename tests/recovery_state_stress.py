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
KERNEL = "6.16.12-valve24.5-1-neptune-616-stress"
NVIDIA = "575.64.05"
REVISION = "f" * 40


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
    archive.write_bytes(b"immutable stress artifact")
    plan_command = [PLAN]
    create = [
        *plan_command, "create", "--plan", plan,
        "--steamos", "3.8.14", "--nvidia", NVIDIA,
        "--kernel-tag", KERNEL, "--release-tag", "stress-release",
        "--asset-name", "stress-artifact-x86_64.tar.gz",
    ]
    run(create)
    run([*plan_command, "bind-archive", "--plan", plan, "--archive", archive])
    canonical_plan = plan.read_bytes()
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
