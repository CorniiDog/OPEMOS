#!/usr/bin/env python3
"""No-input interstitial progress, installation, and hardening tests."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "lib/interstitial_progress.py"
VALIDATOR = ROOT / "lib/validate_interstitial_binary.py"
INSTALLER = ROOT / "bootstrap/install_recovery_guardian_to_root.sh"
LIVE_INSTALLER = ROOT / "bootstrap/install_recovery_guardian.sh"
SERVICE = ROOT / "support/recovery/opemos-interstitial.service.in"
GUARDIAN_SERVICE = ROOT / "support/recovery/opemos-nvidia-guardian.service.in"
DEMO = ROOT / "interstitial/demo/index.html"
SCHEMA = ROOT / "interstitial/progress-schema-v1.json"


def run_writer(state, *arguments, success=True):
    result = subprocess.run(
        ["python3", str(WRITER), *arguments, "--state", str(state)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stderr
    return json.loads(result.stdout) if success else result


with tempfile.TemporaryDirectory(prefix="opemos-interstitial-") as temporary:
    base = Path(temporary)
    state = base / "run/progress.json"
    state.parent.mkdir(mode=0o755)
    value = run_writer(state, "reset")
    assert value == {
        "schemaVersion": 1, "sequence": 0, "status": "working",
        "phase": "starting", "completed": None, "total": None,
        "stepCompleted": None, "stepTotal": None,
    }
    value = run_writer(state, "set", "--phase", "verifying", "--completed", "2", "--total", "5",
                       "--step-completed", "3", "--step-total", "4")
    assert value["sequence"] == 1 and value["completed"] == 2 and value["stepCompleted"] == 3
    run_writer(state, "set", "--phase", "verifying", "--completed", "3", "--total", "5",
               "--step-completed", "1", success=False)
    run_writer(state, "set", "--phase", "verifying", "--completed", "3", "--total", "5",
               "--step-completed", "1", "--step-total", "4", success=False)
    value = run_writer(state, "set", "--phase", "building", "--completed", "3", "--total", "5",
                       "--step-completed", "1", "--step-total", "4")
    assert value["stepCompleted"] == 1
    run_writer(state, "set", "--phase", "building", "--completed", "1", "--total", "5", success=False)
    value = run_writer(state, "succeed")
    assert value["status"] == "succeeded" and value["completed"] == value["total"] == 1
    assert value["stepCompleted"] == value["stepTotal"] == 1
    run_writer(state, "set", "--phase", "building", success=False)
    run_writer(state, "reset")
    run_writer(state, "set", "--phase", "complete", success=False)
    run_writer(state, "set", "--phase", "building", "--completed", "3", "--total", "2", success=False)

    state.unlink()
    outside = base / "outside"
    outside.write_text("fixture\n", encoding="utf-8")
    state.symlink_to(outside)
    run_writer(state, "reset", success=False)

    state.unlink()
    state.write_bytes(b"{" + b"x" * (64 * 1024))
    run_writer(state, "show", success=False)

    # Concurrent writers are serialized and cannot reuse a sequence.
    state.unlink()
    run_writer(state, "reset")
    processes = [subprocess.Popen(
        ["python3", str(WRITER), "set", "--phase", "verifying", "--state", str(state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for _ in range(8)]
    records = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        records.append(json.loads(stdout))
    assert sorted(record["sequence"] for record in records) == list(range(1, 9))

    binary = base / "opemos-interstitial"
    elf = bytearray(120)
    elf[:7] = b"\x7fELF\x02\x01\x01"
    elf[16:18] = (3).to_bytes(2, "little")
    elf[18:20] = (62).to_bytes(2, "little")
    elf[20:24] = (1).to_bytes(4, "little")
    elf[32:40] = (64).to_bytes(8, "little")
    elf[52:54] = (64).to_bytes(2, "little")
    elf[54:56] = (56).to_bytes(2, "little")
    elf[56:58] = (1).to_bytes(2, "little")
    binary.write_bytes(elf)
    binary.chmod(0o755)
    digest = hashlib.sha256(elf).hexdigest()
    checked = subprocess.run(
        ["python3", str(VALIDATOR), "--binary", str(binary), "--sha256", digest],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    assert json.loads(checked.stdout)["architecture"] == "x86_64"
    assert subprocess.run(
        ["python3", str(VALIDATOR), "--binary", str(binary), "--sha256", "0" * 64],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).returncode != 0

    target = base / "target"
    target.mkdir()
    subprocess.run([
        str(INSTALLER), "--root", str(target), "--support-revision", "a" * 40,
        "--nvidia", "575.64.05", "--interstitial-binary", str(binary),
        "--interstitial-sha256", digest,
    ], env={**os.environ, "PROJECT_TEST_MODE": "1"}, check=True)
    destination = target / "home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"
    assert (destination / "bin/opemos-interstitial").read_bytes() == bytes(elf)
    assert (destination / "bin/opemos-interstitial").stat().st_mode & 0o777 == 0o755
    assert (destination / "lib/interstitial_progress.py").is_file()
    assert (destination / "lib/validate_interstitial_binary.py").is_file()
    assert (destination / "bootstrap/launch_interstitial.sh").is_file()
    assert (destination / "interstitial.sha256").read_text().strip() == digest
    assert (destination / "bootstrap/run_guardian_with_interstitial.sh").is_file()
    assert (target / "etc/systemd/system/multi-user.target.wants/opemos-interstitial.service").is_symlink()
    installed_service = (target / "etc/systemd/system/opemos-interstitial.service").read_text()
    assert "@DEST@" not in installed_service
    # A repeat with the exact payload is idempotent.
    subprocess.run([
        str(INSTALLER), "--root", str(target), "--support-revision", "a" * 40,
        "--nvidia", "575.64.05", "--interstitial-binary", str(binary),
        "--interstitial-sha256", digest,
    ], env={**os.environ, "PROJECT_TEST_MODE": "1"}, check=True)
    installed_binary = destination / "bin/opemos-interstitial"
    installed_binary.write_bytes(installed_binary.read_bytes()[:-1] + b"X")
    assert subprocess.run(
        [str(destination / "bootstrap/launch_interstitial.sh")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).returncode != 0

help_result = subprocess.run(
    [str(LIVE_INSTALLER), "--help"], text=True,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
assert help_result.returncode == 0 and "--interstitial-binary" in help_result.stdout
pair_rejected = subprocess.run(
    [str(LIVE_INSTALLER), "--interstitial-binary", "/tmp/missing"],
    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
assert pair_rejected.returncode != 0 and "must be supplied together" in pair_rejected.stderr
live_installer = LIVE_INSTALLER.read_text(encoding="utf-8")
assert "enable opemos-interstitial.service" in live_installer
assert "validate_interstitial_binary.py" in live_installer

service = SERVICE.read_text(encoding="utf-8")
assert "Before=display-manager.service graphical.target" in service
assert "StandardInput=null" in service
assert "bootstrap/launch_interstitial.sh" in service
assert "ConditionPathExists=@DEST@/interstitial.sha256" in service
assert "RuntimeMaxSec=315" in service
assert "SuccessExitStatus=1 124 130 143" in service
assert "DevicePolicy=closed" in service
assert "ProtectSystem=strict" in service and "NoNewPrivileges=yes" in service
assert "ProtectHome=read-only" in service and "ProtectHome=yes" not in service
assert "CapabilityBoundingSet=\n" in service and "PrivateNetwork=yes" in service
assert "WantedBy=multi-user.target" in service
assert "keyboard" not in service.lower() and "mouse" not in service.lower()
guardian_service = GUARDIAN_SERVICE.read_text(encoding="utf-8")
assert "run_guardian_with_interstitial.sh" in guardian_service
assert "After=opemos-interstitial.service" in guardian_service

demo = DEMO.read_text(encoding="utf-8")
schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
assert schema["schemaVersion"] == 1 and schema["maxDocumentBytes"] == 64 * 1024
assert schema["maxSequence"] == 1_000_000_000
assert schema["fields"] == ["schemaVersion", "sequence", "status", "phase", "completed", "total",
                            "stepCompleted", "stepTotal"]
assert {record["id"] for record in schema["phases"]} == {
    "starting", "inspecting", "waiting_for_network", "downloading", "verifying", "building",
    "installing_userspace", "installing_modules", "updating_boot", "generating_initramfs",
    "cleaning_up", "complete", "recovery_required",
}
for label in (record["label"] for record in schema["phases"] if record["id"] not in {
    "waiting_for_network", "building", "recovery_required",
}):
    assert label in demo
assert "addEventListener" not in demo and "<input" not in demo and "<button" not in demo
assert 'src="/opemos-pill.svg"' in demo
assert 'id="overall-track"' in demo and 'id="step-track"' in demo
assert 'class="step-bar"' in demo
assert 'id="percent"' not in demo and "Overall / current operation" not in demo
assert "docs/assets/images/opemos-pill.svg" in (ROOT / "test_update_macos.sh").read_text()

print("No-input interstitial contract checks passed.")
