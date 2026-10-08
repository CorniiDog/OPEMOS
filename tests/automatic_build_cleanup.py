#!/usr/bin/env python3
"""Prove exact-source refusal and owned-container failure/cancellation cleanup."""
import os
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "40bd1b5d6d39ae4e4180b7a665df144b08854d14"
KERNEL = "6.18.50-valve2-1-neptune-618-gc7289a96b14d"


def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body)
    path.chmod(0o755)


def main():
    with tempfile.TemporaryDirectory(prefix="automatic-build-cleanup-") as temporary:
        root = Path(temporary)
        tools = root / "bin"
        tools.mkdir()
        releases = root / "releases.json"
        releases.write_text("[]\n")
        marker = root / "container-started"
        pid = root / "container-pid"
        executable(tools / "git", f'''if [[ "$1" == clone ]]; then mkdir "${{@: -1}}"; fi
if [[ "${{3:-}}" == rev-parse ]]; then echo {COMMIT}; fi
''')
        executable(tools / "podman", '''while [[ "$1" == --* ]]; do
  if [[ "$1" == --runroot ]]; then
    [[ ${#2} -le 50 && -d "$2" ]] || exit 125
    echo "$2" > "$MOCK_RUNTIME"
  fi
  shift 2
done
case "$1 $2" in
  "pull "*) exit 0;;
  "image inspect") printf 'registry.fedoraproject.org/fedora@sha256:%064d\\n' 0; exit 0;;
  "container exists") exit 1;;
  "run --rm")
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == --cidfile ]]; then printf '%064d\\n' 1 > "$2"; fi
      if [[ "$1" == -v && "$2" == *:/output:rw ]]; then output=${2%:/output:rw}; fi
      shift
    done
    echo $$ > "$MOCK_PID"
    touch "$MOCK_MARKER"
    if [[ "$MOCK_MODE" == cancel ]]; then sleep 30 & wait; fi
    if [[ "$MOCK_MODE" == success || "$MOCK_MODE" == collision ]]; then
      printf 'owned result\\n' > "$output/product"
      if [[ "$MOCK_MODE" == collision ]]; then
        mkdir "$MOCK_OUTPUT"
        printf 'concurrent preserved\\n' > "$MOCK_OUTPUT/sentinel"
      fi
      exit 0
    fi
    if [[ "$MOCK_MODE" == incompatible ]]; then
      cat > "$output/build-result.json" <<EOF
{"schemaVersion":1,"status":"failed","reason":"compiler_policy_mismatch","message":"Compiler mismatch","trust":"development-unverified","target":{"steamosVersion":"3.8.28","kernelVersion":"$MOCK_KERNEL","nvidiaVersion":"575.64.05","architecture":"x86_64"}}
EOF
    fi
    if [[ "$MOCK_MODE" == malformed ]]; then
      printf '%s\\n' "$MOCK_FAILURE_DOCUMENT" > "$output/build-result.json"
    fi
    exit 17;;
esac
exit 99
''')
        preexisting = root / "preexisting-cache"
        preexisting.write_text("preserved\n")
        runtime_parent = root / "overly-long-runtime-parent"
        runtime_parent.mkdir(mode=0o700)
        env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}",
               "TMPDIR": str(root), "MOCK_PID": str(pid), "MOCK_MARKER": str(marker),
               "MOCK_RUNTIME": str(root / "runtime-path"),
               "XDG_RUNTIME_DIR": str(runtime_parent),
               "MOCK_KERNEL": KERNEL,
               "SUPPORT_REPO": "CorniiDog/OPEMOS",
               "NVIDIA_BUILD_IMAGE": "registry.fedoraproject.org/fedora:42"}
        command = [str(ROOT / "bootstrap/build_automatic_for_target.sh"),
                   "3.8.28", KERNEL, "575.64.05", str(releases), str(root / "output")]
        env.update(MOCK_MODE="success", MOCK_OUTPUT=str(root / "output"))
        succeeded = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10)
        assert succeeded.returncode == 0, (succeeded.stdout, succeeded.stderr)
        assert (root / "output/product").read_text() == "owned result\n"
        assert not list(root.glob("opemos-automatic-build.*"))
        # Existing output refuses before acquisition and is never overwritten.
        marker.unlink()
        refused_existing = subprocess.run(command, env=env, text=True,
                                          capture_output=True, timeout=10)
        assert refused_existing.returncode != 0 and not marker.exists()
        assert (root / "output/product").read_text() == "owned result\n"
        shutil.rmtree(root / "output")
        env["MOCK_MODE"] = "collision"
        collision = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10)
        assert collision.returncode != 0, (collision.stdout, collision.stderr)
        assert sorted(p.name for p in (root / "output").iterdir()) == ["sentinel"]
        assert (root / "output/sentinel").read_text() == "concurrent preserved\n"
        assert not list(root.glob("opemos-automatic-build.*"))
        shutil.rmtree(root / "output")
        marker.unlink()
        env["MOCK_MODE"] = "fail"
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10)
        assert result.returncode == 17, (result.stdout, result.stderr)
        assert marker.exists() and not (root / "output").exists()
        assert not list(root.glob("opemos-automatic-build.*"))
        marker.unlink()
        # Wrong installed userspace cannot reach acquisition or the container.
        refused = command.copy()
        refused[3] = "580.119.02"
        result = subprocess.run(refused, env=env, text=True, capture_output=True, timeout=10)
        assert result.returncode != 0 and not marker.exists()
        assert not list(root.glob("opemos-automatic-build.*"))
        env["MOCK_MODE"] = "cancel"
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None
                time.sleep(0.02)
            assert marker.exists()
            child_pid = int(pid.read_text())
            process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=10)
            assert process.returncode == 143, (out, err)
            assert not list(root.glob("opemos-automatic-build.*"))
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("owned container command survived cancellation")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        assert preexisting.read_text() == "preserved\n"
        env["MOCK_MODE"] = "incompatible"
        failure = root / "build-failure.json"
        result = subprocess.run([*command, str(failure)], env=env, text=True,
                                capture_output=True, timeout=10)
        assert result.returncode == 17, (result.stdout, result.stderr)
        import json
        assert json.loads(failure.read_text())["reason"] == "compiler_policy_mismatch"
        assert failure.stat().st_mode & 0o777 == 0o600
        assert not list(root.glob("opemos-automatic-build.*"))
        valid_failure = json.loads(failure.read_text())
        failure.unlink()
        for mutation in (dict(schemaVersion=True), dict(trust="certified-published"),
                         dict(target={}), dict(reason="../wrong")):
            document = {**valid_failure, **mutation}
            env.update(MOCK_MODE="malformed", MOCK_FAILURE_DOCUMENT=json.dumps(document), PYTHONOPTIMIZE="1")
            rejected = subprocess.run([*command, str(failure)], env=env, text=True,
                                      capture_output=True, timeout=10)
            assert rejected.returncode != 0 and not failure.exists()
            assert not list(root.glob("opemos-automatic-build.*"))
            assert preexisting.read_text() == "preserved\n"
        env.pop("MOCK_FAILURE_DOCUMENT")
        env.pop("PYTHONOPTIMIZE")
        # Exercise failure through the documented raw public installer too,
        # rather than accepting helper-only cleanup evidence.
        support = root / "support"
        for directory in ("bootstrap", "lib", "policies", "profiles", "trust"):
            shutil.copytree(ROOT / directory, support / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        install_marker = root / "unexpected-install"
        for script in ("install.sh", "install_recovery_guardian.sh"):
            executable(support / "bootstrap" / script,
                       'touch "$MOCK_INSTALL_MARKER"; exit 99\n')
        executable(tools / "git", f'''if [[ "$1" == clone ]]; then
  if [[ "$*" == *CorniiDog/OPEMOS.git* ]]; then
    cp -a "$MOCK_SUPPORT" "${{@: -1}}"
  else
    mkdir "${{@: -1}}"
  fi
fi
if [[ "${{3:-}}" == rev-parse ]]; then
  if [[ "$2" == */support ]]; then echo "${{SUPPORT_REVISION,,}}"; else echo {COMMIT}; fi
fi
''')
        executable(tools / "nvidia-smi", 'echo 575.64.05\n')
        executable(tools / "uname", f'case "$1" in -r) echo {KERNEL};; -m) echo x86_64;; esac\n')
        executable(tools / "curl", '''while [[ $# -gt 0 ]]; do
  if [[ "$1" == -o ]]; then cp "$MOCK_RELEASES" "$2"; exit 0; fi
  shift
done
exit 99
''')
        system = root / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nVERSION_ID="3.8.28"\n')
        env.update(MOCK_MODE="fail", MOCK_SUPPORT=str(support), MOCK_RELEASES=str(releases),
                   MOCK_INSTALL_MARKER=str(install_marker), HOME=str(root / "home"),
                   SUPPORT_REVISION="a" * 40, PROJECT_TEST_MODE="1",
                   PROJECT_TEST_ROOT=str(root / "system"))
        marker.unlink()
        releases.write_text("[]\n")
        no_flag = subprocess.run(["bash", "-s", "--", "--yes"],
                                 input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                 cwd="/", env=env, capture_output=True, text=True, timeout=10)
        assert no_flag.returncode != 0 and not marker.exists() and not install_marker.exists()
        executable(tools / "nvidia-smi", 'exit 1\n')
        setup_log = root / "setup-arguments"
        env["MOCK_SETUP_LOG"] = str(setup_log)
        executable(support / "bootstrap/setup_nvidia.sh",
                   'printf "%s\\n" "$@" > "$MOCK_SETUP_LOG"; exit 77\n')
        for flags, expected in (([], ["-y"]), (["--build-as-fallback"], ["--build-as-fallback", "-y"])):
            setup_result = subprocess.run(["bash", "-s", "--", *flags, "--yes"],
                                          input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                          cwd="/", env=env, capture_output=True, text=True, timeout=10)
            assert setup_result.returncode == 77, (setup_result.stdout, setup_result.stderr)
            assert setup_log.read_text().splitlines() == expected
            assert not marker.exists() and not install_marker.exists()
        executable(tools / "nvidia-smi", 'echo 575.64.05\n')
        for metadata, should_build in (("[]\n", True), ('{"error":"untrusted"}\n', False)):
            releases.write_text(metadata)
            result = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                    input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                    cwd="/", env=env, capture_output=True, text=True, timeout=10)
            assert result.returncode != 0, (result.stdout, result.stderr)
            assert marker.exists() == should_build
            assert not install_marker.exists()
            assert not list(root.glob("opemos-automatic-build.*"))
            cache = root / "home/.cache/open-gpu-kernel-modules-steamos-support"
            assert not list(cache.glob("online-install.*"))
            if marker.exists():
                marker.unlink()
        releases.write_text("[]\n")
        env["MOCK_MODE"] = "incompatible"
        marker.unlink(missing_ok=True)
        result = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                cwd="/", env=env, text=True, capture_output=True, timeout=10)
        assert result.returncode == 76, (result.stdout, result.stderr)
        assert "compiler_policy_mismatch" in result.stderr and not install_marker.exists()
        marker.unlink()
        env["OPEMOS_SKIP_UNCHANGED_BUILD"] = "1"
        result = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                cwd="/", env=env, text=True, capture_output=True, timeout=10)
        assert result.returncode == 76 and not marker.exists(), (result.stdout, result.stderr)
        env.pop("OPEMOS_SKIP_UNCHANGED_BUILD")
        env["MOCK_MODE"] = "cancel"
        public = subprocess.Popen(["bash", "-s", "--", "--build-as-fallback", "--yes"], cwd="/", env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        try:
            public.stdin.write((ROOT / "bootstrap/online_install.sh").read_text())
            public.stdin.close()
            public.stdin = None
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                assert public.poll() is None
                time.sleep(0.02)
            assert marker.exists()
            child_pid = int(pid.read_text())
            public.send_signal(signal.SIGTERM)
            out, err = public.communicate(timeout=10)
            assert public.returncode == 143, (out, err)
            assert not list(root.glob("opemos-automatic-build.*"))
            assert not list(cache.glob("online-install.*"))
            assert not install_marker.exists()
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("public installer left its build command alive")
        finally:
            if public.poll() is None:
                public.kill()
                public.wait()
        assert not Path((root / "runtime-path").read_text().strip()).exists()
        assert not list(runtime_parent.iterdir())


if __name__ == "__main__":
    main()
