#!/usr/bin/env python3
"""Exercise setup_nvidia.sh interrupt cleanup with command-level fixtures."""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "bootstrap/setup_nvidia.sh"
VERSION = "575.64.05"

def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)

def main() -> None:
    with tempfile.TemporaryDirectory(prefix="setup-nvidia-signal-") as name:
        root = Path(name)
        home = root / "home with spaces"
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (root / "etc").mkdir()
        (root / "etc/os-release").write_text(
            'ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n', encoding="utf-8"
        )
        readonly_log = root / "readonly.log"
        pacman_started = root / "pacman-started"
        executable(fake_bin / "curl", '''out=
url=
prev=
for arg in "$@"; do
  [ "$prev" != -o ] || out=$arg
  prev=$arg
  case "$arg" in http://*|https://*) url=$arg;; esac
done
case "$url" in
  */nvidia-utils/) printf '%s\\n' 'nvidia-utils-575.64.05-1-x86_64.pkg.tar.zst' > "$out";;
  */lib32-nvidia-utils/) printf '%s\\n' 'lib32-nvidia-utils-575.64.05-1-x86_64.pkg.tar.zst' > "$out";;
  *.sig) exit 22;;
  *) : > "$out";;
esac
''')
        executable(fake_bin / "steamos-readonly", '''case "${1:-}" in
  status) echo enabled;;
  disable|enable) printf '%s\\n' "$1" >> "$MOCK_READONLY_LOG";;
  *) exit 2;;
esac
''')
        executable(fake_bin / "pacman", '''if [ "${1:-}" = -U ]; then
  : > "$MOCK_PACMAN_STARTED"
  while :; do sleep 1; done
fi
printf 'nvidia-utils 575.64.05-1\\n'
''')
        executable(fake_bin / "sudo", '''[ "${1:-}" != -v ] || exit 0
if [ "${1:-}" = mkdir ] || [ "${1:-}" = cp ]; then exit 0; fi
exec "$@"
''')
        for command in ("ldconfig", "modinfo"):
            executable(fake_bin / command, "exit 0\n")
        environment = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PROJECT_TEST_MODE": "1",
            "PROJECT_TEST_ROOT": str(root),
            "MOCK_READONLY_LOG": str(readonly_log),
            "MOCK_PACMAN_STARTED": str(pacman_started),
        }
        process = subprocess.Popen(
            [str(ENTRYPOINT), "--development", VERSION, "--yes"], cwd="/",
            env=environment, start_new_session=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 10
        while not pacman_started.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                raise AssertionError("setup did not reach the pacman transaction")
            time.sleep(0.02)
        assert process.poll() is None, process.communicate()
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, (process.returncode, stdout, stderr)
        assert readonly_log.read_text(encoding="utf-8").splitlines() == ["disable", "enable"]
        cache = home / ".cache/open-gpu-kernel-modules-steamos-support"
        assert cache.is_dir()
        assert not list(cache.glob("setup-nvidia.*"))

if __name__ == "__main__":
    main()
