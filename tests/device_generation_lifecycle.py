#!/usr/bin/env python3
"""Installed-device reviewed-generation lifecycle regression tests."""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
from generate_userspace_lock_generation_fixtures import (  # noqa: E402
    canonical,
    documents,
    refresh,
)


TOOL = LIB / "device_generation_lifecycle.py"
DISCOVERY_FILENAME = "opemos-userspace-lock-discovery-v1.json"
FINGERPRINT = "A" * 40
TARGET_ARGUMENTS = [
    "--steamos", "3.8.14",
    "--kernel", "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
    "--nvidia", "575.64.05",
]


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def write(path, payload, mode=0o600):
    path.write_bytes(payload)
    path.chmod(mode)


def create_source(root, sequence, predecessor, authority, signature):
    root.mkdir(mode=0o700)
    payload_root = root / "payload"
    payload_root.mkdir(mode=0o700)
    lock_payload = b'{"schemaVersion":1}\n'
    package_payload = f"package-{sequence}\n".encode()
    package_signature = f"package-signature-{sequence}\n".encode()
    discovery, manifest = documents(sequence=sequence, predecessor=predecessor)
    lock = manifest["targetLocks"][0]["lock"]
    lock.update({"size": len(lock_payload), "sha256": digest(lock_payload)})
    manifest["authority"] = dict(authority)
    manifest["files"] = sorted([
        {
            "role": "package",
            "filename": f"package-{sequence}.pkg.tar.zst",
            "size": len(package_payload),
            "sha256": digest(package_payload),
        },
        {
            "role": "package-signature",
            "filename": f"package-{sequence}.pkg.tar.zst.sig",
            "size": len(package_signature),
            "sha256": digest(package_signature),
        },
        {
            "role": "userspace-lock",
            "filename": lock["filename"],
            "size": len(lock_payload),
            "sha256": digest(lock_payload),
        },
    ], key=lambda item: (item["role"], item["filename"]))
    refresh(discovery, manifest)
    discovery["generation"]["signatureSize"] = len(signature)
    discovery["generation"]["signatureSha256"] = digest(signature)
    manifest_name = discovery["generation"]["manifestFilename"]
    signature_name = discovery["generation"]["signatureFilename"]
    write(root / DISCOVERY_FILENAME, canonical(discovery))
    write(root / f"{DISCOVERY_FILENAME}.sig", signature)
    write(root / manifest_name, canonical(manifest))
    write(root / signature_name, signature)
    write(payload_root / f"package-{sequence}.pkg.tar.zst", package_payload)
    write(
        payload_root / f"package-{sequence}.pkg.tar.zst.sig",
        package_signature,
    )
    write(payload_root / lock["filename"], lock_payload)
    return discovery["generation"]["manifestSha256"]


def create_lineage_source(root, full_source):
    root.mkdir(mode=0o700)
    discovery_payload = (full_source / DISCOVERY_FILENAME).read_bytes()
    discovery = json.loads(discovery_payload)
    names = (
        DISCOVERY_FILENAME,
        f"{DISCOVERY_FILENAME}.sig",
        discovery["generation"]["manifestFilename"],
        discovery["generation"]["signatureFilename"],
    )
    for name in names:
        write(root / name, (full_source / name).read_bytes())


def invoke(environment, store, policy, keyring, checkpoint, command,
           arguments=None, success=True):
    process = subprocess.run([
        sys.executable, str(TOOL), "--store", str(store),
        "--policy", str(policy), "--keyring", str(keyring),
        "--checkpoint", str(checkpoint), command, *(arguments or []),
    ], cwd="/", env=environment, stdout=subprocess.PIPE,
       stderr=subprocess.PIPE, text=True, check=False)
    assert process.stderr == "", process.stderr
    assert (process.returncode == 0) is success, process.stdout
    document = json.loads(process.stdout)
    assert document["schemaVersion"] == 1
    assert document["status"] == ("ok" if success else "failed")
    return document


def activate(environment, store, policy, keyring, checkpoint, source,
             lineage=None, success=True):
    arguments = ["--source", str(source), *TARGET_ARGUMENTS]
    for item in lineage or []:
        arguments.extend(["--lineage", str(item)])
    return invoke(
        environment, store, policy, keyring, checkpoint, "activate",
        arguments, success,
    )


def acknowledge(environment, store, policy, keyring, checkpoint):
    state = invoke(
        environment, store, policy, keyring, checkpoint, "status"
    )["state"]
    evidence = checkpoint.parent / "health-evidence.json"
    write(evidence, canonical({
        "schemaVersion": 1,
        "kind": "opemos-device-generation-health",
        "status": "healthy",
        "generation": state["active"],
        "checks": ["generation-integrity", "recovery-ready"],
    }))
    return invoke(
        environment, store, policy, keyring, checkpoint,
        "acknowledge-health", ["--evidence", str(evidence)],
    )


def main():
    help_result = subprocess.run(
        [str(ROOT / "bootstrap/generationctl.sh"), "--help"], cwd="/",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    assert help_result.returncode == 0 and "installed-device" in help_result.stdout
    with tempfile.TemporaryDirectory(prefix="opemos-device-generations-") as name:
        root = Path(name).resolve()
        store = root / "device-store"
        keyring = root / "test-keyring.gpg"
        policy = root / "policy.json"
        checkpoint = root / "checkpoint.json"
        verifier = root / "gpgv"
        signature = b"test-signature\n"
        write(keyring, b"test-keyring\n")
        write(verifier, (
            "#!/bin/sh\n"
            f"printf '%s\\n' '[GNUPG:] VALIDSIG {FINGERPRINT}'\n"
        ).encode(), 0o700)
        policy_document = {
            "schemaVersion": 1,
            "status": "active",
            "policyId": "opemos-userspace-lock-generations",
            "policySchemaVersion": 1,
            "keyringFilename": keyring.name,
            "keyringSha256": digest(keyring.read_bytes()),
            "signingKeyFingerprint": FINGERPRINT,
        }
        write(policy, canonical(policy_document))
        authority = {
            "policyId": policy_document["policyId"],
            "policySchemaVersion": policy_document["policySchemaVersion"],
            "policySha256": digest(policy.read_bytes()),
            "keyringFilename": policy_document["keyringFilename"],
            "keyringSha256": policy_document["keyringSha256"],
            "signingKeyFingerprint": FINGERPRINT,
        }
        generation_7 = root / "generation-7"
        hash_7 = create_source(generation_7, 7, "1" * 64, authority, signature)
        write(checkpoint, canonical({
            "schemaVersion": 1,
            "policySha256": authority["policySha256"],
            "sequence": 7,
            "manifestSha256": hash_7,
        }))
        environment = {
            **os.environ,
            "OPEMOS_GENERATION_DEVELOPMENT_TRUST_OVERRIDE": "1",
            "OPEMOS_GENERATION_TEST_GPGV": str(verifier),
        }

        initial = activate(
            environment, store, policy, keyring, checkpoint, generation_7
        )
        assert initial["state"] == {
            "schemaVersion": 1,
            "channel": "reviewed-userspace-lock-generations",
            "active": {"sequence": 7, "manifestSha256": hash_7},
            "lastKnownGood": None,
            "highWaterSequence": 7,
            "healthPending": True,
        }
        assert initial["generationCreated"] is True
        cached_names = {
            item.name for item in (store / "generations" / hash_7).iterdir()
        }
        assert DISCOVERY_FILENAME in cached_names
        assert f"{DISCOVERY_FILENAME}.sig" in cached_names
        assert "discovery.json" not in cached_names
        assert "manifest.json" not in cached_names
        repeated = activate(
            environment, store, policy, keyring, checkpoint, generation_7
        )
        assert repeated["reason"] == "already_active"
        repeated_wrong_target = invoke(
            environment, store, policy, keyring, checkpoint, "activate",
            ["--source", str(generation_7), "--steamos", "3.8.15",
             "--kernel", TARGET_ARGUMENTS[3], "--nvidia", "575.64.05"],
            success=False,
        )
        assert repeated_wrong_target["reason"] == "device_generation_not_authorized"
        state_path = store / "state.json"
        valid_state_payload = state_path.read_bytes()
        invalid_state = json.loads(valid_state_payload)
        invalid_state["healthPending"] = False
        write(state_path, canonical(invalid_state))
        invalid_state_result = invoke(
            environment, store, policy, keyring, checkpoint, "status",
            success=False,
        )
        assert invalid_state_result["reason"] == "device_generation_state_invalid"
        write(state_path, valid_state_payload)
        wrong_evidence = root / "wrong-health.json"
        write(wrong_evidence, canonical({
            "schemaVersion": 1,
            "kind": "opemos-device-generation-health",
            "status": "healthy",
            "generation": {"sequence": 7, "manifestSha256": "0" * 64},
            "checks": ["generation-integrity", "recovery-ready"],
        }))
        rejected_health = invoke(
            environment, store, policy, keyring, checkpoint,
            "acknowledge-health", ["--evidence", str(wrong_evidence)],
            success=False,
        )
        assert rejected_health["reason"] == "device_generation_health_invalid"
        healthy_7 = acknowledge(
            environment, store, policy, keyring, checkpoint
        )
        assert healthy_7["state"]["lastKnownGood"] == healthy_7["state"]["active"]
        assert healthy_7["state"]["healthPending"] is False

        generation_8 = root / "generation-8"
        hash_8 = create_source(generation_8, 8, hash_7, authority, signature)
        wrong_target = invoke(
            environment, store, policy, keyring, checkpoint, "activate",
            ["--source", str(generation_8), "--steamos", "3.8.15",
             "--kernel", TARGET_ARGUMENTS[3], "--nvidia", "575.64.05"],
            success=False,
        )
        assert wrong_target["reason"] == "device_generation_not_authorized"
        activated_8 = activate(
            environment, store, policy, keyring, checkpoint, generation_8
        )
        assert activated_8["state"]["highWaterSequence"] == 8
        assert activated_8["state"]["lastKnownGood"]["manifestSha256"] == hash_7
        rolled_back = invoke(
            environment, store, policy, keyring, checkpoint, "rollback"
        )
        assert rolled_back["state"]["active"]["manifestSha256"] == hash_7
        assert rolled_back["state"]["highWaterSequence"] == 8
        replay = activate(
            environment, store, policy, keyring, checkpoint, generation_8,
            success=False,
        )
        assert replay["reason"] == "device_generation_not_authorized"

        generation_9 = root / "generation-9"
        hash_9 = create_source(generation_9, 9, hash_8, authority, signature)
        lineage_8 = root / "lineage-8"
        create_lineage_source(lineage_8, generation_8)
        caught_up = activate(
            environment, store, policy, keyring, checkpoint, generation_9,
            lineage=[lineage_8],
        )
        assert caught_up["state"]["active"] == {
            "sequence": 9, "manifestSha256": hash_9,
        }
        assert caught_up["state"]["highWaterSequence"] == 9
        generation_10 = root / "generation-10"
        hash_10 = create_source(generation_10, 10, hash_9, authority, signature)
        pending = activate(
            environment, store, policy, keyring, checkpoint, generation_10,
            success=False,
        )
        assert pending["reason"] == "device_generation_health_pending"
        acknowledge(environment, store, policy, keyring, checkpoint)

        write(generation_10 / "unexpected", b"unexpected\n")
        ambiguous = activate(
            environment, store, policy, keyring, checkpoint, generation_10,
            success=False,
        )
        assert ambiguous["reason"] == "device_generation_input_invalid"
        (generation_10 / "unexpected").unlink()

        no_space_environment = {
            **environment, "OPEMOS_GENERATION_TEST_FAIL_PHASE": "copy-enospc",
        }
        no_space = activate(
            no_space_environment, store, policy, keyring, checkpoint,
            generation_10, success=False,
        )
        assert no_space["reason"] == "device_generation_space_insufficient"
        assert not list((store / "generations").glob(".stage-*"))
        assert invoke(
            environment, store, policy, keyring, checkpoint, "status"
        )["state"]["active"]["manifestSha256"] == hash_9

        cancel_environment = {
            **environment, "OPEMOS_GENERATION_TEST_PAUSE_AFTER_STAGE": "30",
        }
        command = [
            sys.executable, str(TOOL), "--store", str(store),
            "--policy", str(policy), "--keyring", str(keyring),
            "--checkpoint", str(checkpoint), "activate", "--source",
            str(generation_10), *TARGET_ARGUMENTS,
        ]
        process = subprocess.Popen(
            command, cwd="/", env=cancel_environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 5
        while (not list((store / "generations").glob(".stage-*"))
               and time.time() < deadline):
            time.sleep(0.02)
        assert list((store / "generations").glob(".stage-*"))
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130 and stderr == ""
        assert json.loads(stdout)["status"] == "cancelled"
        assert not list((store / "generations").glob(".stage-*"))

        abandoned = store / "generations/.stage-abandoned"
        abandoned.mkdir(mode=0o700)
        (abandoned / "payload").mkdir(mode=0o700)
        write(abandoned / "payload/file", b"partial")
        (abandoned / "payload").chmod(0o500)
        invoke(environment, store, policy, keyring, checkpoint, "prune")
        assert not abandoned.exists()

        abandoned_prune = store / f"generations/.prune-{'f' * 64}"
        abandoned_prune.mkdir(mode=0o700)
        write(abandoned_prune / "partial", b"partial", 0o400)
        abandoned_prune.chmod(0o500)
        invoke(environment, store, policy, keyring, checkpoint, "prune")
        assert not abandoned_prune.exists()

        with (store / ".generation.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = invoke(
                environment, store, policy, keyring, checkpoint, "prune",
                success=False,
            )
            assert busy["reason"] == "device_generation_busy"
            busy_check = invoke(
                environment, store, policy, keyring, checkpoint, "check",
                success=False,
            )
            assert busy_check["reason"] == "device_generation_busy"

        invalid_signature = root / "invalid-signature"
        create_source(invalid_signature, 10, hash_9, authority, signature)
        invalid_discovery = json.loads(
            (invalid_signature / DISCOVERY_FILENAME).read_text(encoding="utf-8")
        )
        write(
            invalid_signature
            / invalid_discovery["generation"]["signatureFilename"],
            b"changed-signature\n",
        )
        rejected = activate(
            environment, store, policy, keyring, checkpoint,
            invalid_signature, success=False,
        )
        assert rejected["reason"] == "device_generation_authentication_failed"

        noisy_verifier = root / "noisy-gpgv"
        write(noisy_verifier, (
            f"#!{sys.executable}\n"
            "import sys\n"
            "sys.stdout.write('A' * 65537)\n"
        ).encode(), 0o700)
        noisy = activate(
            {**environment, "OPEMOS_GENERATION_TEST_GPGV": str(noisy_verifier)},
            store, policy, keyring, checkpoint, generation_10, success=False,
        )
        assert noisy["reason"] == "device_generation_authentication_failed"

        cached_8_file = next((store / "generations" / hash_8 / "payload").iterdir())
        cached_8_file.chmod(0o600)
        unsafe_cache = invoke(
            environment, store, policy, keyring, checkpoint, "prune",
            success=False,
        )
        assert unsafe_cache["reason"] == "device_generation_store_invalid"
        cached_8_file.chmod(0o400)

        for sequence, predecessor in ((10, hash_9), (11, None), (12, None)):
            source = root / f"generation-{sequence}-retention"
            current_predecessor = predecessor
            if current_predecessor is None:
                current_predecessor = invoke(
                    environment, store, policy, keyring, checkpoint, "status"
                )["state"]["active"]["manifestSha256"]
            create_source(source, sequence, current_predecessor, authority, signature)
            activate(environment, store, policy, keyring, checkpoint, source)
            acknowledge(environment, store, policy, keyring, checkpoint)
        generation_entries = [
            item for item in (store / "generations").iterdir()
            if not item.name.startswith(".")
        ]
        assert len(generation_entries) <= 4
        checked = invoke(
            environment, store, policy, keyring, checkpoint, "check"
        )
        assert checked["reason"] == "checked"

        active = checked["state"]["active"]
        generation_13 = root / "generation-13-crash"
        hash_13 = create_source(
            generation_13, 13, active["manifestSha256"], authority, signature
        )
        crash_environment = {
            **environment, "OPEMOS_GENERATION_TEST_PAUSE_AFTER_STATE": "30",
        }
        crash_command = [
            sys.executable, str(TOOL), "--store", str(store),
            "--policy", str(policy), "--keyring", str(keyring),
            "--checkpoint", str(checkpoint), "activate", "--source",
            str(generation_13), *TARGET_ARGUMENTS,
        ]
        crashed = subprocess.Popen(
            crash_command, cwd="/", env=crash_environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            state_path = store / "state.json"
            if (state_path.exists()
                    and json.loads(state_path.read_text())["active"]["sequence"] == 13):
                break
            time.sleep(0.02)
        assert json.loads((store / "state.json").read_text())["active"] == {
            "sequence": 13, "manifestSha256": hash_13,
        }
        crashed.kill()
        crashed.communicate(timeout=5)
        assert crashed.returncode == -signal.SIGKILL
        recovered = invoke(
            environment, store, policy, keyring, checkpoint, "check"
        )
        assert recovered["state"]["active"]["sequence"] == 13
        acknowledge(environment, store, policy, keyring, checkpoint)

        generation_14 = root / "generation-14-cancel-after-commit"
        hash_14 = create_source(generation_14, 14, hash_13, authority, signature)
        committed_cancel = subprocess.Popen([
            sys.executable, str(TOOL), "--store", str(store),
            "--policy", str(policy), "--keyring", str(keyring),
            "--checkpoint", str(checkpoint), "activate", "--source",
            str(generation_14), *TARGET_ARGUMENTS,
        ], cwd="/", env=crash_environment, stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 5
        while time.time() < deadline:
            state = json.loads((store / "state.json").read_text())
            if state["active"]["sequence"] == 14:
                break
            time.sleep(0.02)
        committed_cancel.send_signal(signal.SIGTERM)
        stdout, stderr = committed_cancel.communicate(timeout=5)
        committed_result = json.loads(stdout)
        assert committed_cancel.returncode == 0 and stderr == ""
        assert committed_result["status"] == "ok"
        assert committed_result["cancellationAfterCommit"] is True
        assert committed_result["state"]["active"] == {
            "sequence": 14, "manifestSha256": hash_14,
        }

        inactive = invoke(
            environment, store, policy, keyring, checkpoint, "update",
            success=False,
        )
        assert inactive["reason"] == "device_generation_network_inactive"

        store_link = root / "store-link"
        store_link.symlink_to(store, target_is_directory=True)
        unsafe_store = invoke(
            environment, store_link, policy, keyring, checkpoint, "status",
            success=False,
        )
        assert unsafe_store["reason"] == "device_generation_path_unsafe"

        production = {
            key: value for key, value in environment.items()
            if not key.startswith("OPEMOS_GENERATION_")
        }
        process = subprocess.run(
            [sys.executable, str(TOOL), "--store", str(root / "production"),
             "update"], cwd="/", env=production, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        assert process.returncode == 1 and process.stderr == ""
        assert json.loads(process.stdout)["reason"] == (
            "device_generation_network_inactive"
        )
        production_activate = subprocess.run([
            sys.executable, str(TOOL), "--store", str(root / "production"),
            "--policy", str(policy), "--keyring", str(keyring),
            "--checkpoint", str(checkpoint), "activate", "--source",
            str(generation_7), *TARGET_ARGUMENTS,
        ], cwd="/", env=production, stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, text=True, check=False)
        assert production_activate.returncode == 1
        assert json.loads(production_activate.stdout)["reason"] == (
            "device_generation_authentication_failed"
        )
        assert not (root / "production").exists()

        result_schema = json.loads((
            ROOT / "contracts/schemas/device-generation-result-v1.schema.json"
        ).read_text(encoding="utf-8"))
        health_schema = json.loads((
            ROOT / "contracts/schemas/device-generation-health-v1.schema.json"
        ).read_text(encoding="utf-8"))
        assert result_schema["additionalProperties"] is False
        assert result_schema["$defs"]["state"]["additionalProperties"] is False
        assert set(result_schema["properties"]["status"]["enum"]) == {
            "ok", "failed", "cancelled",
        }
        assert health_schema["additionalProperties"] is False
        assert health_schema["properties"]["checks"]["const"] == [
            "generation-integrity", "recovery-ready",
        ]


if __name__ == "__main__":
    main()
