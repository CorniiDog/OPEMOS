#!/usr/bin/env python3
"""Synthetic x86 contract tests for offline-root immutable input validation."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_install_inputs.py"
INSTALLER = ROOT / "bootstrap/install_to_root.sh"
KERNEL = "6.16.12-valve24.5-1-neptune-616-gfixture"
NVIDIA = "575.64.05"
SIGNER = "A" * 40
MODULES = (
    "nvidia.ko",
    "nvidia-drm.ko",
    "nvidia-modeset.ko",
    "nvidia-peermem.ko",
    "nvidia-uvm.ko",
)


def add_bytes(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def make_package(path, name, version=NVIDIA, gsp=False):
    with tarfile.open(path, "w:gz") as archive:
        add_bytes(
            archive,
            ".PKGINFO",
            f"pkgname = {name}\npkgver = {version}-1\narch = x86_64\n".encode(),
        )
        add_bytes(archive, f"usr/lib/{name}/fixture", b"userspace\n")
        if gsp:
            add_bytes(
                archive,
                f"usr/lib/firmware/nvidia/{version}/gsp_ga10x.bin",
                b"firmware\n",
            )


def make_fixture(root):
    target = root / "target"
    (target / "etc").mkdir(parents=True)
    (target / "usr/lib/modules" / KERNEL).mkdir(parents=True)
    (target / "etc/os-release").write_text(
        "ID=steamos\nVERSION_ID=3.8.16\n", encoding="utf-8"
    )
    module_content = {
        name: f"fixture {name}\n".encode() for name in MODULES
    }
    provenance = {
        "schemaVersion": 1,
        "trust": "locally-built-verified",
        "target": {
            "steamosVersion": "3.8.16",
            "kernelVersion": KERNEL,
            "nvidiaVersion": NVIDIA,
            "architecture": "x86_64",
        },
        "modules": [
            {
                "name": name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in sorted(module_content.items())
        ],
    }
    provenance_bytes = (
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    provenance_path = root / "artifact.provenance.json"
    provenance_path.write_bytes(provenance_bytes)
    archive_path = root / "artifact.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, content in module_content.items():
            add_bytes(archive, f"modules/{name}", content)
        add_bytes(archive, "BUILD-INFO.txt", b"fixture\n")
        add_bytes(archive, "PROVENANCE.json", provenance_bytes)
    checksum = root / "artifact.tar.gz.sha256"
    checksum.write_text(
        f"{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  {archive_path.name}\n",
        encoding="utf-8",
    )
    nvidia_utils = root / "nvidia-utils.pkg.tar.gz"
    lib32 = root / "lib32-nvidia-utils.pkg.tar.gz"
    make_package(nvidia_utils, "nvidia-utils", gsp=True)
    make_package(lib32, "lib32-nvidia-utils")
    for path in (nvidia_utils, lib32):
        path.with_suffix(path.suffix + ".sig").write_bytes(b"signature\n")
    keyring = root / "approved.gpg"
    keyring.write_bytes(b"keyring\n")
    return {
        "target": target,
        "archive": archive_path,
        "checksum": checksum,
        "provenance": provenance_path,
        "nvidia": nvidia_utils,
        "nvidia_sig": nvidia_utils.with_suffix(nvidia_utils.suffix + ".sig"),
        "lib32": lib32,
        "lib32_sig": lib32.with_suffix(lib32.suffix + ".sig"),
        "keyring": keyring,
    }


def make_mocks(root):
    binaries = root / "bin"
    binaries.mkdir()
    (binaries / "modinfo").write_text(
        "#!/bin/sh\ncase $2 in version) echo \"$MOCK_NVIDIA\";; vermagic) echo \"$MOCK_KERNEL SMP preempt mod_unload\";; esac\n",
        encoding="utf-8",
    )
    (binaries / "gpgv").write_text(
        f"#!/bin/sh\n[ \"${{MOCK_BAD_SIGNATURE:-0}}\" = 0 ] || exit 1\necho '[GNUPG:] VALIDSIG {SIGNER} 2026-01-01 0 4 0 1 10 00 {SIGNER}'\n",
        encoding="utf-8",
    )
    (binaries / "pacman").write_text(
        """#!/bin/sh
root=
previous=
for argument in "$@"; do
    [ "$previous" != --root ] || root=$argument
    previous=$argument
done
case " $* " in
  *" -U "*)
    mkdir -p "$root/usr/lib/firmware/nvidia/$MOCK_NVIDIA" "$root/var/lib/pacman"
    printf firmware > "$root/usr/lib/firmware/nvidia/$MOCK_NVIDIA/gsp_ga10x.bin"
    ;;
  *" -Q nvidia-utils "*) echo "nvidia-utils $MOCK_NVIDIA-1" ;;
  *" -Q lib32-nvidia-utils "*) echo "lib32-nvidia-utils $MOCK_NVIDIA-1" ;;
esac
""",
        encoding="utf-8",
    )
    (binaries / "depmod").write_text(
        "#!/bin/sh\nroot=$2; kernel=$4; mkdir -p \"$root/usr/lib/modules/$kernel\"; echo fixture > \"$root/usr/lib/modules/$kernel/modules.dep\"\n",
        encoding="utf-8",
    )
    (binaries / "mount").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "mountpoint").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "umount").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "chroot").write_text(
        "#!/bin/sh\n[ \"${MOCK_FAIL_CHROOT:-0}\" = 0 ] || exit 1\nmkdir -p \"$1/boot\"; echo initramfs > \"$1/boot/initramfs-fixture.img\"\n",
        encoding="utf-8",
    )
    for path in binaries.iterdir():
        path.chmod(0o755)
    return binaries


def run(paths, binaries, output, success, **environment):
    command = [
        sys.executable,
        str(VALIDATOR),
        "--root", str(paths["target"]),
        "--archive", str(paths["archive"]),
        "--checksum", str(paths["checksum"]),
        "--provenance", str(paths["provenance"]),
        "--kernel", KERNEL,
        "--nvidia-utils", str(paths["nvidia"]),
        "--nvidia-utils-signature", str(paths["nvidia_sig"]),
        "--lib32-nvidia-utils", str(paths["lib32"]),
        "--lib32-nvidia-utils-signature", str(paths["lib32_sig"]),
        "--package-keyring", str(paths["keyring"]),
        "--output", str(output),
    ]
    env = os.environ.copy()
    env.update(
        PATH=f"{binaries}:{env['PATH']}",
        MOCK_NVIDIA=NVIDIA,
        MOCK_KERNEL=KERNEL,
        PROJECT_TEST_MODE="1",
        PROJECT_TEST_APPLIANCE_ARCH="x86_64",
        **environment,
    )
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    accepted = completed.returncode == 0
    if accepted != success:
        raise AssertionError(
            "validator result did not match expectation: " + completed.stderr
        )


def run_installer(paths, binaries, result, success, **environment):
    command = [
        str(INSTALLER),
        "--root", str(paths["target"]),
        "--archive", str(paths["archive"]),
        "--checksum", str(paths["checksum"]),
        "--provenance", str(paths["provenance"]),
        "--kernel", KERNEL,
        "--nvidia-utils", str(paths["nvidia"]),
        "--nvidia-utils-signature", str(paths["nvidia_sig"]),
        "--lib32-nvidia-utils", str(paths["lib32"]),
        "--lib32-nvidia-utils-signature", str(paths["lib32_sig"]),
        "--package-keyring", str(paths["keyring"]),
        "--result-json", str(result),
    ]
    env = os.environ.copy()
    env.update(
        PATH=f"{binaries}:{env['PATH']}",
        MOCK_NVIDIA=NVIDIA,
        MOCK_KERNEL=KERNEL,
        PROJECT_TEST_MODE="1",
        PROJECT_TEST_APPLIANCE_ARCH="x86_64",
        **environment,
    )
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if (completed.returncode == 0) != success:
        raise AssertionError(f"installer result did not match expectation: {completed.stderr}")
    return json.loads(result.read_text(encoding="utf-8"))


def main():
    with tempfile.TemporaryDirectory(prefix="offline-root-validation-") as temporary:
        temporary = Path(temporary)
        binaries = make_mocks(temporary)
        paths = make_fixture(temporary)
        run(paths, binaries, temporary / "valid.json", True)
        assert json.loads((temporary / "valid.json").read_text())["status"] == "verified"

        original_checksum = paths["checksum"].read_text()
        paths["checksum"].write_text("0" * 64 + "  artifact.tar.gz\n")
        run(paths, binaries, temporary / "bad-checksum.json", False)
        paths["checksum"].write_text(original_checksum)

        run(paths, binaries, temporary / "bad-signature.json", False, MOCK_BAD_SIGNATURE="1")

        make_package(paths["lib32"], "lib32-nvidia-utils", version="580.1.1")
        run(paths, binaries, temporary / "bad-version.json", False)
        make_package(paths["lib32"], "lib32-nvidia-utils")

        make_package(paths["nvidia"], "nvidia-utils", gsp=False)
        run(paths, binaries, temporary / "missing-gsp.json", False)

        make_package(paths["nvidia"], "nvidia-utils", gsp=True)
        successful = run_installer(paths, binaries, temporary / "install.json", True)
        assert successful["status"] == "success"
        assert successful["cleanup"]["mountsReleased"] is True
        module_root = paths["target"] / "usr/lib/modules" / KERNEL
        assert (module_root / "updates/open-gpu-kernel-modules-steamos/nvidia.ko.zst").is_file()
        assert (module_root / "modules.dep").is_file()
        assert (paths["target"] / "boot/initramfs-fixture.img").is_file()

        second = run_installer(paths, binaries, temporary / "install-again.json", True)
        assert second["status"] == "success"

        failed = run_installer(
            paths,
            binaries,
            temporary / "install-failed.json",
            False,
            MOCK_FAIL_CHROOT="1",
        )
        assert failed["status"] == "failed"
        assert failed["reason"] == "initramfs"
        assert failed["cleanup"]["mountsReleased"] is True


if __name__ == "__main__":
    main()
