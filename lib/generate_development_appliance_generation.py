#!/usr/bin/env python3
"""Materialize one deterministic, explicitly non-production generation handoff."""

import argparse
import copy
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from userspace_lock_generation_contract import (
    DISCOVERY_FILENAME,
    DISCOVERY_SIGNATURE_FILENAME,
    canonical,
    validate_pair,
)
from userspace_lock_verifier_evidence import validate_evidence_record


TARGET = {
    "steamosVersion": "3.8.14",
    "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
    "nvidiaVersion": "575.64.05",
    "architecture": "x86_64",
}
FINGERPRINT = "A" * 40
EVIDENCE_FILENAME = "opemos-userspace-lock-verifier-evidence-v1.json"
HANDOFF_FILENAME = "opemos-core-generation-handoff-v1.json"
LOCK_FILENAME = "steamos-3-8-14-nvidia-575-64-05-development-lock.json"
SIGNER_POLICY_FILENAME = "nvidia-userspace-package-signers-development-v1.json"
PACKAGE_KEYRING_FILENAME = "archlinux-nvidia-userspace-development.gpg"


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def write(path, payload, mode=0o400):
    path.write_bytes(payload)
    path.chmod(mode)


def package(name, filename, version, architecture, dependencies, provides):
    payload = f"OPEMOS DEVELOPMENT TEST PACKAGE: {name}\n".encode()
    signature = f"OPEMOS DEVELOPMENT TEST SIGNATURE: {name}\n".encode()
    return {
        "record": {
            "name": name,
            "filename": filename,
            "signatureFilename": filename + ".sig",
            "version": version,
            "architecture": architecture,
            "packageSha256": sha256(payload),
            "signatureSha256": sha256(signature),
            "signerFingerprint": FINGERPRINT,
            "installedSize": len(payload),
            "dependencies": dependencies,
            "provides": provides,
        },
        "payload": payload,
        "signature": signature,
    }


def build_documents():
    packages = [
        package("egl-gbm", "egl-gbm-1.1.2.1-1-x86_64.pkg.tar.zst", "1.1.2.1-1", "x86_64", ["eglexternalplatform", "glibc", "mesa", "libdrm"], ["libnvidia-egl-gbm.so=1"]),
        package("egl-wayland", "egl-wayland-4:1.1.19-1-x86_64.pkg.tar.zst", "4:1.1.19-1", "x86_64", ["eglexternalplatform", "glibc", "libdrm", "wayland"], ["libnvidia-egl-wayland.so=1-64"]),
        package("egl-x11", "egl-x11-1.0.2-1-x86_64.pkg.tar.zst", "1.0.2-1", "x86_64", ["eglexternalplatform", "glibc", "libdrm", "libx11", "libxcb", "mesa"], []),
        package("eglexternalplatform", "eglexternalplatform-1.2.1-1-any.pkg.tar.zst", "1.2.1-1", "any", ["libegl"], []),
        package("lib32-nvidia-utils", "lib32-nvidia-utils-575.64.05-1-x86_64.pkg.tar.zst", "575.64.05-1", "x86_64", ["lib32-zlib", "lib32-gcc-libs", "lib32-libglvnd", "nvidia-utils=575.64.05"], ["lib32-vulkan-driver", "lib32-opengl-driver", "lib32-nvidia-libgl"]),
        package("nvidia-utils", "nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst", "575.64.05-2", "x86_64", ["libglvnd", "egl-wayland", "egl-gbm", "egl-x11"], ["vulkan-driver", "opengl-driver", "nvidia-libgl"]),
    ]
    package_keyring = b"OPEMOS DEVELOPMENT TEST PACKAGE KEYRING\n"
    signer_policy = canonical({
        "schemaVersion": 1,
        "signers": [{
            "fingerprint": FINGERPRINT,
            "status": "active",
            "packages": [item["record"]["name"] for item in packages],
            "reviewedAt": "2026-09-03",
            "evidence": "Synthetic development/test authority; never valid for production",
        }],
    })
    lock = {
        "schemaVersion": 1,
        "status": "reviewed",
        "target": {key: TARGET[key] for key in ("steamosVersion", "nvidiaVersion", "architecture")},
        "snapshot": {"identity": "development-test-only"},
        "keyring": {
            "filename": PACKAGE_KEYRING_FILENAME,
            "sha256": sha256(package_keyring),
            "provenance": {"status": "development-test-only"},
        },
        "packages": [item["record"] for item in packages],
        "missingReview": [],
    }
    lock_payload = (json.dumps(lock, sort_keys=True, indent=2) + "\n").encode()
    generation_keyring = b"OPEMOS DEVELOPMENT TEST GENERATION KEYRING\n"
    policy = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-bootstrap-policy",
        "status": "active",
        "policyId": "opemos-userspace-lock-generations",
        "policySchemaVersion": 1,
        "authority": {
            "keyringFilename": "opemos-userspace-lock-generations.gpg",
            "keyringSha256": sha256(generation_keyring),
            "primarySigningFingerprint": FINGERPRINT,
            "signatureScheme": "openpgp-detached-v1",
            "allowedHashAlgorithmIds": [8, 9, 10],
        },
        "channel": {
            "origin": "https://development.invalid",
            "discoveryPath": "/development/opemos-userspace-lock-discovery-v1.json",
            "discoveryFilename": DISCOVERY_FILENAME,
            "discoverySignatureFilename": DISCOVERY_SIGNATURE_FILENAME,
            "immutableReleasePathPrefix": "/development/releases/",
            "releaseTagPrefix": "opemos-userspace-lock-generation-v1-s",
            "allowRedirects": False,
        },
        "compatibility": {
            "discoverySchemaVersions": [1],
            "generationManifestSchemaVersions": [1],
            "userspaceLockSchemaVersions": [1],
            "installerResultSchemaVersions": [1],
        },
        "replayPolicy": {
            "requireMonotonicHighWater": True,
            "requireImmediatePredecessor": True,
            "allowAuthenticatedLineageCatchup": True,
            "maximumLineageGenerations": 64,
        },
    }
    policy_payload = canonical(policy)
    authority = {
        "policyId": "opemos-userspace-lock-generations",
        "policySchemaVersion": 1,
        "policySha256": sha256(policy_payload),
        "keyringFilename": "opemos-userspace-lock-generations.gpg",
        "keyringSha256": sha256(generation_keyring),
        "signingKeyFingerprint": FINGERPRINT,
    }
    assets = {}
    files = []
    for item in packages:
        source_name = item["record"]["filename"]
        alias = source_name.replace(":", "@")
        assets[alias] = item["payload"]
        assets[alias + ".sig"] = item["signature"]
        files.extend([
            {"role": "package", "filename": alias, "size": len(item["payload"]), "sha256": sha256(item["payload"])},
            {"role": "package-signature", "filename": alias + ".sig", "size": len(item["signature"]), "sha256": sha256(item["signature"])},
        ])
    assets[LOCK_FILENAME] = lock_payload
    assets[PACKAGE_KEYRING_FILENAME] = package_keyring
    assets[SIGNER_POLICY_FILENAME] = signer_policy
    files.extend([
        {"role": "userspace-lock", "filename": LOCK_FILENAME, "size": len(lock_payload), "sha256": sha256(lock_payload)},
        {"role": "keyring", "filename": PACKAGE_KEYRING_FILENAME, "size": len(package_keyring), "sha256": sha256(package_keyring)},
        {"role": "signer-policy", "filename": SIGNER_POLICY_FILENAME, "size": len(signer_policy), "sha256": sha256(signer_policy)},
    ])
    files.sort(key=lambda item: (item["role"], item["filename"]))
    manifest = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-generation",
        "channel": "reviewed",
        "sequence": 1,
        "publishedAt": "2026-09-03T12:00:00Z",
        "authority": authority,
        "previousManifestSha256": None,
        "targetLocks": [{
            "target": copy.deepcopy(TARGET),
            "lock": {"filename": LOCK_FILENAME, "schemaVersion": 1, "sha256": sha256(lock_payload), "size": len(lock_payload)},
        }],
        "files": files,
    }
    manifest_payload = canonical(manifest)
    manifest_name = "opemos-userspace-lock-generation-v1-s1.manifest.json"
    manifest_signature_name = manifest_name + ".sig"
    manifest_signature = b"OPEMOS DEVELOPMENT TEST MANIFEST SIGNATURE\n"
    discovery = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-discovery",
        "channel": "reviewed",
        "sequence": 1,
        "publishedAt": manifest["publishedAt"],
        "authority": copy.deepcopy(authority),
        "compatibility": {
            "discoverySchemaVersion": 1,
            "generationManifestSchemaVersion": 1,
            "userspaceLockSchemaVersion": 1,
            "minimumInstallerResultSchemaVersion": 1,
        },
        "generation": {
            "releaseTag": "opemos-userspace-lock-generation-v1-s1",
            "manifestFilename": manifest_name,
            "manifestSha256": sha256(manifest_payload),
            "manifestSize": len(manifest_payload),
            "signatureFilename": manifest_signature_name,
            "signatureSha256": sha256(manifest_signature),
            "signatureSize": len(manifest_signature),
            "previousManifestSha256": None,
        },
        "targets": copy.deepcopy(manifest["targetLocks"]),
    }
    validate_pair(discovery, manifest)
    discovery_payload = canonical(discovery)
    discovery_signature = b"OPEMOS DEVELOPMENT TEST DISCOVERY SIGNATURE\n"
    status = {
        "signingFingerprint": FINGERPRINT,
        "primarySigningFingerprint": FINGERPRINT,
        "hashAlgorithmId": 10,
    }
    evidence = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-verifier-evidence",
        "status": "authenticated",
        "verificationProfile": "openpgp-detached-validsig-v1",
        "policySha256": sha256(policy_payload),
        "keyringFilename": "opemos-userspace-lock-generations.gpg",
        "keyringSha256": sha256(generation_keyring),
        "primarySigningFingerprint": FINGERPRINT,
        "documents": [],
    }
    for role, payload, signature in (
            ("discovery", discovery_payload, discovery_signature),
            ("generation-manifest", manifest_payload, manifest_signature)):
        evidence["documents"].append({
            "role": role,
            "payloadSha256": sha256(payload), "payloadSize": len(payload),
            "signatureSha256": sha256(signature), "signatureSize": len(signature),
            **status,
        })
    validate_evidence_record(evidence)
    checkpoint = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-bootstrap-checkpoint",
        "policySha256": sha256(policy_payload),
        "minimumSequence": 1,
        "minimumManifestSha256": sha256(manifest_payload),
    }
    assets.update({
        DISCOVERY_FILENAME: discovery_payload,
        DISCOVERY_SIGNATURE_FILENAME: discovery_signature,
        manifest_name: manifest_payload,
        manifest_signature_name: manifest_signature,
        EVIDENCE_FILENAME: canonical(evidence),
    })
    handoff_files = [{"filename": name, "size": len(payload), "sha256": sha256(payload)} for name, payload in sorted(assets.items())]
    handoff = {
        "schemaVersion": 1,
        "kind": "opemos-core-appliance-generation-handoff",
        "operationId": "development-generation-v1",
        "identity": {"sequence": 1, "generationId": sha256(manifest_payload), "manifestSha256": sha256(manifest_payload)},
        "target": copy.deepcopy(TARGET),
        "lineageManifestSha256": [],
        "files": handoff_files,
    }
    return {
        "assets": assets,
        "policy": policy_payload,
        "keyring": generation_keyring,
        "checkpoint": canonical(checkpoint),
        "handoff": canonical(handoff),
        "identity": handoff["identity"],
    }


def materialize(output):
    if output.exists() or output.is_symlink():
        raise ValueError("development generation output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".development-generation-", dir=output.parent))
    complete = False
    try:
        handoff = stage / "handoff"
        trust = stage / "trust"
        handoff.mkdir(mode=0o700)
        trust.mkdir(mode=0o700)
        documents = build_documents()
        for name, payload in documents["assets"].items():
            write(handoff / name, payload)
        write(handoff / HANDOFF_FILENAME, documents["handoff"])
        write(trust / "policy.json", documents["policy"])
        write(trust / "opemos-userspace-lock-generations.gpg", documents["keyring"])
        write(trust / "checkpoint.json", documents["checkpoint"])
        verifier = trust / "development-gpgv"
        write(verifier, (
            "#!/bin/sh\n"
            "printf '%s\\n' '[GNUPG:] NEWSIG' "
            "'[GNUPG:] KEY_CONSIDERED AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA 0' "
            "'[GNUPG:] VALIDSIG AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA 2026-09-03 1788436800 0 4 0 1 10 00 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'\n"
        ).encode(), 0o500)
        summary = {"schemaVersion": 1, "trust": "development-test-only", "target": TARGET, "generation": documents["identity"]}
        write(stage / "development-generation.json", canonical(summary))
        for directory in (handoff, trust, stage):
            directory.chmod(0o500)
        os.replace(stage, output)
        complete = True
        return summary
    finally:
        if not complete and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-test", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.development_test:
        raise SystemExit("development generation creation requires --development-test")
    try:
        result = materialize(args.output)
    except (OSError, ValueError) as error:
        raise SystemExit(f"generate_development_appliance_generation.py: {error}") from None
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
