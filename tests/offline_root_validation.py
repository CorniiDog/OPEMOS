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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_install_inputs.py"
INSTALLER = ROOT / "bootstrap/install_to_root.sh"
KERNEL = "6.16.12-valve24.5-1-neptune-616-gfixture"
NVIDIA = "575.64.05"
NVIDIA_SIGNER = "05C7775A9E8B977407FE08E69D4C5AA15426DA0A"
LIB32_SIGNER = "D2E95FEC015CF1F911AAAB0C3D4C5008BB5C8D29"
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


def make_package(
    path,
    name,
    version=NVIDIA,
    pkgrel="1",
    gsp=False,
    firmware_version=None,
    link_target=None,
):
    with tarfile.open(path, "w:gz") as archive:
        add_bytes(
            archive,
            ".PKGINFO",
            f"pkgname = {name}\npkgver = {version}-{pkgrel}\narch = x86_64\n".encode(),
        )
        add_bytes(archive, f"usr/lib/{name}/fixture", b"userspace\n")
        if gsp:
            add_bytes(
                archive,
                f"usr/lib/firmware/nvidia/{firmware_version or version}/gsp_ga10x.bin",
                b"firmware\n",
            )
        if link_target:
            member = tarfile.TarInfo("usr/lib/nvidia-fixture-link")
            member.type = tarfile.SYMTYPE
            member.linkname = link_target
            archive.addfile(member)


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
    make_package(nvidia_utils, "nvidia-utils", pkgrel="2", gsp=True)
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
        f"#!/bin/sh\nsleep \"${{MOCK_GPGV_DELAY:-0}}\"\n[ \"${{MOCK_BAD_SIGNATURE:-0}}\" = 0 ] || exit 1\ncase \"$*\" in *lib32*) signer={LIB32_SIGNER};; *) signer={NVIDIA_SIGNER};; esac\necho \"[GNUPG:] VALIDSIG $signer 2026-01-01 0 4 0 1 10 00 $signer\"\n",
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
    (binaries / "mount").write_text(
        "#!/bin/sh\ncase \" $* \" in *' --rbind '*) eval target=\\${$#}; echo \"$target\" >> \"$MOCK_MOUNT_STATE\";; esac\n",
        encoding="utf-8",
    )
    (binaries / "mountpoint").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "findmnt").write_text(
        "#!/bin/sh\neval target=\\${$#}; grep -F -q \"$target\" \"$MOCK_MOUNT_STATE\" 2>/dev/null\n",
        encoding="utf-8",
    )
    (binaries / "umount").write_text(
        "#!/bin/sh\neval target=\\${$#}; grep -F -v \"$target\" \"$MOCK_MOUNT_STATE\" > \"$MOCK_MOUNT_STATE.next\" || true; mv \"$MOCK_MOUNT_STATE.next\" \"$MOCK_MOUNT_STATE\"\n",
        encoding="utf-8",
    )
    (binaries / "chroot").write_text(
        "#!/bin/sh\nsleep \"${MOCK_CHROOT_DELAY:-0}\"\n[ \"${MOCK_FAIL_CHROOT:-0}\" = 0 ] || exit 1\nmkdir -p \"$1/boot\"; echo initramfs > \"$1/boot/initramfs-fixture.img\"\n",
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
    return json.loads(output.read_text(encoding="utf-8"))


def installer_command(paths, result):
    return [
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


def installer_environment(binaries, mount_state, **environment):
    env = os.environ.copy()
    env.update(
        PATH=f"{binaries}:{env['PATH']}",
        MOCK_NVIDIA=NVIDIA,
        MOCK_KERNEL=KERNEL,
        PROJECT_TEST_MODE="1",
        PROJECT_TEST_APPLIANCE_ARCH="x86_64",
        MOCK_MOUNT_STATE=str(mount_state),
        **environment,
    )
    return env


def run_installer(paths, binaries, result, success, **environment):
    command = installer_command(paths, result)
    mount_state = result.with_suffix(".mounts")
    mount_state.write_text("", encoding="utf-8")
    env = installer_environment(binaries, mount_state, **environment)
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if (completed.returncode == 0) != success:
        raise AssertionError(f"installer result did not match expectation: {completed.stderr}")
    assert not mount_state.read_text(encoding="utf-8").strip()
    return json.loads(result.read_text(encoding="utf-8"))


def cancel_installer(paths, binaries, result, expected_phase, **environment):
    mount_state = result.with_suffix(".mounts")
    mount_state.write_text("", encoding="utf-8")
    env = installer_environment(binaries, mount_state, **environment)
    before_validation_files = set(Path("/tmp").glob("offline-root-validation.*"))
    process = subprocess.Popen(
        installer_command(paths, result),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    if expected_phase == "validation":
        time.sleep(0.5)
    else:
        while time.monotonic() < deadline and not mount_state.read_text().strip():
            time.sleep(0.05)
        assert mount_state.read_text().strip(), "mutation never established bind mounts"
    process.terminate()
    _, stderr = process.communicate(timeout=5)
    assert process.returncode != 0, stderr
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["status"] == "cancelled"
    assert document["reason"] == "cancelled"
    assert document["phase"] == expected_phase
    assert document["cleanup"]["mountsReleased"] is True
    assert not mount_state.read_text(encoding="utf-8").strip()
    assert set(Path("/tmp").glob("offline-root-validation.*")) == before_validation_files


def main():
    with tempfile.TemporaryDirectory(prefix="offline-root-validation-") as temporary:
        temporary = Path(temporary)
        binaries = make_mocks(temporary)
        paths = make_fixture(temporary)
        run(paths, binaries, temporary / "valid.json", True)
        valid = json.loads((temporary / "valid.json").read_text())
        assert valid["status"] == "verified"
        assert [package["fullVersion"] for package in valid["packages"]] == [
            f"{NVIDIA}-2",
            f"{NVIDIA}-1",
        ]

        original_checksum = paths["checksum"].read_text()
        paths["checksum"].write_text("0" * 64 + "  artifact.tar.gz\n")
        bad_checksum = run(paths, binaries, temporary / "bad-checksum.json", False)
        assert bad_checksum["reason"] == "archive_checksum_mismatch"
        installer_bad_checksum = run_installer(
            paths, binaries, temporary / "install-bad-checksum.json", False
        )
        assert installer_bad_checksum["reason"] == "archive_checksum_mismatch"
        assert installer_bad_checksum["phase"] == "validation"
        paths["checksum"].write_text(original_checksum)

        run(paths, binaries, temporary / "bad-signature.json", False, MOCK_BAD_SIGNATURE="1")

        make_package(paths["lib32"], "lib32-nvidia-utils", version="580.1.1")
        run(paths, binaries, temporary / "bad-version.json", False)
        make_package(paths["lib32"], "lib32-nvidia-utils")

        make_package(
            paths["nvidia"],
            "nvidia-utils",
            pkgrel="2",
            gsp=True,
            link_target="nvidia-utils/fixture",
        )
        run(paths, binaries, temporary / "safe-link.json", True)
        make_package(
            paths["nvidia"],
            "nvidia-utils",
            pkgrel="2",
            gsp=True,
            link_target="../../../escape",
        )
        unsafe_link = run(paths, binaries, temporary / "unsafe-link.json", False)
        assert unsafe_link["reason"] == "userspace_package_unsafe"

        make_package(paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=False)
        run(paths, binaries, temporary / "missing-gsp.json", False)

        make_package(
            paths["nvidia"],
            "nvidia-utils",
            pkgrel="2",
            gsp=True,
            firmware_version="wrong-version",
        )
        run(paths, binaries, temporary / "wrong-gsp-version.json", False)

        make_package(paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=True)
        successful = run_installer(paths, binaries, temporary / "install.json", True)
        assert successful["status"] == "success"
        assert successful["cleanup"]["mountsReleased"] is True
        assert successful["validation"]["keyring"]["name"] == "approved.gpg"
        assert len(successful["validation"]["keyring"]["sha256"]) == 64
        assert [
            package["fullVersion"]
            for package in successful["validation"]["packages"]
        ] == [f"{NVIDIA}-2", f"{NVIDIA}-1"]
        assert [
            package["signer"]
            for package in successful["validation"]["packages"]
        ] == [NVIDIA_SIGNER, LIB32_SIGNER]
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

        cancel_installer(
            paths,
            binaries,
            temporary / "cancel-validation.json",
            "validation",
            MOCK_GPGV_DELAY="30",
        )
        cancel_installer(
            paths,
            binaries,
            temporary / "cancel-mutation.json",
            "initramfs",
            MOCK_CHROOT_DELAY="30",
        )


if __name__ == "__main__":
    main()
