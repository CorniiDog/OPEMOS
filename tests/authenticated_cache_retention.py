#!/usr/bin/env python3
"""Regression tests for authenticated cache retention and active leases."""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/prune_authenticated_cache.py"


def generation(store, marker, size, age):
    payload = (marker.encode() * (size + 1))[:size]
    signature = (marker + "-signature").encode()
    payload_hash = __import__("hashlib").sha256(payload).hexdigest()
    signature_hash = __import__("hashlib").sha256(signature).hexdigest()
    document = {"schemaVersion": 1, "kind": "detached-signature-artifact",
                "originalFilename": f"{marker}.pkg",
                "artifact": {"path": "payload/artifact", "size": len(payload),
                             "sha256": payload_hash},
                "signature": {"path": "payload/artifact.sig", "size": len(signature),
                              "sha256": signature_hash},
                "trust": {"method": "detached-signature+reviewed-signer",
                          "signerFingerprint": "A" * 40, "keyringSha256": "b" * 64,
                          "reviewedSignersSha256": "c" * 64}}
    manifest = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    identity = __import__("hashlib").sha256(manifest).hexdigest()
    path = store / identity
    path.mkdir()
    (path / "manifest.json").write_bytes(manifest)
    (path / "payload").mkdir()
    (path / "payload/artifact").write_bytes(payload)
    (path / "payload/artifact.sig").write_bytes(signature)
    os.utime(path, ns=(age, age))
    return path, identity


def run(store, count, size, *extra, success=True):
    completed = subprocess.run([sys.executable, str(TOOL), "--store", str(store),
                                "--max-count", str(count), "--max-bytes", str(size), *extra],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert (completed.returncode == 0) == success, completed.stderr
    return json.loads(completed.stdout) if success else completed


def main():
    with tempfile.TemporaryDirectory(prefix="authenticated-retention-") as temporary:
        store = Path(temporary) / "store"
        store.mkdir()
        created = [generation(store, character, 10, index + 1)
                   for index, character in enumerate("123")]
        values = [item[0] for item in created]
        identities = [item[1] for item in created]
        leases = store / ".leases"
        leases.mkdir()
        (leases / f"{identities[0]}.installer-test").write_text(identities[0] + "\n")
        (store / ".current").write_text(identities[2] + "\n")
        partial = store / ".cache-set-import-partial"
        partial.mkdir()
        linked = store / ("4" * 64)
        os.symlink(values[1], linked)
        result = run(store, 2, 4096)
        assert result["keptCount"] == 2
        assert values[0].exists() and values[2].exists() and not values[1].exists()
        assert not partial.exists() and not linked.exists()
        decisions = {item["cacheId"]: item for item in result["decisions"]}
        assert decisions[identities[0]]["protected"] is True
        assert decisions[identities[1]]["decision"] == "remove"

        run(store, 1, 1, "--protect", identities[0], success=False)
        assert values[0].exists() and values[2].exists()

        # A pruner waits behind the same import/export lock; cancellation while
        # waiting leaves every generation untouched.
        lock_path = store / ".import.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen([sys.executable, str(TOOL), "--store", str(store),
                                        "--max-count", "2", "--max-bytes", "4096"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.1)
            assert process.poll() is None
            process.terminate()
            process.wait(timeout=5)
            assert process.returncode != 0
        assert values[0].exists() and values[2].exists()


if __name__ == "__main__":
    main()
