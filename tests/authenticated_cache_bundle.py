#!/usr/bin/env python3
"""Offline authenticated-cache export/import regression tests."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/authenticated_cache_bundle.py"
SIGNER = "889B5EBDDD505A683621900DAF1D2199EF0A3CCF"


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


if __name__ == "__main__":
    main()
