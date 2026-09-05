#!/usr/bin/env python3
"""Verify compile.sh restores read-only mode when gh installation is interrupted."""
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "bootstrap/compile.sh"

def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)

def main() -> None:
    with tempfile.TemporaryDirectory(prefix="compile-readonly-signal-") as name:
        root = Path(name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (root / "etc").mkdir()
        (root / "etc/os-release").write_text(
            'ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n', encoding="utf-8"
        )
        for command in ("bash", "dirname", "python3", "sleep", "git", "podman", "tar", "zip", "unzip", "sha256sum", "modinfo", "grep"):
            source = shutil.which(command)
            assert source, command
            (fake_bin / command).symlink_to(source)
        log = root / "readonly.log"
        started = root / "pacman-started"
        executable(fake_bin / "steamos-readonly", '''case "${1:-}" in
  status) echo enabled;;
  disable|enable) printf '%s\\n' "$1" >> "$MOCK_READONLY_LOG";;
  *) exit 2;;
esac
''')
        executable(fake_bin / "pacman", ''': > "$MOCK_PACMAN_STARTED"
while :; do sleep 1; done
''')
        executable(fake_bin / "sudo", '''[ "${1:-}" != -v ] || exit 0
exec "$@"
''')
        environment = {
            **os.environ, "HOME": str(root / "home"), "PATH": str(fake_bin),
            "PROJECT_TEST_MODE": "1", "PROJECT_TEST_ROOT": str(root),
            "MOCK_READONLY_LOG": str(log), "MOCK_PACMAN_STARTED": str(started),
        }
        process = subprocess.Popen(
            [str(ENTRYPOINT), "--auto-upload", "--yes"], cwd="/", env=environment,
            start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 10
        while not started.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                raise AssertionError("compile did not reach GitHub CLI installation")
            time.sleep(0.02)
        assert process.poll() is None, process.communicate()
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, (process.returncode, stdout, stderr)
        assert log.read_text(encoding="utf-8").splitlines() == ["disable", "enable"]

if __name__ == "__main__":
    main()
