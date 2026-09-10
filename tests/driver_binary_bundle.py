#!/usr/bin/env python3
"""Deterministic and hostile-input tests for the driver binary release bundle."""

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import publisher  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "lib" / "build_driver_product.py"
BUNDLE = ROOT / "lib" / "driver_binary_bundle.py"


def run(*arguments):
    return subprocess.run(
        [sys.executable, str(BUNDLE), *map(str, arguments)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def product(inputs, output):
    completed = subprocess.run(
        [
            sys.executable,
            str(PRODUCT),
            "--archive", str(inputs[0]),
            "--checksum", str(inputs[1]),
            "--build-info", str(inputs[2]),
            "--provenance", str(inputs[3]),
            "--output-dir", str(output),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    archive = Path(result["archive"])
    return archive, Path(str(archive) + ".sha256")


def create(archive, sidecar, output):
    return run(
        "create",
        "--archive", archive,
        "--sidecar", sidecar,
        "--output", output,
        "--release-tag", publisher.TAG,
    )


def validate(manifest, assets, digest, commit=publisher.SUPPORT_COMMIT):
    return run(
        "validate",
        "--manifest", manifest,
        "--asset-dir", assets,
        "--expected-manifest-sha256", digest,
        "--expected-core-commit", commit,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="driver-binary-bundle-") as temporary:
        root = Path(temporary)
        archive, sidecar = product(publisher.fixture(root / "inputs"), root / "assets")
        first = root / "first.json"
        second = root / "second.json"
        created = create(archive, sidecar, first)
        repeated = create(archive, sidecar, second)
        assert created.returncode == repeated.returncode == 0, (
            created.stderr,
            repeated.stderr,
        )
        assert first.read_bytes() == second.read_bytes()
        digest = hashlib.sha256(first.read_bytes()).hexdigest()
        result = json.loads(first.read_text(encoding="utf-8"))
        assert result["contract"] == {
            "driverProductManifestSchemaVersion": 1,
            "releaseBundleManifestSchemaVersion": 1,
        }
        assert result["core"] == {
            "repository": "CorniiDog/OPEMOS",
            "commit": publisher.SUPPORT_COMMIT,
        }
        assert result["source"]["commit"] == publisher.SOURCE_COMMIT
        assert result["compatibility"] == {
            "architecture": "exact",
            "fallback": False,
            "kernel": "exact",
        }
        assert [record["role"] for record in result["assets"]] == [
            "driver-product",
            "sha256-sidecar",
        ]
        checked = validate(first, archive.parent, digest)
        assert checked.returncode == 0, checked.stderr
        assert json.loads(checked.stdout)["status"] == "validated"

        assert create(archive, sidecar, first).returncode != 0
        assert validate(first, archive.parent, "0" * 64).returncode != 0
        assert validate(
            first, archive.parent, digest, "f" * 40
        ).returncode != 0

        original = archive.read_bytes()
        archive.write_bytes(original + b"tamper")
        assert validate(first, archive.parent, digest).returncode != 0
        archive.write_bytes(original)

        sidecar_original = sidecar.read_bytes()
        sidecar.write_bytes(sidecar_original + b"tamper")
        assert validate(first, archive.parent, digest).returncode != 0
        sidecar.write_bytes(sidecar_original)

        hostile_archive, hostile_sidecar = product(
            publisher.fixture(root / "hostile-inputs"), root / "hostile-assets"
        )
        with tarfile.open(hostile_archive, "r:gz") as source:
            members = [(member, source.extractfile(member).read()) for member in source.getmembers()]
        with tarfile.open(hostile_archive, "w:gz") as target:
            for member, payload in members:
                if member.name == "payload/nvidia-driver.tar.zst":
                    payload += b"changed"
                    member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
        hostile_sidecar.write_text(
            f"{hashlib.sha256(hostile_archive.read_bytes()).hexdigest()}  "
            f"{hostile_archive.name}\n",
            encoding="utf-8",
        )
        assert create(
            hostile_archive, hostile_sidecar, root / "hostile.json"
        ).returncode != 0

        linked = root / "linked.json"
        linked.symlink_to(first)
        assert run(
            "validate",
            "--manifest", linked,
            "--asset-dir", archive.parent,
        ).returncode != 0

        sidecar.unlink()
        assert validate(first, archive.parent, digest).returncode != 0


if __name__ == "__main__":
    main()
