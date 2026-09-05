#!/usr/bin/env python3
"""Regression coverage for install_upstream.sh --build-only isolation."""
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "580.119.02"
KERNEL = "6.16.12-valve-test"

def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)

def main():
    with tempfile.TemporaryDirectory(prefix="upstream-build-only-") as name:
        fixture = Path(name)
        support = fixture / "support"
        (support / "bootstrap").mkdir(parents=True)
        (support / "lib").mkdir()
        shutil.copy2(ROOT / "bootstrap/install_upstream.sh", support / "bootstrap/install_upstream.sh")
        shutil.copy2(ROOT / "lib/common.sh", support / "lib/common.sh")
        executable(support / "bootstrap/setup_build_env.sh", "exit 0\n")
        executable(support / "bootstrap/build.sh", '''source_dir="$HOME/.cache/open-gpu-kernel-modules-steamos-support/upstream/580.119.02"
mkdir -p "$source_dir/kernel-open"
for module in nvidia nvidia-drm nvidia-modeset nvidia-peermem nvidia-uvm; do
  printf 'fixture %s\\n' "$module" > "$source_dir/kernel-open/$module.ko"
done
''')
        install_marker = fixture / "install-called"
        captured_archive = fixture / "installed-input.tar.gz"
        executable(support / "bootstrap/install.sh", f"""printf '%s\\n' "$*" >> {str(install_marker)!r}
archive=
previous=
for argument in "$@"; do [ "$previous" != --archive ] || archive=$argument; previous=$argument; done
cp "$archive" {str(captured_archive)!r}
""")
        system = fixture / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n')
        fake_bin = fixture / "bin"
        fake_bin.mkdir()
        sudo_marker = fixture / "sudo-called"
        executable(fake_bin / "sudo", f"printf '%s\\n' \"$*\" >> {str(sudo_marker)!r}\n[ \"${{1:-}}\" = -v ] && exit 0\nexit 99\n")
        executable(fake_bin / "uname", f'''case "${{1:-}}" in -r) echo {KERNEL};; *) echo Linux;; esac
''')
        executable(fake_bin / "nvidia-smi", f'''echo {VERSION}
''')
        executable(fake_bin / "modinfo", f'''[ "${{1:-}}" = -F ] && [ "${{2:-}}" = vermagic ] && echo '{KERNEL} SMP' && exit 0
exit 1
''')
        git_log = fixture / "git.log"
        executable(fake_bin / "git", f'''printf '%s\n' "$*" >> {str(git_log)!r}
case "$*" in
  "clone "*) dest=${{@: -1}}; mkdir -p "$dest/.git" "$dest/kernel-open"; echo 'NVIDIA_VERSION = {VERSION}' > "$dest/version.mk";;
  *" rev-list -n1 "*) printf '%040d\\n' 1;;
  *" rev-parse HEAD"*) printf '%040d\\n' 2;;
  *" status --porcelain"*) :;;
  *) :;;
esac
''')
        home = fixture / "home"
        state = fixture / "state"
        home.mkdir(); state.mkdir()
        state_file = state / "open-gpu-kernel-modules-steamos-support/dev-state"
        state_file.parent.mkdir()
        state_file.write_text("preserved-state\n")
        env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state),
               "PROJECT_TEST_MODE": "1", "PROJECT_TEST_ROOT": str(fixture / "system"),
               "PATH": f"{fake_bin}:{os.environ['PATH']}"}
        completed = subprocess.run(
            [str(support / "bootstrap/install_upstream.sh"), "--build-only", "--yes", VERSION],
            cwd="/", env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert not sudo_marker.exists()
        assert not install_marker.exists()
        assert state_file.read_text() == "preserved-state\n"
        output = home / ".cache/open-gpu-kernel-modules-steamos-support/upstream-builds"
        archives = list(output.glob("*.tar.gz"))
        assert len(archives) == 1
        checksum = Path(str(archives[0]) + ".sha256")
        fields = checksum.read_text().split()
        assert fields == [hashlib.sha256(archives[0].read_bytes()).hexdigest(), archives[0].name]
        with tarfile.open(archives[0]) as archive:
            build_info = archive.extractfile("BUILD-INFO.txt").read().decode()
        assert "source_provider=upstream\n" in build_info
        assert "project_patches=0\n" in build_info
        assert "source_commit=" + "0" * 39 + "1\n" in build_info
        assert not list((home / ".cache/open-gpu-kernel-modules-steamos-support").glob("upstream-install.*"))
        assert "Modules were NOT installed." in completed.stderr

        completed = subprocess.run(
            [str(support / "bootstrap/install_upstream.sh"), "--yes", VERSION],
            cwd="/", env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert sudo_marker.read_text().splitlines() == ["-v"]
        calls = install_marker.read_text().splitlines()
        assert len(calls) == 1
        assert "--archive " in calls[0] and " --checksum " in calls[0]
        assert calls[0].endswith(" -y")
        assert captured_archive.is_file()
        with tarfile.open(captured_archive) as archive:
            installed_info = archive.extractfile("BUILD-INFO.txt").read().decode()
        assert "source_provider=upstream\n" in installed_info
        assert "project_patches=0\n" in installed_info
        git_calls = git_log.read_text().splitlines()
        exact_ref = f"+refs/tags/{VERSION}:refs/tags/{VERSION}"
        assert sum(exact_ref in call for call in git_calls) == 2
        assert sum("checkout --quiet --detach " + "0" * 39 + "1" in call for call in git_calls) == 2
        assert state_file.read_text() == "preserved-state\n"

if __name__ == "__main__":
    main()
