#!/usr/bin/env python3
"""Installer materialization contract for compiled-driver products."""

import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib" / "materialize_driver_product.py"
SCHEMA = json.loads((
    ROOT / "contracts" / "schemas" / "driver-product-materialization-v1.schema.json"
).read_text(encoding="utf-8"))
CORE_COMMIT = "a" * 40
SOURCE_COMMIT = "b" * 40
STEAMOS = "3.8.14"
NVIDIA = "575.64.05"
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfixture"
TARGET = {
    "steamosVersion": STEAMOS,
    "kernelVersion": KERNEL,
    "nvidiaVersion": NVIDIA,
    "architecture": "x86_64",
}
TAG = f"steamos-{STEAMOS}-nvidia-{NVIDIA}-k{KERNEL}-modules-zstd-r1"
SOURCE_NAME = f"nvidia-open-{TAG}-x86_64.tar.gz"
PRODUCT_NAME = f"opemos-driver-{TAG}-x86_64.tar.gz"
MODULES = (
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko",
    "nvidia-uvm.ko",
)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def add(archive, name, payload=b"", *, directory=False):
    member = tarfile.TarInfo(name)
    member.uid = member.gid = member.mtime = 0
    member.mode = 0o755 if directory else 0o644
    if directory:
        member.type = tarfile.DIRTYPE
        archive.addfile(member)
    else:
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def gzip_tar(members):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, payload, directory in members:
                add(archive, name, payload, directory=directory)
    return buffer.getvalue()


def fixture(root, *, invalid_container=False, wrong_provenance_target=False,
            invalid_module_representation=False, source_name=SOURCE_NAME):
    root.mkdir(parents=True)
    module_payloads = {
        name: (b"bad!" if invalid_module_representation and index == 0
               else b"\x28\xb5\x2f\xfd") + f"fixture:{name}\n".encode()
        for index, name in enumerate(MODULES)
    }
    provenance_target = dict(TARGET)
    if wrong_provenance_target:
        provenance_target["kernelVersion"] = "wrong-kernel"
    provenance = {
        "schemaVersion": 1,
        "trust": "locally-built-verified",
        "target": provenance_target,
        "artifact": {
            "archive": source_name,
            "releaseTag": TAG,
            "representation": "ko.zst",
            "revision": 1,
        },
        "repack": {
            "schemaVersion": 1,
            "sourceReleaseTag": TAG.removesuffix("-modules-zstd-r1"),
            "payloadIdentity": "byte-identical",
            "encoding": "zstd-19-t1",
            "encoder": {"name": "zstd", "version": "1.5.7"},
            "sourceArchiveSha256": "c" * 64,
            "sourceProvenanceSha256": "d" * 64,
        },
        "support": {
            "repository": "CorniiDog/OPEMOS", "commit": CORE_COMMIT, "dirty": 0,
        },
        "source": {
            "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
            "commit": SOURCE_COMMIT,
            "dirty": 0,
        },
        "modules": [
            {
                "name": name,
                "version": NVIDIA,
                "architecture": "x86_64",
                "vermagic": f"{KERNEL} SMP preempt",
                "representation": "ko.zst",
                "representationFilename": name + ".zst",
                "sha256": digest(module_payloads[name]),
                "payloadSha256": digest(f"decoded:{name}\n".encode()),
            }
            for name in MODULES
        ],
    }
    provenance_bytes = canonical(provenance)
    build_info = "\n".join((
        "schema_version=1",
        f"steamos_version={STEAMOS}",
        f"kernel_version={KERNEL}",
        f"nvidia_version={NVIDIA}",
        "build_architecture=x86_64",
        "trust_classification=locally-built-verified",
        f"release_tag={TAG}",
        f"release_asset={source_name}",
        "support_repository=CorniiDog/OPEMOS",
        f"support_commit={CORE_COMMIT}",
        "source_repository=CorniiDog/open-gpu-kernel-modules-steamos",
        f"source_commit={SOURCE_COMMIT}",
        "",
    )).encode()
    payload = gzip_tar([
        ("modules/", b"", True),
        ("BUILD-INFO.txt", build_info, False),
        ("PROVENANCE.json", provenance_bytes, False),
        *[(f"modules/{name}.zst", module_payloads[name], False) for name in MODULES],
    ])
    if invalid_container:
        payload = b"not a gzip installer archive"
    receipt = canonical({
        "schemaVersion": 1,
        "status": "validated",
        "target": TARGET,
        "payloadSha256": digest(payload),
        "provenanceSha256": digest(provenance_bytes),
        "validator": {"repository": "CorniiDog/OPEMOS", "commit": CORE_COMMIT},
    })
    metadata = [
        {"role": "build-info", "path": "metadata/BUILD-INFO.txt",
         "bytes": len(build_info), "sha256": digest(build_info)},
        {"role": "provenance", "path": "metadata/PROVENANCE.json",
         "bytes": len(provenance_bytes), "sha256": digest(provenance_bytes)},
        {"role": "validation-receipt", "path": "metadata/VALIDATION-RECEIPT.json",
         "bytes": len(receipt), "sha256": digest(receipt)},
    ]
    product_manifest = canonical({
        "schemaVersion": 1,
        "kind": "opemos-compiled-driver",
        "creator": {
            "component": "OPEMOS Core", "repository": "CorniiDog/OPEMOS",
            "commit": CORE_COMMIT, "cleanupOwner": "core",
        },
        "target": TARGET,
        "compatibility": {"kernel": "exact", "architecture": "exact", "fallback": False},
        "capabilities": ["open-kernel-modules", "offline-install", "initramfs-integration"],
        "payload": {"path": "payload/nvidia-driver.tar.zst", "sourceName": source_name,
                    "bytes": len(payload), "sha256": digest(payload)},
        "metadata": metadata,
        "install": {
            "moduleDestination": f"/usr/lib/modules/{KERNEL}/updates/nvidia",
            "runDepmod": True,
            "initramfs": {"required": True, "kernelVersion": KERNEL},
        },
        "source": {
            "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
            "commit": SOURCE_COMMIT,
        },
        "releaseTag": TAG,
    })
    product_bytes = gzip_tar([
        ("DRIVER-MANIFEST.json", product_manifest, False),
        ("payload/nvidia-driver.tar.zst", payload, False),
        ("metadata/BUILD-INFO.txt", build_info, False),
        ("metadata/PROVENANCE.json", provenance_bytes, False),
        ("metadata/VALIDATION-RECEIPT.json", receipt, False),
    ])
    product = root / PRODUCT_NAME
    product.write_bytes(product_bytes)
    sidecar = root / (PRODUCT_NAME + ".sha256")
    sidecar.write_text(f"{digest(product_bytes)}  {PRODUCT_NAME}\n", encoding="utf-8")
    bundle = {
        "schemaVersion": 1,
        "kind": "opemos-driver-binary-bundle",
        "contract": {
            "driverProductManifestSchemaVersion": 1,
            "releaseBundleManifestSchemaVersion": 1,
        },
        "release": {"repository": "CorniiDog/OPEMOS", "tag": TAG},
        "core": {"repository": "CorniiDog/OPEMOS", "commit": CORE_COMMIT},
        "source": {
            "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
            "commit": SOURCE_COMMIT,
        },
        "target": TARGET,
        "compatibility": {"architecture": "exact", "fallback": False, "kernel": "exact"},
        "assets": [
            {"role": "driver-product", "name": PRODUCT_NAME,
             "bytes": len(product_bytes), "sha256": digest(product_bytes)},
            {"role": "sha256-sidecar", "name": sidecar.name,
             "bytes": sidecar.stat().st_size, "sha256": digest(sidecar.read_bytes())},
        ],
    }
    manifest = root / "bundle.manifest.json"
    manifest.write_bytes(canonical(bundle))
    return manifest, digest(manifest.read_bytes()), payload, provenance_bytes, build_info


def run(manifest, manifest_hash, output, *extra):
    return subprocess.run([
        str(TOOL),
        "--manifest", str(manifest),
        "--asset-dir", str(manifest.parent),
        "--expected-manifest-sha256", manifest_hash,
        "--expected-core-commit", CORE_COMMIT,
        "--expected-steamos", STEAMOS,
        "--expected-kernel", KERNEL,
        "--expected-nvidia", NVIDIA,
        "--expected-architecture", "x86_64",
        "--output-dir", str(output),
        *extra,
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def assert_empty(path):
    assert not path.exists() or not list(path.iterdir()), list(path.iterdir())


def main():
    with tempfile.TemporaryDirectory(prefix="materialize-driver-product-") as temporary:
        root = Path(temporary)
        manifest, manifest_hash, payload, provenance, build_info = fixture(root / "valid")
        output = root / "output"
        completed = run(manifest, manifest_hash, output)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        jsonschema.validate(result, SCHEMA)
        assert result["status"] == "materialized"
        assert result["target"] == TARGET
        assert result["core"]["commit"] == CORE_COMMIT
        assert result["representation"] == {
            "productMember": "payload/nvidia-driver.tar.zst",
            "installerContainer": "tar+gzip",
            "modules": "ko.zst",
            "conversion": "none-byte-identical",
        }
        archive = output / SOURCE_NAME
        assert archive.read_bytes() == payload
        assert (output / (SOURCE_NAME + ".sha256")).read_text() == (
            f"{digest(payload)}  {SOURCE_NAME}\n"
        )
        assert (output / (SOURCE_NAME[:-7] + ".provenance.json")).read_bytes() == provenance
        assert (output / (SOURCE_NAME[:-7] + ".build-info.txt")).read_bytes() == build_info
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        collision = run(manifest, manifest_hash, output)
        assert collision.returncode != 0 and "already exists" in collision.stderr
        assert before == {path.name: path.read_bytes() for path in output.iterdir()}

        wrong_target = subprocess.run([
            str(TOOL), "--manifest", str(manifest), "--asset-dir", str(manifest.parent),
            "--expected-manifest-sha256", manifest_hash,
            "--expected-core-commit", CORE_COMMIT,
            "--expected-steamos", STEAMOS,
            "--expected-kernel", "wrong-kernel",
            "--expected-nvidia", NVIDIA,
            "--expected-architecture", "x86_64",
            "--output-dir", str(root / "wrong-target-output"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wrong_target.returncode != 0 and "exact requested target" in wrong_target.stderr
        assert_empty(root / "wrong-target-output")

        assert run(manifest, "0" * 64, root / "wrong-pin").returncode != 0
        assert_empty(root / "wrong-pin")

        for label, options, error in (
            ("container", {"invalid_container": True}, "gzip installer archive"),
            ("provenance", {"wrong_provenance_target": True}, "provenance does not match"),
            ("module", {"invalid_module_representation": True}, "non-zstd module"),
            ("source-name", {"source_name": "nvidia-driver.tar.zst"}, "representation is unsupported"),
        ):
            hostile_manifest, hostile_hash, *_ = fixture(root / label, **options)
            hostile_output = root / f"{label}-output"
            rejected = run(hostile_manifest, hostile_hash, hostile_output)
            assert rejected.returncode != 0 and error in rejected.stderr, (
                label, rejected.stdout, rejected.stderr
            )
            assert_empty(hostile_output)

        tampered_manifest, tampered_hash, *_ = fixture(root / "tampered")
        product = tampered_manifest.parent / PRODUCT_NAME
        product.write_bytes(product.read_bytes() + b"tamper")
        rejected = run(tampered_manifest, tampered_hash, root / "tampered-output")
        assert rejected.returncode != 0 and "size or hash" in rejected.stderr
        assert_empty(root / "tampered-output")


if __name__ == "__main__":
    main()
