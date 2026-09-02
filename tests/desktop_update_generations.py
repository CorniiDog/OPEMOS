#!/usr/bin/env python3
"""Crash, trust, and rollback tests for native companion generations."""

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "lib/desktop_update_generations.py"
SIGNER = "A" * 40
REVISION = "b" * 40


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


def elf(marker):
    payload = bytearray(64)
    payload[:6] = b"\x7fELF\x02\x01"
    payload[18:20] = (62).to_bytes(2, "little")
    return bytes(payload) + marker.encode("ascii")


def update_files(root, version, marker):
    binary = root / f"binary-{version}"
    manifest = root / f"manifest-{version}.json"
    signature = root / f"manifest-{version}.json.sig"
    binary.write_bytes(elf(marker))
    document = {
        "schemaVersion": 1,
        "kind": "opemos-desktop-update",
        "releaseTag": f"opemos-desktop-v{version}",
        "version": version,
        "architecture": "x86_64",
        "filename": "opemos-recovery-status",
        "size": binary.stat().st_size,
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "supportRevision": REVISION,
        "minimumGuardianSchema": 1,
    }
    manifest.write_text(canonical(document), encoding="utf-8")
    signature.write_bytes(b"detached signature fixture\n")
    return manifest, signature, binary


def invoke(environment, *arguments, success=True, now=None):
    current = dict(environment)
    if now is not None:
        current["OPEMOS_TEST_NOW"] = str(now)
    completed = subprocess.run(
        [sys.executable, str(TOOL), *map(str, arguments)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=current, check=False,
    )
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"updater emitted invalid JSON: {completed.stdout!r}") from error
    if success and completed.returncode != 0:
        raise AssertionError(f"updater failed: {document} {completed.stderr}")
    if not success and completed.returncode == 0:
        raise AssertionError(f"updater unexpectedly succeeded: {document}")
    return completed, document


def stage(environment, store, paths, success=True):
    manifest, signature, binary = paths
    return invoke(environment, "stage", "--store", store,
                  "--manifest", manifest, "--signature", signature,
                  "--binary", binary, success=success)


def main():
    with tempfile.TemporaryDirectory(prefix="opemos-desktop-update-") as name:
        root = Path(name)
        binaries = root / "bin"
        binaries.mkdir()
        gpgv = binaries / "gpgv"
        gpgv.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${MOCK_PID_FILE:-}\" ]; then echo $$ > \"$MOCK_PID_FILE\"; exec sleep 30; fi\n"
            "[ \"${MOCK_GPGV_FAIL:-0}\" = 0 ] || exit 1\n"
            "printf '[GNUPG:] VALIDSIG %s 0 0 0 0 0 0 0 0 0\\n' \"${MOCK_SIGNER:-" + SIGNER + "}\"\n",
            encoding="utf-8",
        )
        gpgv.chmod(0o755)
        keyring = root / "updates.gpg"
        keyring.write_bytes(b"reviewed keyring fixture\n")
        policy = root / "policy.json"
        policy.write_text(canonical({
            "schemaVersion": 1,
            "status": "active",
            "keyringSha256": hashlib.sha256(keyring.read_bytes()).hexdigest(),
            "signers": [{"fingerprint": SIGNER, "status": "active",
                         "scope": "opemos-desktop-update"}],
        }), encoding="utf-8")
        environment = {
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "OPEMOS_DEVELOPMENT_TRUST_OVERRIDE": "1",
            "OPEMOS_DESKTOP_UPDATE_POLICY": str(policy),
            "OPEMOS_DESKTOP_UPDATE_KEYRING": str(keyring),
            "OPEMOS_TEST_GPGV": str(gpgv),
            "OPEMOS_TEST_ARCHITECTURE": "x86_64",
            "OPEMOS_TEST_SUPPORT_REVISION": REVISION,
        }
        store = root / "store"
        first = update_files(root, "0.1.0", "first")
        second = update_files(root, "0.2.0", "second")
        third = update_files(root, "0.3.0", "third")
        fourth = update_files(root, "0.4.0", "fourth")
        fifth = update_files(root, "0.5.0", "fifth")

        _, first_result = stage(environment, store, first)
        first_id = first_result["generation"]
        _, repeated = stage(environment, store, first)
        assert repeated["reason"] == "generation_already_staged"
        _, second_result = stage(environment, store, second)
        second_id = second_result["generation"]
        _, third_result = stage(environment, store, third)
        third_id = third_result["generation"]
        _, fourth_result = stage(environment, store, fourth)
        fourth_id = fourth_result["generation"]
        _, fifth_result = stage(environment, store, fifth)
        fifth_id = fifth_result["generation"]
        generation = store / "generations" / first_id
        assert stat.S_IMODE(generation.stat().st_mode) == 0o555
        assert stat.S_IMODE((generation / "opemos-recovery-status").stat().st_mode) == 0o555

        # Bootstrap, acknowledge, update, and acknowledge the new generation.
        invoke(environment, "activate", "--store", store, "--generation", first_id,
               "--timeout", 30, "--initial", now=100)
        invoke(environment, "acknowledge", "--store", store, "--generation", first_id,
               now=101)
        invoke(environment, "activate", "--store", store, "--generation", second_id,
               "--timeout", 30, now=200)
        _, pending = invoke(environment, "recover", "--store", store, now=210)
        assert pending["status"] == "pending"
        invoke(environment, "acknowledge", "--store", store, "--generation", second_id,
               now=211)
        _, resolved = invoke(environment, "resolve", "--store", store, now=212)
        assert resolved["generation"] == second_id
        assert Path(resolved["executable"]).name == "opemos-recovery-status"

        # A late acknowledgement rolls back rather than blessing a stale start.
        invoke(environment, "activate", "--store", store, "--generation", third_id,
               "--timeout", 5, now=300)
        _, late = invoke(environment, "acknowledge", "--store", store,
                         "--generation", third_id, success=False, now=306)
        assert "after its deadline" in late["message"]
        assert (store / "current").read_text().strip() == second_id
        assert not (store / "pending.json").exists()

        # Power loss before the pointer switch discards only the pending intent.
        pending_state = {"schemaVersion": 1, "candidate": third_id,
                         "previous": second_id, "activatedAt": 400, "deadline": 430}
        (store / "pending.json").write_text(canonical(pending_state), encoding="utf-8")
        os.chmod(store / "pending.json", 0o600)
        _, recovered = invoke(environment, "recover", "--store", store, now=401)
        assert recovered["reason"] == "uncommitted_activation_discarded"
        assert (store / "current").read_text().strip() == second_id

        # Power loss after the durable health marker finalizes acknowledgement.
        invoke(environment, "activate", "--store", store, "--generation", third_id,
               "--timeout", 30, now=500)
        (store / "last-known-good").write_text(third_id + "\n", encoding="ascii")
        _, finalized = invoke(environment, "recover", "--store", store, now=501)
        assert finalized["reason"] == "health_acknowledgement_finalized"
        assert (store / "current").read_text().strip() == third_id

        # Forced failure returns to the independently verified last-known-good.
        invoke(environment, "activate", "--store", store, "--generation", fourth_id,
               "--timeout", 30, now=600)
        _, rollback = invoke(environment, "recover", "--store", store, "--force", now=601)
        assert rollback["status"] == "rolled-back"
        assert rollback["generation"] == third_id

        # Cryptographic, signer, payload, manifest, architecture, and mode failures.
        bad_env = {**environment, "MOCK_GPGV_FAIL": "1"}
        bad_candidate = update_files(root, "0.7.0", "bad-candidate")
        stage(bad_env, root / "bad-signature-store", bad_candidate, success=False)
        wrong_signer = {**environment, "MOCK_SIGNER": "C" * 40}
        stage(wrong_signer, root / "bad-signer-store", bad_candidate, success=False)
        corrupt = list(update_files(root, "0.8.0", "corrupt"))
        corrupt[2].write_bytes(corrupt[2].read_bytes() + b"changed")
        stage(environment, root / "corrupt-store", corrupt, success=False)
        malformed = list(update_files(root, "0.9.0", "malformed"))
        malformed[0].write_text(malformed[0].read_text().replace(
            '{"architecture":', '{"architecture":"x86_64","architecture":', 1))
        stage(environment, root / "malformed-store", malformed, success=False)
        wrong_revision = list(update_files(root, "0.10.0", "wrong-revision"))
        revision_document = json.loads(wrong_revision[0].read_text())
        revision_document["supportRevision"] = "c" * 40
        wrong_revision[0].write_text(canonical(revision_document), encoding="utf-8")
        stage(environment, root / "wrong-revision-store", wrong_revision, success=False)
        bool_schema = list(update_files(root, "0.11.0", "bool-schema"))
        bool_document = json.loads(bool_schema[0].read_text())
        bool_document["schemaVersion"] = True
        bool_schema[0].write_text(canonical(bool_document), encoding="utf-8")
        stage(environment, root / "bool-schema-store", bool_schema, success=False)
        original_policy = policy.read_text()
        policy_document = json.loads(original_policy)
        policy_document["keyringSha256"] = "0" * 64
        policy.write_text(canonical(policy_document), encoding="utf-8")
        stage(environment, root / "wrong-keyring-store", bad_candidate, success=False)
        policy.write_text(original_policy, encoding="utf-8")
        wrong_arch = {**environment, "OPEMOS_TEST_ARCHITECTURE": "aarch64"}
        stage(wrong_arch, root / "wrong-arch-store", bad_candidate, success=False)
        os.chmod(generation / "opemos-recovery-status", 0o755)
        invoke(environment, "activate", "--store", store, "--generation", first_id,
               "--timeout", 30, success=False, now=650)
        os.chmod(generation / "opemos-recovery-status", 0o555)
        _, downgrade = invoke(environment, "activate", "--store", store,
                              "--generation", second_id, "--timeout", 30,
                              success=False, now=651)
        assert "must advance" in downgrade["message"]

        # A pending candidate that changes on disk is rolled back immediately,
        # without waiting for its startup deadline.
        invoke(environment, "activate", "--store", store, "--generation", fifth_id,
               "--timeout", 30, now=700)
        fourth_binary = store / "generations" / fifth_id / "opemos-recovery-status"
        os.chmod(fourth_binary, 0o755)
        fourth_binary.write_bytes(fourth_binary.read_bytes() + b"tampered")
        os.chmod(fourth_binary, 0o555)
        _, tampered = invoke(environment, "recover", "--store", store, now=701)
        assert tampered["status"] == "rolled-back"
        assert (store / "current").read_text().strip() == third_id

        # Locking and marker confinement fail before any lifecycle mutation.
        lock = open(store / ".update.lock", "a+b")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, locked = invoke(environment, "status", "--store", store, success=False)
        assert "another desktop update operation" in locked["message"]
        lock.close()
        victim = root / "victim"
        victim.write_text("unchanged\n")
        (store / "current").unlink()
        (store / "current").symlink_to(victim)
        invoke(environment, "status", "--store", store, success=False)
        assert victim.read_text() == "unchanged\n"

        unsafe_store = root / "world-writable-store"
        unsafe_store.mkdir(mode=0o777)
        os.chmod(unsafe_store, 0o777)
        invoke(environment, "stage", "--store", unsafe_store,
               "--manifest", bad_candidate[0], "--signature", bad_candidate[1],
               "--binary", bad_candidate[2], success=False)

        # Cancellation terminates and reaps the isolated signature verifier.
        pid_file = root / "gpgv.pid"
        cancelled_env = {**environment, "MOCK_PID_FILE": str(pid_file)}
        process = subprocess.Popen(
            [sys.executable, str(TOOL), "stage", "--store", root / "cancel-store",
             "--manifest", bad_candidate[0], "--signature", bad_candidate[1], "--binary", bad_candidate[2]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=cancelled_env,
        )
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.02)
        assert pid_file.exists()
        verifier_pid = int(pid_file.read_text())
        process.terminate()
        stdout, _ = process.communicate(timeout=5)
        assert process.returncode == 130
        assert json.loads(stdout)["status"] == "cancelled"
        try:
            os.kill(verifier_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("cancelled signature verifier remained alive")
        assert not any((root / "cancel-store" / "generations").glob(".stage-*"))

        # The committed production policy deliberately remains fail closed.
        production = {key: value for key, value in environment.items()
                      if not key.startswith("OPEMOS_")}
        stage(production, root / "production-store", bad_candidate, success=False)


if __name__ == "__main__":
    main()
