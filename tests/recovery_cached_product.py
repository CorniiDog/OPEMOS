#!/usr/bin/env python3
"""Exact cached product recovery contract regression."""

import hashlib
import json
import os
import subprocess
import tarfile
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


def fixture(root, archive_payload=b"exact gzip fixture"):
    root.mkdir(mode=0o700)
    files = {
        "archive": ("nvidia-open-exact.tar.gz", archive_payload),
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


def valid_archive(root):
    payload = root / "valid-driver.tar.gz"
    staging = root / "valid-driver"
    modules = staging / "modules"
    modules.mkdir(parents=True)
    (staging / "BUILD-INFO.txt").write_text(
        f"steamos_version={STEAMOS}\nkernel_version={KERNEL}\n"
        f"nvidia_version={NVIDIA}\n", encoding="utf-8")
    for name in ("nvidia", "nvidia-drm", "nvidia-modeset", "nvidia-peermem",
                 "nvidia-uvm"):
        (modules / f"{name}.ko.zst").write_bytes((name + " fixture\n").encode())
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(staging / "BUILD-INFO.txt", arcname="BUILD-INFO.txt")
        archive.add(modules, arcname="modules")
    return payload.read_bytes()


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
import os
from pathlib import Path
root = Path(os.environ["PROJECT_TEST_ROOT"])
module_dir = root / %r
verified = module_dir.is_dir() and len(list(module_dir.glob("*.ko.zst"))) == 5
print(json.dumps({
    "schemaVersion": 1, "status": "fallback-active",
    "reason": "exact_nvidia_ready" if verified else "module_payload_mismatch",
    "target": {"kernelVersion": %r, "nvidiaVersion": %r},
    "moduleVerification": {"status": "verified" if verified else "failed",
                           "records": []},
    "fallback": {"active": True, "profile": "console"},
    "actions": ["disable-fallback"] if verified else ["repair-exact-kernel"],
}, sort_keys=True, separators=(",", ":")))
""" % ("usr/lib/modules/" + KERNEL + "/updates/open-gpu-kernel-modules-steamos",
         KERNEL, NVIDIA), encoding="utf-8")
        status.chmod(0o755)
        mockbin = flow / "bin"
        mockbin.mkdir()
        curl_marker = flow / "curl-called"
        online_marker = flow / "online-called"
        readonly_marker = flow / "readonly-enabled"
        readonly_log = flow / "readonly.log"
        immutable_home = flow / "root-home"
        immutable_home.mkdir()
        installer_tmp = flow / "var-tmp"
        installer_tmp.mkdir()
        (mockbin / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (mockbin / "curl").write_text(
            f"#!/bin/sh\ntouch {str(curl_marker)!r}\nexit 1\n", encoding="utf-8")
        online = flow / "online-install"
        online.write_text(
            f"#!/bin/sh\ntouch {str(online_marker)!r}\nexit 1\n", encoding="utf-8")
        (mockbin / "sudo").write_text(
            "#!/bin/sh\n[ \"${1:-}\" = -v ] && exit 0\nexec \"$@\"\n", encoding="utf-8")
        (mockbin / "uname").write_text(
            f"#!/bin/sh\n[ \"${{1:-}}\" = -r ] && printf '%s\\n' {KERNEL!r} && exit 0\n"
            "exec /usr/bin/uname \"$@\"\n", encoding="utf-8")
        (mockbin / "zstd").write_text(
            "#!/bin/sh\n[ \"${1:-}\" = -q ] && [ \"${2:-}\" = -t ] && exit 0\nexit 1\n",
            encoding="utf-8")
        (mockbin / "depmod").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (mockbin / "mkdir").write_text("""#!/bin/sh
case " $* " in
  *" $PROJECT_TEST_IMMUTABLE_HOME"*|*" $PROJECT_TEST_IMMUTABLE_HOME/"*)
    [ ! -e "$PROJECT_TEST_READONLY_MARKER" ] || exit 30 ;;
esac
exec /usr/bin/mkdir "$@"
""", encoding="utf-8")
        (mockbin / "steamos-readonly").write_text("""#!/bin/sh
printf '%s\n' "${1:-}" >> "$PROJECT_TEST_READONLY_LOG"
case "${1:-}" in
  status) [ -e "$PROJECT_TEST_READONLY_MARKER" ] && echo enabled || echo disabled ;;
  disable) rm -f "$PROJECT_TEST_READONLY_MARKER" ;;
  enable) : > "$PROJECT_TEST_READONLY_MARKER" ;;
  *) exit 2 ;;
esac
""", encoding="utf-8")
        (mockbin / "modinfo").write_text("""#!/bin/sh
case "$1:$2" in
  -F:vermagic) printf '%s SMP\n' "$PROJECT_TEST_KERNEL" ;;
  -F:version) printf '%s\n' "$PROJECT_TEST_NVIDIA" ;;
  -n:nvidia) printf '%s/usr/lib/modules/%s/updates/open-gpu-kernel-modules-steamos/nvidia.ko.zst\n' "$PROJECT_TEST_ROOT" "$PROJECT_TEST_KERNEL" ;;
  *) exit 1 ;;
esac
""", encoding="utf-8")
        for command in ("flock", "curl", "sudo", "uname", "zstd", "depmod", "mkdir",
                        "steamos-readonly", "modinfo"):
            (mockbin / command).chmod(0o755)
        online.chmod(0o755)

        def recovery(cache, test_root):
            test_root.mkdir(exist_ok=True)
            (test_root / "etc").mkdir(exist_ok=True)
            (test_root / "etc/os-release").write_text(
                f'ID=steamos\nVERSION_ID="{STEAMOS}"\n', encoding="utf-8")
            recovery_root = (test_root /
                "var/lib/open-gpu-kernel-modules-steamos-support/recovery")
            recovery_root.mkdir(parents=True, exist_ok=True)
            state = recovery_root / "state.json"
            if not state.exists():
                state.write_text(
                    '{"active":true,"profile":"console","schemaVersion":1}\n',
                    encoding="utf-8")
                state.chmod(0o644)
            (test_root / "usr/lib/modules" / KERNEL / "updates").mkdir(
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
                "PROJECT_TEST_KERNEL": KERNEL,
                "PROJECT_TEST_NVIDIA": NVIDIA,
                "PROJECT_TEST_IMMUTABLE_HOME": str(immutable_home),
                "PROJECT_TEST_READONLY_MARKER": str(readonly_marker),
                "PROJECT_TEST_READONLY_LOG": str(readonly_log),
                "HOME": str(immutable_home),
                "TMPDIR": str(installer_tmp),
            }
            environment.pop("USER", None)
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
        readonly_marker.touch()
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

        # A new exact validated materialization can service the next automatic
        # timer after the bounded failure. Run the real installer with USER
        # absent, as it is for the systemd repair service, and prove the five
        # exact modules survive the successful retry without either network path.
        valid_source = flow / "valid-source"
        archive_payload = valid_archive(flow)
        valid_materialization, _ = fixture(valid_source, archive_payload)
        valid_product = flow / "valid-cache"
        run("stage", *exact_args(), "--materialization", valid_materialization,
            "--input-dir", valid_source, "--destination", valid_product)
        cached_retry = recovery(valid_product, cached_failure_root)
        assert cached_retry.returncode == 0, (
            cached_retry.stdout, cached_retry.stderr)
        target = (cached_failure_root / "usr/lib/modules" / KERNEL / "updates" /
                  "open-gpu-kernel-modules-steamos")
        assert sorted(path.name for path in target.glob("*.ko.zst")) == [
            "nvidia-drm.ko.zst", "nvidia-modeset.ko.zst", "nvidia-peermem.ko.zst",
            "nvidia-uvm.ko.zst", "nvidia.ko.zst",
        ]
        retried_transaction = json.loads((cached_failure_root /
            "var/lib/open-gpu-kernel-modules-steamos-support/recovery/transaction.json"
        ).read_text(encoding="utf-8"))
        assert retried_transaction["phase"] == "restored"
        assert retried_transaction["reason"] == "exact_nvidia_restored"
        assert retried_transaction["attempt"] >= 4
        assert retried_transaction["active"] is False
        backup_root = (installer_tmp / "open-gpu-kernel-modules-steamos-support" /
            "backups" / KERNEL)
        assert len(list(backup_root.iterdir())) == 1
        assert not any(immutable_home.iterdir())
        assert not any(installer_tmp.iterdir())
        assert readonly_marker.is_file()
        assert readonly_log.read_text(encoding="utf-8").splitlines() == [
            "status", "disable", "enable",
        ]
        assert not curl_marker.exists() and not online_marker.exists()


if __name__ == "__main__":
    main()
