#!/usr/bin/env python3
"""Verify a changed local online install owns exactly one reboot prompt."""
import hashlib
import os
import shutil
import subprocess
import tarfile
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
        package = root / "package"
        modules = package / "modules"
        modules.mkdir(parents=True)
        build_info = (
            "steamos_version=3.8.14\n"
            "kernel_version=fixture-kernel\n"
            "nvidia_version=580.119.02\n"
        )
        (package / "BUILD-INFO.txt").write_text(build_info)
        names = ("nvidia", "nvidia-drm", "nvidia-modeset", "nvidia-peermem", "nvidia-uvm")
        for module in names:
            (modules / f"{module}.ko").write_text(f"canonical {module}\n")
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(package / "BUILD-INFO.txt", arcname="BUILD-INFO.txt")
            archive.add(modules, arcname="modules")
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
        target = root / "system/usr/lib/modules/fixture-kernel/updates/open-gpu-kernel-modules-steamos"
        target.mkdir(parents=True)
        for module in names:
            payload = f"canonical {module}\n" if module != "nvidia-uvm" else "tampered payload\n"
            (target / f"{module}.ko").write_text(payload)
        state = root / "system/var/lib/open-gpu-kernel-modules-steamos-support"
        state.mkdir(parents=True)
        (state / "installed-build-info.txt").write_text(build_info)
        executable(fake_bin / "uname", 'case "${1:-}" in -r) echo fixture-kernel;; *) echo Linux;; esac\n')
        executable(fake_bin / "modinfo", f'if [ "${{1:-}}" = -n ]; then echo {target}/nvidia.ko; exit 0; fi\nexit 1\n')
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
        assert "requires update or repair" in completed.stdout
        assert guardian_log.read_text().splitlines() == ["called"]
        assert completed.stderr.splitlines().count("+ offer_reboot") == 1
        assert "Restart skipped." in completed.stdout
        assert not reboot_log.exists()
        cache = home / ".cache/open-gpu-kernel-modules-steamos-support"
        assert not list(cache.glob("online-install.*"))

        # Freeze the known-good 575 representation boundary: release archives
        # contain raw modules while SteamOS may store identical bytes as .ko.zst.
        old_build_info = (
            "steamos_version=3.8.14\n"
            "kernel_version=fixture-kernel\n"
            "nvidia_version=575.64.05\n"
        )
        (package / "BUILD-INFO.txt").write_text(old_build_info)
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(package / "BUILD-INFO.txt", arcname="BUILD-INFO.txt")
            archive.add(modules, arcname="modules")
        checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
        Path(str(bundle) + ".sha256").write_text(f"{checksum}  {bundle.name}\n")
        (state / "installed-build-info.txt").write_text(old_build_info)
        executable(fake_bin / "nvidia-smi", "echo 575.64.05\n")
        for module in names:
            raw = target / f"{module}.ko"
            raw.unlink()
            compressed = subprocess.run(
                ["zstd", "-q", "-c"], input=f"canonical {module}\n".encode(),
                stdout=subprocess.PIPE, check=True,
            ).stdout
            (target / f"{module}.ko.zst").write_bytes(compressed)
        install_log.unlink()
        guardian_log.unlink()

        old_release = subprocess.run(
            ["bash", str(ENTRYPOINT), "--local", str(bundle)],
            cwd="/", env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        assert old_release.returncode == 0, (old_release.stdout, old_release.stderr)
        assert "Already installed, healthy, and current." in old_release.stdout
        assert "Nothing to do." in old_release.stdout
        assert not install_log.exists(), "equivalent compressed 575 modules must not reinstall"
        assert guardian_log.read_text().splitlines() == ["called"]
        assert not reboot_log.exists(), "unchanged 575 installation must not offer reboot"
        assert not list(cache.glob("online-install.*"))

if __name__ == "__main__":
    main()
