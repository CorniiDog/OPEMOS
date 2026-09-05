#!/usr/bin/env python3
"""Regression coverage for install_upstream.sh --build-only isolation."""
import hashlib
import os
import shutil
import subprocess
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
        executable(support / "bootstrap/install.sh", f"touch {str(install_marker)!r}\nexit 99\n")
        system = fixture / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n')
        fake_bin = fixture / "bin"
        fake_bin.mkdir()
        sudo_marker = fixture / "sudo-called"
        executable(fake_bin / "sudo", f"touch {str(sudo_marker)!r}\nexit 99\n")
        executable(fake_bin / "uname", f'''case "${{1:-}}" in -r) echo {KERNEL};; *) echo Linux;; esac
''')
        executable(fake_bin / "nvidia-smi", f'''echo {VERSION}
''')
        executable(fake_bin / "modinfo", f'''[ "${{1:-}}" = -F ] && [ "${{2:-}}" = vermagic ] && echo '{KERNEL} SMP' && exit 0
exit 1
''')
        executable(fake_bin / "git", f'''case "$*" in
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
        assert not list((home / ".cache/open-gpu-kernel-modules-steamos-support").glob("upstream-install.*"))
        assert "Modules were NOT installed." in completed.stderr

if __name__ == "__main__":
    main()
