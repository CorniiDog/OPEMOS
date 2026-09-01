#!/usr/bin/env python3
"""Offline authenticated-cache export/import regression tests."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/authenticated_cache_bundle.py"
SIGNER = "889B5EBDDD505A683621900DAF1D2199EF0A3CCF"
TOOL_SPEC = importlib.util.spec_from_file_location("authenticated_cache_bundle", TOOL)
TOOL_MODULE = importlib.util.module_from_spec(TOOL_SPEC)
TOOL_SPEC.loader.exec_module(TOOL_MODULE)


def run(arguments, env, success=True):
    completed = subprocess.run([sys.executable, str(TOOL), *map(str, arguments)],
                               env=env, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    assert (completed.returncode == 0) == success, completed.stderr
    return completed


def clone(source, destination):
    subprocess.run(["cp", "-R", str(source), str(destination)], check=True)


def main():
    with tempfile.TemporaryDirectory(prefix="authenticated-cache-") as temporary:
        root = Path(temporary)
        binary = root / "bin"
        binary.mkdir()
        gpgv = binary / "gpgv"
        gpgv.write_text(
            "#!/bin/sh\n"
            "[ -z \"${CACHE_GPGV_DELAY:-}\" ] || sleep \"$CACHE_GPGV_DELAY\"\n"
            "case \"$(cat \"$5\")\" in *valid*) printf '[GNUPG:] VALIDSIG %s 0 0 0 0 0 0 0 0 0\\n' ;; *) exit 1 ;; esac\n"
            % SIGNER, encoding="utf-8")
        gpgv.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(binary) + os.pathsep + env["PATH"]
        artifact = root / "linux-headers.pkg.tar.zst"
        signature = root / "linux-headers.pkg.tar.zst.sig"
        keyring = root / "holo.gpg"
        signers = root / "valve-signers.json"
        artifact.write_bytes(b"authenticated headers fixture\n")
        signature.write_text("valid detached signature\n", encoding="utf-8")
        keyring.write_bytes(b"minimal reviewed keyring\n")
        signers.write_text(json.dumps({"schemaVersion": 1, "source": {}, "keyring": {},
                                       "signers": [{"fingerprint": SIGNER,
                                                    "status": "active"}]}) + "\n",
                           encoding="utf-8")
        bundle, store = root / "bundle", root / "store"
        common = ["--keyring", keyring, "--reviewed-signers", signers]
        run(["export", *common, "--artifact", artifact, "--signature", signature,
             "--output", bundle], env)
        exported = json.loads((bundle / "manifest.json").read_text())
        assert exported["trust"]["method"] == "detached-signature+reviewed-signer"
        first = json.loads(run(["import", *common, "--bundle", bundle,
                                "--store", store], env).stdout)
        second = json.loads(run(["import", *common, "--bundle", bundle,
                                 "--store", store], env).stdout)
        assert first == second and first["status"] == "verified"
        generations = [entry for entry in store.iterdir() if entry.name != ".import.lock"]
        assert len(generations) == 1

        # Corrupt, partial, symlinked, policy-drifted, and duplicate-key bundles
        # all fail before an imported generation is published.
        cases = []
        corrupt = root / "corrupt"
        clone(bundle, corrupt)
        (corrupt / "payload/artifact").write_bytes(b"corrupt")
        cases.append(corrupt)
        partial = root / "partial"
        clone(bundle, partial)
        (partial / "payload/artifact.sig").unlink()
        cases.append(partial)
        linked = root / "linked"
        clone(bundle, linked)
        (linked / "payload/artifact").unlink()
        os.symlink(artifact, linked / "payload/artifact")
        cases.append(linked)
        duplicate = root / "duplicate"
        clone(bundle, duplicate)
        text = (duplicate / "manifest.json").read_text()
        (duplicate / "manifest.json").write_text(text.replace('{"artifact":', '{"schemaVersion":1,"artifact":', 1))
        cases.append(duplicate)
        for index, candidate in enumerate(cases):
            failed_store = root / f"failed-{index}"
            run(["import", *common, "--bundle", candidate, "--store", failed_store],
                env, success=False)
            if failed_store.exists():
                assert not [entry for entry in failed_store.iterdir()
                            if entry.name != ".import.lock"]

        wrong_keyring = root / "wrong.gpg"
        wrong_keyring.write_bytes(b"unreviewed keyring")
        run(["import", "--keyring", wrong_keyring, "--reviewed-signers", signers,
             "--bundle", bundle, "--store", root / "wrong-store"], env, success=False)

        inactive = root / "inactive.json"
        inactive.write_text(json.dumps({"schemaVersion": 1, "signers": [
            {"fingerprint": SIGNER, "status": "revoked"}]}) + "\n")
        run(["export", "--keyring", keyring, "--reviewed-signers", inactive,
             "--artifact", artifact, "--signature", signature,
             "--output", root / "untrusted"], env, success=False)
        assert not (root / "untrusted").exists()

        # Multi-package userspace/certified-release bundles bind every package
        # and signature to the exact reviewed policy, provenance and trust roots.
        packages = []
        for name in ("nvidia-utils.pkg.tar.zst", "lib32-nvidia-utils.pkg.tar.zst"):
            package = root / name
            package.write_bytes((name + " authenticated payload\n").encode())
            package.with_name(name + ".sig").write_text("valid package signature\n")
            packages.append({"artifact": str(package), "signature": str(package) + ".sig"})
        policy = root / "userspace-lock.json"
        provenance = root / "certified-provenance.json"
        policy.write_text(json.dumps({"schemaVersion": 1, "profile": "gaming-no-cuda-v1",
                                      "packages": [Path(item["artifact"]).name for item in packages]},
                                     sort_keys=True) + "\n")
        provenance.write_text(json.dumps({"schemaVersion": 1, "supportCommit": "a" * 40,
                                          "policySha256": hashlib.sha256(policy.read_bytes()).hexdigest()},
                                         sort_keys=True) + "\n")
        spec = root / "set-spec.json"
        spec.write_text(json.dumps({"schemaVersion": 1, "artifacts": packages}) + "\n")
        set_bundle, set_store = root / "set-bundle", root / "set-store"

        # The manifest admits exactly 64 files and exactly 8 GiB, then rejects
        # one additional byte or file without allocating those payloads.
        def boundary_document(count, total):
            sizes = [total // count] * count
            sizes[-1] += total - sum(sizes)
            records = [{"name": f"package-{index}.pkg", "path": f"payload/package-{index}.pkg",
                        "sha256": "a" * 64, "size": size,
                        "signature": f"payload/package-{index}.pkg.sig",
                        "signatureSha256": "b" * 64, "signatureSize": 0}
                       for index, size in enumerate(sizes)]
            return {"schemaVersion": 1, "kind": "authenticated-artifact-set",
                    "policy": {"path": "metadata/policy.json", "sha256": "c" * 64, "size": 1},
                    "provenance": {"path": "metadata/provenance.json", "sha256": "d" * 64, "size": 1},
                    "artifacts": records,
                    "trust": {"method": "detached-signatures+reviewed-policy+provenance",
                              "signerFingerprints": [SIGNER] * count,
                              "keyringSha256": "e" * 64, "reviewedSignersSha256": "f" * 64,
                              "policySha256": "c" * 64, "provenanceSha256": "d" * 64}}
        TOOL_MODULE.validate_set_document(boundary_document(64, TOOL_MODULE.MAX_SET_BYTES))
        for invalid in (boundary_document(65, TOOL_MODULE.MAX_SET_BYTES),
                        boundary_document(64, TOOL_MODULE.MAX_SET_BYTES + 1)):
            try:
                TOOL_MODULE.validate_set_document(invalid)
                raise AssertionError("invalid boundary manifest was accepted")
            except SystemExit:
                pass

        # A concurrent exporter cannot overwrite the first export or publish a
        # partial generation; the reservation is removed on interruption.
        racing_output = root / "racing-set"
        racing_env = env.copy()
        racing_env["CACHE_GPGV_DELAY"] = "30"
        racing = subprocess.Popen(
            [sys.executable, str(TOOL), "export-set", *map(str, common),
             "--spec", str(spec), "--policy", str(policy),
             "--provenance", str(provenance), "--output", str(racing_output)],
            env=racing_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 5
        while not (root / "racing-set.lock").exists() and time.time() < deadline:
            time.sleep(0.02)
        run(["export-set", *common, "--spec", spec, "--policy", policy,
             "--provenance", provenance, "--output", racing_output], env, success=False)
        racing.terminate()
        racing.wait(timeout=5)
        assert not racing_output.exists() and not (root / "racing-set.lock").exists()

        run(["export-set", *common, "--spec", spec, "--policy", policy,
             "--provenance", provenance, "--output", set_bundle], env)

        delayed_env = env.copy()
        delayed_env["CACHE_GPGV_DELAY"] = "30"
        interrupted_output = root / "interrupted-set"
        interrupted = subprocess.Popen(
            [sys.executable, str(TOOL), "export-set", *map(str, common),
             "--spec", str(spec), "--policy", str(policy),
             "--provenance", str(provenance), "--output", str(interrupted_output)],
            env=delayed_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 5
        while not list(root.glob(".cache-set-export-*")) and time.time() < deadline:
            time.sleep(0.02)
        interrupted.terminate()
        interrupted.wait(timeout=5)
        assert interrupted.returncode != 0 and not interrupted_output.exists()
        assert not list(root.glob(".cache-set-export-*"))
        invocation = ["import-set", *common, "--bundle", set_bundle, "--store", set_store]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = [json.loads(value.stdout) for value in pool.map(lambda _: run(invocation, env), range(4))]
        assert all(value == results[0] for value in results)
        assert results[0]["artifactCount"] == 2
        assert len([entry for entry in set_store.iterdir() if entry.name != ".import.lock"]) == 1

        # Exact policy/provenance drift, corruption, symlinks, duplicates and
        # partial or unexpected layouts fail without publishing a generation.
        mutations = []
        for label in ("policy", "provenance", "corrupt", "partial", "symlink", "extra"):
            candidate = root / f"set-{label}"
            clone(set_bundle, candidate)
            if label == "policy":
                (candidate / "metadata/policy.json").write_text('{"schemaVersion":1,"profile":"drift"}\n')
            elif label == "provenance":
                (candidate / "metadata/provenance.json").write_text('{"schemaVersion":1,"supportCommit":"drift"}\n')
            elif label == "corrupt":
                (candidate / "payload/nvidia-utils.pkg.tar.zst").write_bytes(b"corrupt")
            elif label == "partial":
                (candidate / "payload/nvidia-utils.pkg.tar.zst.sig").unlink()
            elif label == "symlink":
                target = candidate / "payload/nvidia-utils.pkg.tar.zst"
                target.unlink()
                os.symlink(packages[0]["artifact"], target)
            else:
                (candidate / "payload/unexpected").write_bytes(b"unexpected")
            mutations.append(candidate)
        duplicate_set = root / "set-duplicate"
        clone(set_bundle, duplicate_set)
        manifest_text = (duplicate_set / "manifest.json").read_text()
        (duplicate_set / "manifest.json").write_text(manifest_text.replace('{"artifacts":', '{"schemaVersion":1,"artifacts":', 1))
        mutations.append(duplicate_set)
        for index, candidate in enumerate(mutations):
            rejected = root / f"set-rejected-{index}"
            run(["import-set", *common, "--bundle", candidate, "--store", rejected], env, success=False)
            if rejected.exists():
                assert not [entry for entry in rejected.iterdir() if entry.name != ".import.lock"]

        ambiguous_artifact = root / "ambiguous.sig"
        ambiguous_signature = root / "ambiguous.sig.sig"
        ambiguous_artifact.write_bytes(b"ambiguous authenticated payload")
        ambiguous_signature.write_text("valid signature\n")
        ambiguous_spec = root / "ambiguous-spec.json"
        ambiguous_spec.write_text(json.dumps({"schemaVersion": 1, "artifacts": [
            packages[0], {"artifact": str(ambiguous_artifact),
                          "signature": str(ambiguous_signature)}]}) + "\n")
        # `ambiguous.sig` would otherwise be both the first package signature
        # and the second package payload.
        first_with_collision = root / "ambiguous"
        first_with_collision.write_bytes(b"first authenticated payload")
        (root / "ambiguous.sig").write_text("valid shared path\n")
        ambiguous_spec.write_text(json.dumps({"schemaVersion": 1, "artifacts": [
            {"artifact": str(first_with_collision), "signature": str(root / "ambiguous.sig")},
            {"artifact": str(root / "ambiguous.sig"), "signature": str(ambiguous_signature)}]}) + "\n")
        run(["export-set", *common, "--spec", ambiguous_spec, "--policy", policy,
             "--provenance", provenance, "--output", root / "ambiguous-bundle"],
            env, success=False)


if __name__ == "__main__":
    main()
