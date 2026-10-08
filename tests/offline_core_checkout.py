#!/usr/bin/env python3
"""Raw public offline planning executes only objects named by the explicit pin."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = "6.18.50-valve2-1-neptune-618-gc7289a96b14d"


def script(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body)
    path.chmod(0o755)


def main():
    with tempfile.TemporaryDirectory(prefix="offline-core-checkout-") as temporary:
        root = Path(temporary)
        repository = root / "repository"
        repository.mkdir()
        for directory in ("lib", "policies", "profiles"):
            shutil.copytree(ROOT / directory, repository / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        common = repository / "lib/common.sh"
        common.write_text('echo CORE-CODE-EXECUTED >&2\n' + common.read_text())
        git = shutil.which("git")
        def command(*args):
            return subprocess.run([git, *map(str, args)], text=True, capture_output=True, check=True).stdout.strip()
        command("-c", "init.defaultBranch=main", "init", "--quiet", repository)
        command("-C", repository, "add", ".")
        command("-C", repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--quiet", "-m", "Exact offline fixture")
        revision = command("-C", repository, "rev-parse", "HEAD")
        home = root / "home"
        cache = home / ".cache/open-gpu-kernel-modules-steamos-support/core-checkouts"
        cache.mkdir(parents=True, mode=0o700)
        bundle = cache / (revision + ".bundle")
        command("-C", repository, "bundle", "create", bundle, "HEAD")
        bundle.chmod(0o600)
        original = bundle.read_bytes()
        tools = root / "bin"
        tools.mkdir()
        script(tools / "git", f'''if [[ "$1" == clone || "$1" == ls-remote ]]; then exit 7; fi
exec "{git}" "$@"
''')
        script(tools / "uname", f'case "$1" in -r) echo {KERNEL};; -m) echo x86_64;; esac\n')
        script(tools / "curl", '''while [[ $# -gt 0 ]]; do
if [[ "$1" == -o ]]; then echo '[]' > "$2"; exit 0; fi
shift
done
exit 7
''')
        for name in ("sudo", "pacman", "podman", "nvidia-smi"):
            script(tools / name, 'echo UNEXPECTED-MUTATION >&2; exit 98\n')
        system = root / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nVERSION_ID="3.8.28"\n')
        env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "HOME": str(home),
               "SUPPORT_REVISION": revision.upper(), "PROJECT_TEST_MODE": "1",
               "PROJECT_TEST_ROOT": str(root / "system"),
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        def invoke(flags=("--build-as-fallback", "--resolve-only"), overrides=None):
            return subprocess.run(["/bin/bash", "-s", "--", *flags],
                                  input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                  env={**env, **(overrides or {})}, text=True, capture_output=True, timeout=20)
        success = invoke()
        assert success.returncode == 0, (success.stdout, success.stderr)
        assert json.loads(success.stdout)["action"]["kind"] == "build_exact_target"
        assert "CORE-CODE-EXECUTED" in success.stderr
        assert bundle.read_bytes() == original
        no_flag = invoke(("--resolve-only",))
        assert no_flag.returncode != 0 and "CORE-CODE-EXECUTED" not in no_flag.stderr
        unpinned = invoke(overrides={"SUPPORT_REVISION": ""})
        assert unpinned.returncode != 0 and "CORE-CODE-EXECUTED" not in unpinned.stderr
        wrong = cache / ("b" * 40 + ".bundle")
        wrong.write_bytes(original)
        wrong.chmod(0o600)
        mismatch = invoke(overrides={"SUPPORT_REVISION": "b" * 40})
        assert mismatch.returncode != 0 and "CORE-CODE-EXECUTED" not in mismatch.stderr
        for payload, mode in ((b"invalid Git objects", 0o600), (original, 0o644)):
            bundle.write_bytes(payload)
            bundle.chmod(mode)
            refused = invoke()
            assert refused.returncode != 0 and "CORE-CODE-EXECUTED" not in refused.stderr
            assert bundle.read_bytes() == payload
        bundle.unlink()
        bundle.symlink_to(wrong)
        linked = invoke()
        assert linked.returncode != 0 and "CORE-CODE-EXECUTED" not in linked.stderr
        assert bundle.is_symlink() and wrong.read_bytes() == original
        assert not list(cache.parent.glob("online-install.*"))


if __name__ == "__main__":
    main()
