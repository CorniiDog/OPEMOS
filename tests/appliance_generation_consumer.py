#!/usr/bin/env python3
"""End-to-end development generation handoff and guest-consumer tests."""

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from consume_appliance_generation import (  # noqa: E402
    MAX_GENERATION_STORAGE_BYTES,
    validate_handoff,
)
GENERATOR = ROOT / "lib/generate_development_appliance_generation.py"
CONSUMER = ROOT / "lib/consume_appliance_generation.py"
HANDOFF_NAME = "opemos-core-generation-handoff-v1.json"
TARGET = [
    "--steamos", "3.8.14",
    "--kernel", "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
    "--nvidia", "575.64.05",
    "--architecture", "x86_64",
]


def run(arguments, success=True):
    completed = subprocess.run(
        [sys.executable, *map(str, arguments)], cwd="/",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert (completed.returncode == 0) is success, completed.stderr
    return completed


def generate(path):
    completed = run([GENERATOR, "--development-test", "--output", path])
    return json.loads(completed.stdout)


def consume(generation, output, extra=(), success=True, development=True):
    trust = generation / "trust"
    arguments = [CONSUMER]
    if development:
        arguments.append("--development-test")
    arguments.extend([
        "--handoff", generation / "handoff",
        "--operation-id", "development-generation-v1",
        "--policy", trust / "policy.json",
        "--keyring", trust / "opemos-userspace-lock-generations.gpg",
        "--checkpoint", trust / "checkpoint.json",
        "--gpgv", trust / "development-gpgv",
        *TARGET,
        "--output", output,
        *extra,
    ])
    return run(arguments, success)


def tree_identity(root):
    records = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        payload_hash = None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        records.append((relative, stat.S_IMODE(info.st_mode), payload_hash))
    return records


def clone(source, destination):
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    for path in destination.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    destination.chmod(0o700)


def rewrite_handoff(root):
    path = root / "handoff" / HANDOFF_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for item in document["files"]:
        payload = (root / "handoff" / item["filename"]).read_bytes()
        records.append({
            "filename": item["filename"],
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    document["files"] = sorted(records, key=lambda item: item["filename"])
    path.chmod(0o600)
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o400)


def main():
    with tempfile.TemporaryDirectory(prefix="opemos-appliance-consumer-") as name:
        root = Path(name)
        first = root / "first"
        second = root / "second"
        first_summary = generate(first)
        assert first_summary == generate(second)
        assert tree_identity(first) == tree_identity(second)
        assert first_summary["trust"] == "development-test-only"

        handoff = json.loads((first / "handoff" / HANDOFF_NAME).read_text())
        target = {
            "steamosVersion": "3.8.14",
            "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
            "nvidiaVersion": "575.64.05",
            "architecture": "x86_64",
        }
        oversized = json.loads(json.dumps(handoff))
        for record in oversized["files"][:5]:
            record["size"] = 2 * 1024 * 1024 * 1024
        assert sum(item["size"] for item in oversized["files"]) \
            > MAX_GENERATION_STORAGE_BYTES
        try:
            validate_handoff(oversized, "development-generation-v1", target)
        except ValueError as error:
            assert "aggregate size is excessive" in str(error)
        else:
            raise AssertionError("excessive aggregate handoff was accepted")

        reserved = json.loads(json.dumps(handoff))
        reserved["files"][0]["filename"] = "CON.json"
        reserved["files"].sort(key=lambda item: item["filename"])
        try:
            validate_handoff(reserved, "development-generation-v1", target)
        except ValueError:
            pass
        else:
            raise AssertionError("Windows-reserved handoff filename was accepted")

        output_parent = root / "outputs"
        output_parent.mkdir(mode=0o700)
        output = output_parent / "installer-inputs"
        result = json.loads(consume(first, output).stdout)
        assert result["status"] == "prepared"
        assert result["trust"] == "development-test-only"
        assert result["generation"] == first_summary["generation"]
        assert {record["name"] for record in result["packages"]} == {
            "egl-gbm", "egl-wayland", "egl-x11", "eglexternalplatform",
            "lib32-nvidia-utils", "nvidia-utils",
        }
        expected_outputs = {
            result["userspaceLock"], result["packageKeyring"],
            result["packageSignerPolicy"], "installer-inputs-v1.json",
            *(record[field] for record in result["packages"]
              for field in ("filename", "signatureFilename")),
        }
        assert {item.name for item in output.iterdir()} == expected_outputs
        assert (output / "egl-wayland-4:1.1.19-1-x86_64.pkg.tar.zst").is_file()
        assert not (output / "egl-wayland-4@1.1.19-1-x86_64.pkg.tar.zst").exists()
        assert stat.S_IMODE(output.stat().st_mode) == 0o500
        assert all(stat.S_IMODE(item.stat().st_mode) == 0o400 for item in output.iterdir())
        descriptor_hash = hashlib.sha256(
            (output / "installer-inputs-v1.json").read_bytes()
        ).hexdigest()
        consume(first, output, success=False)
        assert hashlib.sha256(
            (output / "installer-inputs-v1.json").read_bytes()
        ).hexdigest() == descriptor_hash

        run([GENERATOR, "--development-test", "--output", first], success=False)
        assert tree_identity(first) == tree_identity(second)

        denied_parent = root / "denied-output"
        denied_parent.mkdir(mode=0o700)
        consume(first, denied_parent / "inputs", success=False, development=False)
        assert not (denied_parent / "inputs").exists()

        wrong_target_parent = root / "wrong-target-output"
        wrong_target_parent.mkdir(mode=0o700)
        consume(first, wrong_target_parent / "inputs", extra=("--steamos", "3.8.15"), success=False)
        assert not (wrong_target_parent / "inputs").exists()

        corrupt = root / "corrupt"
        clone(first, corrupt)
        package = corrupt / "handoff" / "nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst"
        package.chmod(0o600)
        package.write_bytes(b"corrupt\n")
        package.chmod(0o400)
        corrupt_parent = root / "corrupt-output"
        corrupt_parent.mkdir(mode=0o700)
        consume(corrupt, corrupt_parent / "inputs", success=False)
        assert not (corrupt_parent / "inputs").exists()

        extra = root / "extra"
        clone(first, extra)
        extra_file = extra / "handoff" / "unexpected.bin"
        extra_file.write_bytes(b"unexpected\n")
        extra_file.chmod(0o400)
        rewrite_handoff(extra)
        # Include the extra in the receipt to prove manifest equality—not merely
        # directory enumeration—rejects it.
        handoff_path = extra / "handoff" / HANDOFF_NAME
        handoff = json.loads(handoff_path.read_text())
        handoff["files"].append({
            "filename": extra_file.name,
            "size": extra_file.stat().st_size,
            "sha256": hashlib.sha256(extra_file.read_bytes()).hexdigest(),
        })
        handoff["files"].sort(key=lambda item: item["filename"])
        handoff_path.chmod(0o600)
        handoff_path.write_text(json.dumps(handoff, sort_keys=True, separators=(",", ":")) + "\n")
        handoff_path.chmod(0o400)
        extra_parent = root / "extra-output"
        extra_parent.mkdir(mode=0o700)
        consume(extra, extra_parent / "inputs", success=False)
        assert not (extra_parent / "inputs").exists()

        evidence = root / "evidence"
        clone(first, evidence)
        evidence_path = evidence / "handoff/opemos-userspace-lock-verifier-evidence-v1.json"
        evidence_path.chmod(0o600)
        record = json.loads(evidence_path.read_text())
        record["documents"][0]["payloadSha256"] = "0" * 64
        evidence_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        evidence_path.chmod(0o400)
        rewrite_handoff(evidence)
        evidence_parent = root / "evidence-output"
        evidence_parent.mkdir(mode=0o700)
        consume(evidence, evidence_parent / "inputs", success=False)
        assert not (evidence_parent / "inputs").exists()

        unsafe_parent = root / "unsafe-output"
        unsafe_parent.mkdir(mode=0o755)
        unsafe_parent.chmod(0o755)  # Do not let a private umask sanitize the fixture.
        assert unsafe_parent.stat().st_mode & 0o777 == 0o755
        consume(first, unsafe_parent / "inputs", success=False)
        assert not (unsafe_parent / "inputs").exists()

        linked = root / "linked"
        clone(first, linked)
        linked_package = linked / "handoff/nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst"
        sibling = linked / "hardlink-copy"
        os.link(linked_package, sibling)
        linked_parent = root / "linked-output"
        linked_parent.mkdir(mode=0o700)
        consume(linked, linked_parent / "inputs", success=False)
        assert not (linked_parent / "inputs").exists()

        cancelled = root / "cancelled"
        clone(first, cancelled)
        verifier = cancelled / "trust/development-gpgv"
        verifier_pid = root / "verifier.pid"
        verifier.write_text(
            f"#!/bin/sh\necho $$ > {verifier_pid}\nsleep 30\n",
            encoding="utf-8",
        )
        verifier.chmod(0o500)
        cancelled_parent = root / "cancelled-output"
        cancelled_parent.mkdir(mode=0o700)
        trust = cancelled / "trust"
        process = subprocess.Popen([
            sys.executable, str(CONSUMER), "--development-test",
            "--handoff", str(cancelled / "handoff"),
            "--operation-id", "development-generation-v1",
            "--policy", str(trust / "policy.json"),
            "--keyring", str(trust / "opemos-userspace-lock-generations.gpg"),
            "--checkpoint", str(trust / "checkpoint.json"),
            "--gpgv", str(verifier), *TARGET,
            "--output", str(cancelled_parent / "inputs"),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 3
        while not verifier_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert verifier_pid.exists(), "development verifier did not start"
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 130
        assert not (cancelled_parent / "inputs").exists()
        time.sleep(0.1)
        child_pid = int(verifier_pid.read_text())
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("cancelled verifier process survived")


if __name__ == "__main__":
    main()
