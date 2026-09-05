#!/usr/bin/env python3
"""Exercise setup_nvidia development and upstream-development boundaries."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "580.119.02"

def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)

def run(root: Path, option: str):
    support = root / option.removeprefix("--")
    (support / "bootstrap").mkdir(parents=True)
    (support / "lib").mkdir()
    shutil.copy2(ROOT / "bootstrap/setup_nvidia.sh", support / "bootstrap/setup_nvidia.sh")
    (support / "lib/common.sh").write_text('''PROJECT_NAME=OPEMOS
PROJECT_ID=open-gpu-kernel-modules-steamos-support
SUPPORT_REPO=fixture/support
require_steamos() { :; }
need_cmd() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 1; }; }
get_steamos_version() { echo 3.8.14; }
get_kernel_version() { echo fixture-kernel; }
sanitize_release_component() { echo "$1"; }
project_mktemp_dir() { mkdir -p "$HOME/.cache/$PROJECT_ID"; mktemp -d "$HOME/.cache/$PROJECT_ID/$1.XXXXXX"; }
die() { echo "$*" >&2; exit 1; }
log() { echo "$*"; }
warn() { echo "$*" >&2; }
ok() { echo "$*"; }
''')
    log = root / f"{option.removeprefix('--')}.log"
    upstream = root / f"{option.removeprefix('--')}.upstream"
    executable(support / "bootstrap/install_upstream.sh", f"printf 'upstream %s\\n' \"$*\" >> {str(log)!r}\ntouch {str(upstream)!r}\n")
    fake = root / f"bin-{option.removeprefix('--')}"
    fake.mkdir()
    executable(fake / "curl", f'''out=
url=
prev=
for arg in "$@"; do
  [ "$prev" != -o ] || out=$arg
  prev=$arg
  case "$arg" in http://*|https://*) url=$arg;; esac
done
case "$url" in
  */nvidia-utils/) echo nvidia-utils-{VERSION}-1-x86_64.pkg.tar.zst > "$out";;
  */lib32-nvidia-utils/) echo lib32-nvidia-utils-{VERSION}-1-x86_64.pkg.tar.zst > "$out";;
  *.sig) exit 22;;
  *) : > "$out";;
esac
''')
    executable(fake / "steamos-readonly", f'''case "${{1:-}}" in
 status) echo enabled;;
 disable|enable) printf 'readonly %s\\n' "$1" >> {str(log)!r};;
esac
''')
    executable(fake / "pacman", f'''if [ "${{1:-}}" = -U ]; then printf 'pacman install\\n' >> {str(log)!r}; exit 0; fi
case "$*" in *lib32-nvidia-utils*) echo 'lib32-nvidia-utils {VERSION}-1';; *) echo 'nvidia-utils {VERSION}-1';; esac
''')
    executable(fake / "sudo", f'''[ "${{1:-}}" != -v ] || exit 0
case "${{1:-}}" in
 pacman|steamos-readonly) exec "$@";;
 tee) cat >/dev/null;;
 *) printf 'sudo %s\\n' "$*" >> {str(log)!r};;
esac
''')
    executable(fake / "systemctl", "exit 1\n")
    for command in ("ldconfig", "modinfo"):
        executable(fake / command, "exit 0\n")
    home = root / f"home-{option.removeprefix('--')}"
    completed = subprocess.run(
        [str(support / "bootstrap/setup_nvidia.sh"), option, "580", "--yes"],
        cwd="/", env={**os.environ, "HOME": str(home), "PATH": f"{fake}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return completed, log.read_text().splitlines(), upstream

def main():
    with tempfile.TemporaryDirectory(prefix="setup-nvidia-modes-") as name:
        root = Path(name)
        development, log, upstream = run(root, "--development")
        assert "Selection mode:    development" in development.stdout
        assert "leave installed kernel modules unchanged" in development.stdout
        assert log.count("pacman install") == 1
        assert log.count("readonly disable") == 1 and log.count("readonly enable") == 1
        assert not upstream.exists()

        control, log, upstream = run(root, "--use-upstream")
        assert "Selection mode:    upstream-development" in control.stdout
        assert "project fixes are not applied" in control.stdout
        assert log.count("pacman install") == 1
        assert log.count(f"upstream {VERSION} -y") == 1
        assert log.index("pacman install") < log.index(f"upstream {VERSION} -y")
        assert upstream.exists()

if __name__ == "__main__":
    main()
