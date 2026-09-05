#!/usr/bin/env python3
"""Verify a changed local online install owns exactly one reboot prompt."""
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "bootstrap/online_install.sh"
REVISION = "a" * 40

def executable(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)

def main():
    with tempfile.TemporaryDirectory(prefix="online-reboot-") as name:
        root = Path(name)
        source = root / "support"
        (source / "bootstrap").mkdir(parents=True)
        (source / "lib").mkdir()
        shutil.copy2(ROOT / "lib/common.sh", source / "lib/common.sh")
        install_log = root / "install.log"
        guardian_log = root / "guardian.log"
        reboot_log = root / "reboot.log"
        executable(source / "bootstrap/install.sh", f"printf '%s\\n' \"$*\" >> {str(install_log)!r}\n")
        executable(source / "bootstrap/install_recovery_guardian.sh", f"printf '%s\\n' called >> {str(guardian_log)!r}\n")
        system = root / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n')
        bundle = root / "nvidia-open-fixture.tar.gz"
        bundle.write_bytes(b"changed local fixture")
        checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
        Path(str(bundle) + ".sha256").write_text(f"{checksum}  {bundle.name}\n")
        fake_bin = root / "bin"
        fake_bin.mkdir()
        executable(fake_bin / "git", '''if [ "${1:-}" = clone ]; then
  destination=${@: -1}
  mkdir -p "$destination"
  cp -a "$MOCK_SUPPORT/." "$destination/"
fi
exit 0
''')
        executable(fake_bin / "nvidia-smi", "echo 580.119.02\n")
        executable(fake_bin / "sudo", f"printf '%s\\n' \"$*\" >> {str(reboot_log)!r}\nexit 99\n")
        home = root / "home"
        environment = {
            **os.environ, "HOME": str(home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PROJECT_TEST_MODE": "1", "PROJECT_TEST_ROOT": str(root / "system"),
            "SUPPORT_REVISION": REVISION, "MOCK_SUPPORT": str(source),
        }
        completed = subprocess.run(
            ["bash", "-x", str(ENTRYPOINT), "--local", str(bundle)],
            cwd="/", env=environment,
            input="n\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert len(install_log.read_text().splitlines()) == 1
        assert guardian_log.read_text().splitlines() == ["called"]
        assert completed.stderr.splitlines().count("+ offer_reboot") == 1
        assert "Restart skipped." in completed.stdout
        assert not reboot_log.exists()
        cache = home / ".cache/open-gpu-kernel-modules-steamos-support"
        assert not list(cache.glob("online-install.*"))

if __name__ == "__main__":
    main()
