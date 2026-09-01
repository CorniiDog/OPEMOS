#!/usr/bin/env python3
"""Contract tests for resolving imported bundle generations into installer inputs."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib/resolve_authenticated_install_bundle.py"
INSTALLER = ROOT / "bootstrap/install_to_root.sh"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(generation, cache_id, keyring, output, success=True, steamos="3.8.14"):
    completed = subprocess.run([
        sys.executable, str(TOOL), "--generation", str(generation),
        "--cache-id", cache_id, "--steamos", steamos, "--nvidia", "575.64.05",
        "--keyring", str(keyring), "--output", str(output),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert (completed.returncode == 0) == success, completed.stderr


def main():
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "--input-source" in installer and "authenticated-bundle" in installer
    assert "authenticated_cache_bundle.py\" import-set" in installer
    assert "resolve_authenticated_install_bundle.py" in installer
    assert "cannot be mixed with direct userspace" in installer
    assert "--input-bundle-id" in installer
    bundle_section = installer.split("if [[ \"$INPUT_SOURCE\" == authenticated-bundle ]]", 1)[1].split("elif [[", 1)[0]
    assert "curl" not in bundle_section and "wget" not in bundle_section
    with tempfile.TemporaryDirectory(prefix="authenticated-install-bundle-") as temporary:
        root = Path(temporary)
        generation = root / "generation"
        payload, metadata = generation / "payload", generation / "metadata"
        payload.mkdir(parents=True)
        metadata.mkdir()
        keyring = root / "reviewed.gpg"
        keyring.write_bytes(b"reviewed keyring")
        packages = []
        artifacts = []
        for name, role in (("nvidia-utils.pkg", "nvidia-utils"),
                           ("lib32-nvidia-utils.pkg", "lib32-nvidia-utils"),
                           ("egl-gbm.pkg", "egl-gbm")):
            package, signature = payload / name, payload / f"{name}.sig"
            package.write_bytes(f"{role} payload".encode())
            signature.write_bytes(f"{role} signature".encode())
            packages.append({"name": role, "filename": name, "signatureFilename": f"{name}.sig",
                             "packageSha256": digest(package), "signatureSha256": digest(signature)})
            artifacts.append({"name": name, "path": f"payload/{name}", "sha256": digest(package),
                              "size": package.stat().st_size, "signature": f"payload/{name}.sig",
                              "signatureSha256": digest(signature), "signatureSize": signature.stat().st_size})
        policy = {"schemaVersion": 1, "status": "reviewed", "missingReview": [],
                  "target": {"steamosVersion": "3.8.14", "nvidiaVersion": "575.64.05",
                             "architecture": "x86_64"},
                  "keyring": {"filename": keyring.name, "sha256": digest(keyring)},
                  "packages": packages}
        provenance = {"schemaVersion": 1, "target": {"steamosVersion": "3.8.14",
                      "nvidiaVersion": "575.64.05", "architecture": "x86_64"}}
        policy_path, provenance_path = metadata / "policy.json", metadata / "provenance.json"
        policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n")
        provenance_path.write_text(json.dumps(provenance, sort_keys=True) + "\n")
        manifest = {"schemaVersion": 1, "kind": "authenticated-artifact-set",
                    "policy": {"path": "metadata/policy.json", "sha256": digest(policy_path),
                               "size": policy_path.stat().st_size},
                    "provenance": {"path": "metadata/provenance.json", "sha256": digest(provenance_path),
                                   "size": provenance_path.stat().st_size},
                    "artifacts": artifacts, "trust": {}}
        manifest_path = generation / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        cache_id = digest(manifest_path)
        output = root / "resolved.json"
        invoke(generation, cache_id, keyring, output)
        result = json.loads(output.read_text())
        assert result["sourceMode"] == "authenticated-bundle" and result["cacheId"] == cache_id
        assert [item["name"] for item in result["packages"]] == [
            "nvidia-utils", "lib32-nvidia-utils", "egl-gbm"]

        invoke(generation, cache_id, keyring, root / "wrong-target.json", False, steamos="3.8.16")
        wrong_keyring = root / "wrong.gpg"
        wrong_keyring.write_bytes(b"wrong")
        invoke(generation, cache_id, wrong_keyring, root / "wrong-keyring.json", False)
        invoke(generation, "0" * 64, keyring, root / "wrong-generation.json", False)
        linked = root / "linked-generation"
        os.symlink(generation, linked)
        invoke(linked, cache_id, keyring, root / "linked.json", False)


if __name__ == "__main__":
    main()
