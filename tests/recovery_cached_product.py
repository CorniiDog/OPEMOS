#!/usr/bin/env python3
"""Exact cached product recovery contract regression."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "lib/recovery_cached_product.py"
CONTROL = ROOT / "bootstrap/recoveryctl.sh"
STEAMOS = "3.8.16"
KERNEL = "6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45"
NVIDIA = "575.64.05"
REVISION = "a" * 40


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture(root):
    root.mkdir(mode=0o700)
    files = {
        "archive": ("nvidia-open-exact.tar.gz", b"exact gzip fixture"),
        "checksum": ("nvidia-open-exact.tar.gz.sha256", b""),
        "provenance": ("nvidia-open-exact.provenance.json", b'{"exact":true}\n'),
        "buildInfo": ("nvidia-open-exact.build-info.txt", b"exact=true\n"),
    }
    archive_hash = digest(files["archive"][1])
    files["checksum"] = (files["checksum"][0],
                         f"{archive_hash}  {files['archive'][0]}\n".encode())
    outputs = {}
    for role, (name, payload) in files.items():
        (root / name).write_bytes(payload)
        outputs[role] = {"name": name, "bytes": len(payload), "sha256": digest(payload)}
    result = {
        "schemaVersion": 1, "status": "materialized",
        "target": {"steamosVersion": STEAMOS, "kernelVersion": KERNEL,
                   "nvidiaVersion": NVIDIA, "architecture": "x86_64"},
        "core": {"repository": "CorniiDog/OPEMOS", "commit": REVISION},
        "source": {"repository": "CorniiDog/open-gpu-kernel-modules-steamos",
                   "commit": "b" * 40},
        "release": {"repository": "CorniiDog/OPEMOS", "tag": "exact-fixture"},
        "representation": {"productMember": "payload/nvidia-driver.tar.zst",
                           "installerContainer": "tar+gzip", "modules": "ko.zst",
                           "conversion": "none-byte-identical"},
        "outputs": outputs,
    }
    materialization = root / "result.json"
    materialization.write_bytes(canonical(result))
    return materialization, result


def run(*arguments, success=True):
    completed = subprocess.run([str(TOOL), *map(str, arguments)], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert (completed.returncode == 0) is success, (completed.stdout, completed.stderr)
    return completed


def exact_args():
    return ("--steamos", STEAMOS, "--kernel", KERNEL, "--nvidia", NVIDIA,
            "--support-revision", REVISION)


def main():
    with tempfile.TemporaryDirectory(prefix="recovery-cached-product-") as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        source = root / "source"
        materialization, result = fixture(source)
        cache = root / "cached-repair"
        staged = run("stage", *exact_args(), "--materialization", materialization,
                     "--input-dir", source, "--destination", cache)
        assert json.loads(staged.stdout) == result
        assert cache.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in cache.iterdir())
        shown = json.loads(run("show", *exact_args(), "--directory", cache).stdout)
        assert shown["target"]["kernelVersion"] == KERNEL
        assert shown["paths"]["archive"] == str((cache / result["outputs"]["archive"]["name"]).resolve())
        assert run("show", "--steamos", STEAMOS, "--kernel", KERNEL + "-wrong",
                   "--nvidia", NVIDIA, "--support-revision", REVISION,
                   "--directory", cache, success=False).returncode != 0

        archive = cache / result["outputs"]["archive"]["name"]
        archive.write_bytes(b"tampered")
        assert run("show", *exact_args(), "--directory", cache, success=False).returncode != 0

        second = root / "second-source"
        second_result, _ = fixture(second)
        hostile = root / "hostile-cache"
        run("stage", *exact_args(), "--materialization", second_result,
            "--input-dir", second, "--destination", hostile)
        (hostile / "unexpected").write_text("no", encoding="utf-8")
        assert run("show", *exact_args(), "--directory", hostile, success=False).returncode != 0

    control = CONTROL.read_text(encoding="utf-8")
    cached = control.index('if [[ -d "$CACHED_PRODUCT" ]]')
    network = control.index("curl -fsS --connect-timeout 10 --max-time 20")
    assert cached < network
    cached_block = control[cached:network]
    assert 'recovery_cached_product.py" show' in cached_block
    assert 'bootstrap/install.sh"' in cached_block
    assert "canonical_exact_cached_install" in cached_block
    assert "exact_cached_repair_failed" in cached_block
    assert "online_install.sh" not in cached_block


if __name__ == "__main__":
    main()
