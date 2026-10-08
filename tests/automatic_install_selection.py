#!/usr/bin/env python3
"""Exercise the raw public installer planning path without host mutation."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from generate_source_intent_fixtures import matrix


def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body)
    path.chmod(0o755)


def main():
    fixtures = matrix()["cases"]
    published = next(case for case in fixtures if case["name"] == "exact-published-match")
    build = next(case for case in fixtures if case["name"] == "exact-reviewed-build")
    with tempfile.TemporaryDirectory(prefix="public-automatic-plan-") as temporary:
        root = Path(temporary)
        support = root / "support"
        shutil.copytree(ROOT / "lib", support / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "policies", support / "policies")
        shutil.copytree(ROOT / "profiles", support / "profiles")
        (support / "bootstrap").mkdir()
        shutil.copy2(ROOT / "bootstrap/setup_nvidia.sh", support / "bootstrap/setup_nvidia.sh")
        tools = root / "bin"
        tools.mkdir()
        executable(tools / "git", 'if [[ "$1" == clone ]]; then cp -a "$MOCK_SUPPORT" "${@: -1}"; fi\nif [[ "${3:-}" == rev-parse ]]; then echo "${MOCK_CORE_HEAD:-${SUPPORT_REVISION,,}}"; fi\n')
        executable(tools / "curl", '''[[ "${MOCK_OFFLINE:-0}" == 0 ]] || exit 7
while [[ $# -gt 0 ]]; do
  if [[ "$1" == *archive.archlinux.org* ]]; then
    if [[ "$1" == *lib32-nvidia-utils* ]]; then
      package=lib32-nvidia-utils
    else
      package=nvidia-utils
    fi
  fi
  if [[ "$1" == -o ]]; then
    if [[ -n "${package:-}" ]]; then
      printf '%s-575.64.05-1-x86_64.pkg.tar.zst\\n' "$package" > "$2"
    else
      cp "$MOCK_RELEASES" "$2"
    fi
    exit 0
  fi
  shift
done
exit 99
''')
        executable(tools / "uname", '[[ "$1" == -r ]] && printf "%s\\n" "$MOCK_KERNEL"\n')
        for name in ("sudo", "podman", "nvidia-smi", "pacman"):
            executable(tools / name, 'echo MUTATION-OR-USERSPACE-QUERY >&2; exit 98\n')
        system = root / "system/etc"
        system.mkdir(parents=True)
        releases = root / "releases.json"
        home = root / "home"
        env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}",
               "HOME": str(home), "SUPPORT_REVISION": "a" * 40,
               "MOCK_SUPPORT": str(support), "MOCK_RELEASES": str(releases),
               "PROJECT_TEST_MODE": "1", "PROJECT_TEST_ROOT": str(root / "system")}
        cases = ((published, published["releases"], 0, "use_published_artifact"),
                 (build, [], 0, "build_exact_target"),
                 (build, {}, 2, None),
                 (build, [{"tag_name": 7}], 1, None),
                 (build, [{"tag_name": "malformed"}], 0, "build_exact_target"))
        for case, metadata, expected, kind in cases:
            target = case["intent"]["target"]
            (system / "os-release").write_text(f'ID=steamos\nVERSION_ID="{target["steamosVersion"]}"\n')
            releases.write_text(json.dumps(metadata))
            env["MOCK_KERNEL"] = target["kernelVersion"]
            result = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                    input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                    cwd="/", env=env, text=True, capture_output=True)
            assert result.returncode == expected, (result.stdout, result.stderr)
            assert "MUTATION" not in result.stderr
            if kind:
                if kind == "use_published_artifact":
                    no_flag_hit = subprocess.run(["bash", "-s", "--", "--resolve-only"],
                                                 input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                                 cwd="/", env=env, text=True, capture_output=True)
                    assert no_flag_hit.returncode == 0, no_flag_hit.stderr
                    assert json.loads(no_flag_hit.stdout) == json.loads(result.stdout)
                document = json.loads(result.stdout)
                assert document["status"] == "authorized"
                assert document["action"]["kind"] == kind
                assert document["target"] == target
                # The other documented raw one-liner must forward the planning
                # option and align userspace to that same public action.
                setup = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                       input=(ROOT / "bootstrap/online_setup_nvidia.sh").read_text(),
                                       cwd="/", env=env, text=True, capture_output=True)
                assert setup.returncode == 0, (setup.stdout, setup.stderr)
                assert "NVIDIA:            575.64.05" in setup.stdout
                assert "MUTATION" not in setup.stderr
                assert not list((home / ".cache/open-gpu-kernel-modules-steamos-support").glob("online-setup-nvidia.*"))
            assert not list((home / ".cache/open-gpu-kernel-modules-steamos-support").glob("online-install.*"))
        mismatch = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                  input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                  cwd="/", env={**env, "MOCK_CORE_HEAD": "b" * 40}, text=True, capture_output=True)
        assert mismatch.returncode != 0 and "checkout does not match" in mismatch.stderr
        assert "MUTATION" not in mismatch.stderr
        assert not list((home / ".cache/open-gpu-kernel-modules-steamos-support").glob("online-install.*"))
        releases.write_text("[]")
        recovery = root / "system/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"
        recovery.mkdir(parents=True, mode=0o755)
        recovery_env = {**env, "OPEMOS_RECOVERY_PLAN_FILE": str(root / "plan"), "HOME": str(root / "missing-immutable-home")}
        service = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                 input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                 cwd="/", env=recovery_env, text=True, capture_output=True)
        assert service.returncode == 0, (service.stdout, service.stderr)
        assert not Path(recovery_env["HOME"]).exists()
        assert not list((recovery / "workspaces").iterdir())
        (recovery / "workspaces").chmod(0o777)
        unsafe_service = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                        input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                        cwd="/", env=recovery_env, text=True, capture_output=True)
        assert unsafe_service.returncode != 0 and "workspace is unsafe" in unsafe_service.stderr
        assert not list((recovery / "workspaces").iterdir())
        for entry in ("online_install.sh", "online_setup_nvidia.sh"):
            no_flag = subprocess.run(["bash", "-s", "--", "--resolve-only"],
                                     input=(ROOT / "bootstrap" / entry).read_text(),
                                     cwd="/", env=env, text=True, capture_output=True)
            assert no_flag.returncode != 0, (no_flag.stdout, no_flag.stderr)
            assert "MUTATION" not in no_flag.stderr
        env["MOCK_OFFLINE"] = "1"
        result = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                cwd="/", env=env, text=True, capture_output=True)
        assert result.returncode != 0 and not result.stdout
        assert "Failed to query" in result.stderr


if __name__ == "__main__":
    main()
