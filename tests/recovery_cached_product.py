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
    cached = control.index('if [[ -e "$CACHED_PRODUCT" || -L "$CACHED_PRODUCT" ]]')
    network = control.index("curl -fsS --connect-timeout 10 --max-time 20")
    assert cached < network
    cached_block = control[cached:network]
    assert 'recovery_cached_product.py" show' in cached_block
    assert 'bootstrap/install.sh"' in cached_block
    assert "canonical_exact_cached_install" in cached_block
    assert "exact_cached_repair_failed" in cached_block
    assert 'run_cancellable "$ONLINE_INSTALL"' not in cached_block

    # Execute the automatic recovery entry point: every object at the exact
    # cache location is validated before networking, while true absence alone
    # preserves the network path.
    with tempfile.TemporaryDirectory(prefix="recovery-cache-flow-") as temporary:
        flow = Path(temporary)
        policy = flow / "policy"
        policy.mkdir(mode=0o755)
        (policy / "support-revision").write_text(REVISION + "\n", encoding="utf-8")
        (policy / "nvidia-version").write_text(NVIDIA + "\n", encoding="utf-8")
        (policy / "support-revision").chmod(0o644)
        (policy / "nvidia-version").chmod(0o644)
        status = flow / "status.py"
        status.write_text("""#!/usr/bin/env python3
import json
print(json.dumps({
    "schemaVersion": 1, "status": "fallback-active",
    "reason": "module_payload_mismatch",
    "target": {"kernelVersion": %r, "nvidiaVersion": %r},
    "moduleVerification": {"status": "failed", "records": []},
    "fallback": {"active": True, "profile": "console"},
    "actions": ["repair-exact-kernel"],
}, sort_keys=True, separators=(",", ":")))
""" % (KERNEL, NVIDIA), encoding="utf-8")
        status.chmod(0o755)
        mockbin = flow / "bin"
        mockbin.mkdir()
        curl_marker = flow / "curl-called"
        online_marker = flow / "online-called"
        (mockbin / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (mockbin / "curl").write_text(
            f"#!/bin/sh\ntouch {str(curl_marker)!r}\nexit 1\n", encoding="utf-8")
        online = flow / "online-install"
        online.write_text(
            f"#!/bin/sh\ntouch {str(online_marker)!r}\nexit 1\n", encoding="utf-8")
        (mockbin / "flock").chmod(0o755)
        (mockbin / "curl").chmod(0o755)
        online.chmod(0o755)

        def recovery(cache, test_root):
            test_root.mkdir(exist_ok=True)
            (test_root / "etc").mkdir(exist_ok=True)
            (test_root / "etc/os-release").write_text(
                f'ID=steamos\nVERSION_ID="{STEAMOS}"\n', encoding="utf-8")
            (test_root / "var/lib/open-gpu-kernel-modules-steamos-support/recovery").mkdir(
                parents=True, exist_ok=True)
            environment = {
                **os.environ,
                "PATH": f"{mockbin}:{os.environ['PATH']}",
                "PROJECT_TEST_MODE": "1",
                "PROJECT_TEST_ROOT": str(test_root),
                "PROJECT_TEST_POLICY_ROOT": str(policy),
                "PROJECT_TEST_STATUS_TOOL": str(status),
                "PROJECT_TEST_CACHED_PRODUCT": str(cache),
                "PROJECT_TEST_ONLINE_INSTALL": str(online),
            }
            return subprocess.run(
                [str(CONTROL), "repair-auto", "--json"], env=environment,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

        wrong_type = flow / "wrong-type"
        wrong_type.write_text("not a cache directory", encoding="utf-8")
        rejected = recovery(wrong_type, flow / "wrong-root")
        assert rejected.returncode != 0
        assert "invalid" in rejected.stderr.lower(), rejected.stderr
        assert not curl_marker.exists() and not online_marker.exists()

        dangling = flow / "dangling"
        dangling.symlink_to(flow / "missing-target")
        rejected = recovery(dangling, flow / "dangling-root")
        assert rejected.returncode != 0
        assert "invalid" in rejected.stderr.lower(), rejected.stderr
        assert not curl_marker.exists() and not online_marker.exists()

        absent = flow / "absent"
        offline = recovery(absent, flow / "absent-root")
        assert offline.returncode == 75, (offline.stdout, offline.stderr)
        assert curl_marker.is_file()
        assert not online_marker.exists()
        assert json.loads(offline.stdout) == {
            "action": "retry_scheduled", "reason": "network_unavailable",
            "schemaVersion": 1, "status": "offline_waiting",
        }

        # A validated exact cached product skips acquisition and may enter the
        # installer directly from the initial offline-waiting transaction. The
        # deliberately non-archive fixture then makes the real installer fail
        # before mutation, proving recoveryctl reached it and recorded the
        # existing bounded retry outcome without touching either network path.
        curl_marker.unlink()
        cached_source = flow / "cached-source"
        cached_materialization, _ = fixture(cached_source)
        cached_product = flow / "exact-cache"
        run("stage", *exact_args(), "--materialization", cached_materialization,
            "--input-dir", cached_source, "--destination", cached_product)
        cached_failure_root = flow / "cached-failure-root"
        cached_failure = recovery(cached_product, cached_failure_root)
        assert cached_failure.returncode == 75, (
            cached_failure.stdout, cached_failure.stderr)
        assert "Could not determine NVIDIA userspace driver version" not in cached_failure.stderr
        assert json.loads(cached_failure.stdout) == {
            "action": "timer_and_connectivity",
            "reason": "exact_cached_repair_failed",
            "schemaVersion": 1,
            "status": "retry_scheduled",
        }
        transaction = json.loads((cached_failure_root /
            "var/lib/open-gpu-kernel-modules-steamos-support/recovery/transaction.json"
        ).read_text(encoding="utf-8"))
        assert transaction["phase"] == "retry_scheduled"
        assert transaction["reason"] == "exact_cached_repair_failed"
        assert transaction["attempt"] == 2
        assert transaction["automaticRetry"] is True
        assert not curl_marker.exists() and not online_marker.exists()

        # The intact exact cache remains eligible for the next automatic timer
        # after a bounded installer failure. It re-enters installation from
        # retry_scheduled, carries the same pinned target into the driver-absent
        # installer, and records two more durable transitions without network.
        cached_retry = recovery(cached_product, cached_failure_root)
        assert cached_retry.returncode == 75, (
            cached_retry.stdout, cached_retry.stderr)
        assert "Could not determine NVIDIA userspace driver version" not in cached_retry.stderr
        assert json.loads(cached_retry.stdout) == {
            "action": "timer_and_connectivity",
            "reason": "exact_cached_repair_failed",
            "schemaVersion": 1,
            "status": "retry_scheduled",
        }
        retried_transaction = json.loads((cached_failure_root /
            "var/lib/open-gpu-kernel-modules-steamos-support/recovery/transaction.json"
        ).read_text(encoding="utf-8"))
        assert retried_transaction["phase"] == "retry_scheduled"
        assert retried_transaction["reason"] == "exact_cached_repair_failed"
        assert retried_transaction["attempt"] == 4
        assert retried_transaction["automaticRetry"] is True
        assert not curl_marker.exists() and not online_marker.exists()


if __name__ == "__main__":
    main()
