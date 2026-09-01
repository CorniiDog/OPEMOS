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
EGL_EXTERNAL_SIGNER = "83BC8889351B5DEBBB68416EB8AC08600F108CDF"
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


def altered_module_archive(source, destination, alteration):
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as output:
        for member in original.getmembers():
            if alteration == "oversized" and member.name == "BUILD-INFO.txt":
                add_bytes(output, member.name, b"x" * (1024 * 1024 + 1))
                continue
            stream = original.extractfile(member) if member.isfile() else None
            output.addfile(member, stream)
        if alteration == "extra":
            add_bytes(output, "unexpected.txt", b"unexpected\n")
        elif alteration == "duplicate":
            add_bytes(output, "PROVENANCE.json", source.read_bytes()[:16])


def provenance_archive(source, destination, provenance_bytes):
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as output:
        for member in original:
            if member.name == "PROVENANCE.json":
                add_bytes(output, member.name, provenance_bytes)
            else:
                stream = original.extractfile(member) if member.isfile() else None
                output.addfile(member, stream)


def make_package(
    path,
    name,
    version=NVIDIA,
    pkgrel="1",
    gsp=False,
    firmware_version=None,
    link_target=None,
    installed_size=4096,
    dependencies=(),
    provides=(),
    architecture="x86_64",
    duplicate_member=False,
    special_member=False,
):
    with tarfile.open(path, "w:gz") as archive:
        add_bytes(
            archive,
            ".PKGINFO",
            (
                f"pkgname = {name}\npkgver = {version}-{pkgrel}\n"
                f"arch = {architecture}\nsize = {installed_size}\n"
                + "".join(f"depend = {dependency}\n" for dependency in dependencies)
                + "".join(f"provides = {provided}\n" for provided in provides)
            ).encode(),
        )
        add_bytes(archive, f"usr/lib/{name}/fixture", b"userspace\n")
        if duplicate_member:
            add_bytes(archive, f"usr/lib/{name}/fixture", b"duplicate\n")
        if special_member:
            member = tarfile.TarInfo(f"usr/lib/{name}/unsafe-pipe")
            member.type = tarfile.FIFOTYPE
            archive.addfile(member)
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
    package_dependencies = {
        "filesystem": (),
        "glibc": ("filesystem",),
        "pacman": ("glibc",),
    }
    for package_name in ("filesystem", "glibc", "pacman"):
        package_record = target / f"usr/lib/holo/pacmandb/local/{package_name}-1-1"
        package_record.mkdir(parents=True)
        (package_record / "desc").write_text(
            (
                f"%NAME%\n{package_name}\n\n%VERSION%\n1-1\n\n"
                "%ISIZE%\n1024\n\n"
                + ("%DEPENDS%\n" + "\n".join(package_dependencies[package_name]) + "\n"
                   if package_dependencies[package_name] else "")
            ),
            encoding="utf-8",
        )
    # Real Holo local databases can contain unrelated package records without
    # %ISIZE%.  Their identity and dependency metadata remain usable, but they
    # must not receive replacement-size credit.
    unrelated_record = target / "usr/lib/holo/pacmandb/local/steamos-customizations-1-1"
    unrelated_record.mkdir(parents=True)
    (unrelated_record / "desc").write_text(
        "%NAME%\nsteamos-customizations\n\n%VERSION%\n1-1\n\n"
        "%DEPENDS%\nfilesystem\n\n%REASON%\n1\n",
        encoding="utf-8",
    )
    (target / "boot").mkdir()
    (target / "var").mkdir()
    grub = target / "efi/EFI/steamos/grub.cfg"
    grub.parent.mkdir(parents=True)
    grub.write_text(
        "set default=0\n"
        "menuentry 'SteamOS' {\n"
        "  linux /vmlinuz-neptune root=LABEL=rootfs-A quiet nvidia-drm.modeset=0\n"
        "  initrd /initramfs-neptune.img\n"
        "}\n"
        "menuentry 'SteamOS fallback' {\n"
        "  steamenv_boot\tlinux /boot/vmlinuz-linux-neptune-616 root=LABEL=rootfs-A quiet # fallback entry\n"
        "}\n",
        encoding="utf-8",
    )
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
        directory = tarfile.TarInfo("modules/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
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
    egl_wayland = root / "egl-wayland.pkg.tar.gz"
    make_package(
        nvidia_utils, "nvidia-utils", pkgrel="2", gsp=True,
        dependencies=("glibc>=1-1",),
    )
    make_package(
        lib32, "lib32-nvidia-utils", dependencies=("nvidia-utils=575.64.05-2",),
    )
    make_package(egl_wayland, "egl-wayland", version="4.0.0", installed_size=2048)
    for path in (nvidia_utils, lib32, egl_wayland):
        path.with_suffix(path.suffix + ".sig").write_bytes(b"signature\n")
    keyring = root / "approved.gpg"
    keyring.write_bytes(b"keyring\n")
    userspace_lock = root / "userspace-lock.json"
    return {
        "target": target,
        "archive": archive_path,
        "checksum": checksum,
        "provenance": provenance_path,
        "nvidia": nvidia_utils,
        "nvidia_sig": nvidia_utils.with_suffix(nvidia_utils.suffix + ".sig"),
        "lib32": lib32,
        "lib32_sig": lib32.with_suffix(lib32.suffix + ".sig"),
        "dependency": egl_wayland,
        "dependency_sig": egl_wayland.with_suffix(egl_wayland.suffix + ".sig"),
        "keyring": keyring,
        "userspace_lock": userspace_lock,
    }


def write_userspace_lock(paths):
    package_paths = [paths["nvidia"], paths["lib32"]]
    if paths.get("dependency_packages") is not None:
        package_paths.extend(paths["dependency_packages"])
    elif paths.get("stage_dependency"):
        package_paths.append(paths["dependency"])
    packages = []
    for package in package_paths:
        with tarfile.open(package, "r:gz") as archive:
            metadata = archive.extractfile(".PKGINFO").read().decode()
        fields = {}
        dependencies = []
        provides = []
        for line in metadata.splitlines():
            if " = " not in line:
                continue
            key, value = line.split(" = ", 1)
            fields.setdefault(key, value)
            if key == "depend":
                dependencies.append(value)
            elif key == "provides":
                provides.append(value)
        signature = package.with_suffix(package.suffix + ".sig")
        signer = LIB32_SIGNER if ("lib32" in package.name or "egl-wayland" in package.name) else NVIDIA_SIGNER
        packages.append({
            "name": fields["pkgname"], "filename": package.name,
            "signatureFilename": signature.name, "version": fields["pkgver"],
            "architecture": fields["arch"],
            "packageSha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            "signatureSha256": hashlib.sha256(signature.read_bytes()).hexdigest(),
            "signerFingerprint": signer, "installedSize": int(fields["size"]),
            "dependencies": dependencies, "provides": provides,
        })
    document = {
        "schemaVersion": 1, "status": "reviewed", "missingReview": [],
        "target": {"steamosVersion": "3.8.16", "nvidiaVersion": NVIDIA,
                   "architecture": "x86_64"},
        "snapshot": {"identity": "fixture", "url": "https://invalid.example/fixture/"},
        "keyring": {"filename": paths["keyring"].name,
                    "sha256": hashlib.sha256(paths["keyring"].read_bytes()).hexdigest(),
                    "provenance": "fixture"},
        "packages": sorted(packages, key=lambda item: item["name"]),
    }
    paths["userspace_lock"].write_text(json.dumps(document), encoding="utf-8")


def make_mocks(root):
    binaries = root / "bin"
    binaries.mkdir()
    (binaries / "modinfo").write_text(
        "#!/bin/sh\ncase $2 in version) echo \"$MOCK_NVIDIA\";; vermagic) echo \"$MOCK_KERNEL SMP preempt mod_unload\";; esac\n",
        encoding="utf-8",
    )
    (binaries / "gpgv").write_text(
        f"#!/bin/sh\nsleep \"${{MOCK_GPGV_DELAY:-0}}\"\n[ \"${{MOCK_BAD_SIGNATURE:-0}}\" = 0 ] || exit 1\ncase \"${{MOCK_FORCE_SIGNER:-}}:$*\" in ?*:*) signer=${{MOCK_FORCE_SIGNER}};; *eglexternalplatform*) signer={EGL_EXTERNAL_SIGNER};; *egl-wayland*) signer={LIB32_SIGNER};; *lib32*) signer={LIB32_SIGNER};; *) signer={NVIDIA_SIGNER};; esac\necho \"[GNUPG:] VALIDSIG $signer 2026-01-01 0 4 0 1 10 00 $signer\"\n",
        encoding="utf-8",
    )
    (binaries / "vercmp").write_text(
        "#!/bin/sh\nif [ \"$1\" = \"$2\" ]; then echo 0; else echo 1; fi\n",
        encoding="utf-8",
    )
    (binaries / "pacman").write_text(
        """#!/bin/sh
root=
dbpath=
previous=
for argument in "$@"; do
    [ "$previous" != --root ] || root=$argument
    [ "$previous" != --dbpath ] || dbpath=$argument
    previous=$argument
done
[ "$dbpath" = "$root/usr/lib/holo/pacmandb" ] || exit 91
case " $* " in
  *" -U "*)
    mkdir -p "$root/usr/lib/firmware/nvidia/$MOCK_NVIDIA"
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
        "#!/bin/sh\ncase \" $* \" in *' -T '*) echo 'btrfs rw,compress=zstd:3'; exit 0;; esac\neval target=\\${$#}; grep -F -q \"$target\" \"$MOCK_MOUNT_STATE\" 2>/dev/null\n",
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


def run(paths, binaries, output, success, preserve_lock=False, **environment):
    if not preserve_lock:
        write_userspace_lock(paths)
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
        "--userspace-lock", str(paths["userspace_lock"]),
        "--output", str(output),
    ]
    dependency_packages = paths.get("dependency_packages")
    if dependency_packages is None:
        dependency_packages = [paths["dependency"]] if paths.get("stage_dependency") else []
    for dependency in dependency_packages:
        command.extend([
            "--dependency-package", str(dependency),
            "--dependency-signature", str(dependency.with_suffix(dependency.suffix + ".sig")),
        ])
    if "progress_attempt" in paths:
        command.extend(["--progress-attempt", str(paths["progress_attempt"])])
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
    output.with_suffix(output.suffix + ".stderr").write_text(completed.stderr, encoding="utf-8")
    accepted = completed.returncode == 0
    if accepted != success:
        raise AssertionError(
            "validator result did not match expectation: " + completed.stderr
        )
    return json.loads(output.read_text(encoding="utf-8"))


def installer_command(paths, result, preserve_lock=False):
    if not preserve_lock:
        write_userspace_lock(paths)
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
        "--userspace-lock", str(paths["userspace_lock"]),
        "--result-json", str(result),
    ]
    dependency_packages = paths.get("dependency_packages")
    if dependency_packages is None:
        dependency_packages = [paths["dependency"]] if paths.get("stage_dependency") else []
    for dependency in dependency_packages:
        command.extend([
            "--dependency-package", str(dependency),
            "--dependency-signature", str(dependency.with_suffix(dependency.suffix + ".sig")),
        ])
    if "progress_attempt" in paths:
        command.extend(["--progress-attempt", str(paths["progress_attempt"])])
    return command


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


def run_installer(paths, binaries, result, success, preserve_lock=False, **environment):
    command = installer_command(paths, result, preserve_lock=preserve_lock)
    mount_state = result.with_suffix(".mounts")
    mount_state.write_text("", encoding="utf-8")
    env = installer_environment(binaries, mount_state, **environment)
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result.with_suffix(result.suffix + ".stderr").write_text(completed.stderr, encoding="utf-8")
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
        while (
            time.monotonic() < deadline
            and len(mount_state.read_text().splitlines()) < 3
        ):
            time.sleep(0.05)
        assert len(mount_state.read_text().splitlines()) == 3, (
            "mutation never established all bind mounts"
        )
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
    excessive_attempt = subprocess.run(
        [str(INSTALLER), "--progress-attempt", "1000001"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert excessive_attempt.returncode != 0
    assert "exceeds 1000000" in excessive_attempt.stderr
    with tempfile.TemporaryDirectory(prefix="offline-root-validation-") as temporary:
        temporary = Path(temporary)
        for label, arguments in (
            ("unknown", ["--token=supersecret"]),
            ("missing-value", ["--root"]),
            ("duplicate", ["--progress-attempt", "1", "--progress-attempt", "2"]),
        ):
            cli_result = temporary / f"cli-{label}.json"
            completed = subprocess.run(
                [str(INSTALLER), *arguments, "--result-json", str(cli_result)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert completed.returncode != 0
            cli_document = json.loads(cli_result.read_text(encoding="utf-8"))
            assert cli_document["schemaVersion"] == 1
            assert cli_document["status"] == "failed"
            assert cli_document["reason"] == "invalid_arguments"
            assert cli_document["phase"] == "argument_validation"
            assert cli_document["target"]["root"] == "/target-root"
            assert cli_document["cleanup"]["mountsReleased"] is True
            assert "supersecret" not in json.dumps(cli_document)
        binaries = make_mocks(temporary)
        paths = make_fixture(temporary)
        run(paths, binaries, temporary / "valid.json", True)
        valid = json.loads((temporary / "valid.json").read_text())
        progress_lines = [
            line.removeprefix("STEAMOS_NVIDIA_PROGRESS ")
            for line in (temporary / "valid.json.stderr").read_text(encoding="utf-8").splitlines()
            if line.startswith("STEAMOS_NVIDIA_PROGRESS ")
        ]
        progress_records = [json.loads(line) for line in progress_lines]
        assert progress_records
        assert {record["schemaVersion"] for record in progress_records} == {1}
        assert {record["attempt"] for record in progress_records} == {0}
        assert {record["phase"] for record in progress_records} >= {
            "hashing", "holo_database", "archive_layout", "modules",
            "userspace_packages", "dependency_closure", "storage_calculation",
        }
        assert all(
            set(record) <= {
                "schemaVersion", "attempt", "phase", "indeterminate",
                "unit", "completed", "total",
            }
            for record in progress_records
        )
        assert str(paths["target"]) not in "\n".join(progress_lines)
        assert valid["status"] == "verified"
        assert valid["provenanceSha256"] == hashlib.sha256(
            paths["provenance"].read_bytes()
        ).hexdigest()
        assert valid["userspaceLock"] == {
            "name": paths["userspace_lock"].name,
            "sha256": hashlib.sha256(paths["userspace_lock"].read_bytes()).hexdigest(),
        }
        locked = json.loads(paths["userspace_lock"].read_text())
        locked["packages"][0]["packageSha256"] = "0" * 64
        paths["userspace_lock"].write_text(json.dumps(locked), encoding="utf-8")
        lock_mismatch = run(
            paths, binaries, temporary / "lock-mismatch.json", False,
            preserve_lock=True,
        )
        assert lock_mismatch["reason"] == "userspace_lock_mismatch"
        assert lock_mismatch["packageMismatches"][0]["invalidFields"] == [
            "packageSha256"
        ]

        paths["userspace_lock"].write_text("[]\n", encoding="utf-8")
        malformed_lock = run(
            paths, binaries, temporary / "malformed-lock-object.json", False,
            preserve_lock=True,
        )
        assert malformed_lock == {
            "schemaVersion": 1,
            "status": "failed",
            "reason": "userspace_lock_invalid",
            "message": "reviewed userspace lock is not an object",
        }

        write_userspace_lock(paths)
        unexpected_one = temporary / "dependency-placeholder.pkg.tar.gz"
        unexpected_two = temporary / "other-placeholder.pkg.tar.gz"
        duplicate_nvidia = temporary / "duplicate-nvidia-utils.pkg.tar.gz"
        make_package(
            unexpected_one, "dependency-placeholder", version="1.0",
            dependencies=("glibc",), provides=("placeholder-provider",),
        )
        make_package(unexpected_two, "other-placeholder", version="2.0")
        make_package(
            duplicate_nvidia, "nvidia-utils", pkgrel="2", gsp=True,
            dependencies=("glibc>=1-1",),
        )
        for package in (unexpected_one, unexpected_two, duplicate_nvidia):
            package.with_suffix(package.suffix + ".sig").write_bytes(b"signature\n")
        paths["dependency_packages"] = [
            unexpected_two, duplicate_nvidia, unexpected_one,
        ]
        aggregate_lock = json.loads(paths["userspace_lock"].read_text())
        by_name = {record["name"]: record for record in aggregate_lock["packages"]}
        nvidia_expected = by_name["nvidia-utils"]
        nvidia_expected.update({
            "filename": "reviewed-nvidia-utils.pkg.tar.zst",
            "signatureFilename": "reviewed-nvidia-utils.pkg.tar.zst.sig",
            "version": "575.64.05-99",
            "architecture": "any",
            "packageSha256": "1" * 64,
            "signatureSha256": "2" * 64,
        })
        lib32_expected = by_name["lib32-nvidia-utils"]
        lib32_expected.update({
            "signerFingerprint": NVIDIA_SIGNER,
            "installedSize": 987654,
            "dependencies": ["different-runtime>=2", "nvidia-utils=575.64.05-2"],
            "provides": ["lib32-opengl-driver"],
        })
        for index, name in enumerate(("egl-x11", "egl-gbm"), 3):
            missing = dict(lib32_expected)
            missing.update({
                "name": name,
                "filename": f"{name}-1-1-x86_64.pkg.tar.zst",
                "signatureFilename": f"{name}-1-1-x86_64.pkg.tar.zst.sig",
                "version": "1-1",
                "packageSha256": str(index) * 64,
                "signatureSha256": str(index + 2) * 64,
                "installedSize": 1024,
                "dependencies": [],
                "provides": [],
            })
            aggregate_lock["packages"].append(missing)
        paths["userspace_lock"].write_text(json.dumps(aggregate_lock), encoding="utf-8")
        aggregate = run(
            paths, binaries, temporary / "aggregate-lock-mismatch.json", False,
            preserve_lock=True,
        )
        aggregate_again = run(
            paths, binaries, temporary / "aggregate-lock-mismatch-again.json", False,
            preserve_lock=True,
        )
        assert aggregate["reason"] == "userspace_lock_mismatch"
        assert aggregate["missingPackages"] == ["egl-gbm", "egl-x11"]
        assert aggregate["unexpectedPackages"] == [
            "dependency-placeholder", "other-placeholder"
        ]
        assert aggregate["duplicatePackages"] == ["nvidia-utils"]
        assert [item["packageName"] for item in aggregate["packageMismatches"]] == [
            "lib32-nvidia-utils", "nvidia-utils"
        ]
        assert aggregate["packageMismatches"][0]["invalidFields"] == [
            "signerFingerprint", "installedSize", "dependencies", "provides"
        ]
        assert aggregate["packageMismatches"][1]["invalidFields"] == [
            "filename", "signatureFilename", "version", "architecture",
            "packageSha256", "signatureSha256",
        ]
        for key in (
            "missingPackages", "unexpectedPackages", "duplicatePackages",
            "packageMismatches",
        ):
            assert aggregate_again[key] == aggregate[key]
        assert str(paths["target"]) not in json.dumps(aggregate)
        aggregate_result = run_installer(
            paths, binaries, temporary / "aggregate-lock-result.json", False,
            preserve_lock=True,
        )
        for key in (
            "missingPackages", "unexpectedPackages", "duplicatePackages",
            "packageMismatches",
        ):
            assert aggregate_result["validation"][key] == aggregate[key]
        assert aggregate_result["cleanup"]["mountsReleased"] is True
        assert "No mutation began." in aggregate_result["message"]
        paths["dependency_packages"] = [paths["dependency"]] * 63
        bounded = run(
            paths, binaries, temporary / "bounded-lock-diagnostics.json", False,
            preserve_lock=True,
        )
        assert bounded["reason"] == "userspace_package_limit_exceeded"
        assert len(json.dumps(bounded)) < 4096
        del paths["dependency_packages"]
        write_userspace_lock(paths)
        assert valid["pacmanDatabase"] == {
            "path": "/usr/lib/holo/pacmandb",
            "packageCount": 4,
        }
        assert valid["boot"]["efiMountPath"] == "/efi"
        assert valid["boot"]["rootfsBootPath"] == "/boot"
        assert valid["boot"]["grubConfiguration"] == "/efi/EFI/steamos/grub.cfg"
        assert valid["storage"]["packageInstalledBytes"] == 8192
        assert valid["storage"]["packageCompressedBytes"] > 0
        assert valid["storage"]["moduleInstalledBytes"] > 0
        assert valid["storage"]["initramfsReserveBytes"] >= 64 * 1024 * 1024
        assert valid["storage"]["rootRequiredBytes"] > 0
        assert [item["name"] for item in valid["packageDependencyClosure"]] == [
            "filesystem",
            "glibc",
            "lib32-nvidia-utils",
            "nvidia-utils",
        ]
        assert valid["compression"]["compressionSavingsCreditedBytes"] == 0
        assert valid["compression"]["filesystem"] == "btrfs"
        assert valid["compression"]["enabled"] is True
        assert valid["compression"]["assessment"] == (
            "informational-package-archive-proxy-not-admission-credit"
        )

        insufficient = run(
            paths,
            binaries,
            temporary / "insufficient-space.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES="1",
        )
        assert insufficient["reason"] == "target_space_insufficient"
        assert insufficient["storage"]["rootAvailableBytes"] == 1
        insufficient_result = run_installer(
            paths,
            binaries,
            temporary / "install-insufficient-space.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES="1",
        )
        assert insufficient_result["reason"] == "target_space_insufficient"
        assert insufficient_result["validation"]["storage"]["rootAvailableBytes"] == 1

        local_database = paths["target"] / "usr/lib/holo/pacmandb/local"
        for name, version, installed_size in (
            ("nvidia-utils", f"{NVIDIA}-1", 3000),
            ("lib32-nvidia-utils", f"{NVIDIA}-1", 2000),
        ):
            record = local_database / f"{name}-{version}"
            record.mkdir()
            (record / "desc").write_text(
                f"%NAME%\n{name}\n\n%VERSION%\n{version}\n\n%ISIZE%\n{installed_size}\n",
                encoding="utf-8",
            )
        replacement = run(paths, binaries, temporary / "replacement-space.json", True)
        assert replacement["storage"]["packageReplacedBytes"] == 5000
        for name, version in (
            ("nvidia-utils", f"{NVIDIA}-1"),
            ("lib32-nvidia-utils", f"{NVIDIA}-1"),
        ):
            record = local_database / f"{name}-{version}"
            (record / "desc").unlink()
            record.rmdir()

        missing_size_record = local_database / f"nvidia-utils-{NVIDIA}-1"
        missing_size_record.mkdir()
        (missing_size_record / "desc").write_text(
            f"%NAME%\nnvidia-utils\n\n%VERSION%\n{NVIDIA}-1\n\n",
            encoding="utf-8",
        )
        missing_replacement_size = run(
            paths, binaries, temporary / "replacement-size-missing.json", False
        )
        assert missing_replacement_size["reason"] == "target_pacman_database_invalid"
        assert missing_replacement_size["packageRecord"] == missing_size_record.name
        assert missing_replacement_size["invalidFields"] == ["ISIZE"]
        (missing_size_record / "desc").unlink()
        missing_size_record.rmdir()
        assert [package["fullVersion"] for package in valid["packages"]] == [
            f"{NVIDIA}-2",
            f"{NVIDIA}-1",
        ]
        for package in valid["packages"]:
            assert set(package) == {
                "name", "role", "filename", "signatureFilename", "fullVersion",
                "pkgver", "pkgrel", "architecture", "signer", "sha256",
                "signatureSha256", "installedSize", "dependencies", "provides",
            }
            assert package["architecture"] == "x86_64"
            assert len(package["sha256"]) == 64
            assert len(package["signatureSha256"]) == 64

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

        pacman_local = paths["target"] / "usr/lib/holo/pacmandb/local"
        hidden_pacman_local = paths["target"] / "usr/lib/holo/pacmandb/local.hidden"
        pacman_local.rename(hidden_pacman_local)
        missing_database = run(
            paths, binaries, temporary / "missing-pacman-database.json", False
        )
        assert missing_database["reason"] == "target_pacman_database_invalid"
        hidden_pacman_local.rename(pacman_local)

        filesystem_desc = pacman_local / "filesystem-1-1/desc"
        valid_filesystem_desc = filesystem_desc.read_text(encoding="utf-8")
        filesystem_desc.write_text("%NAME%\nfilesystem\n", encoding="utf-8")
        malformed_database = run(
            paths, binaries, temporary / "malformed-pacman-database.json", False
        )
        assert malformed_database["reason"] == "target_pacman_database_invalid"
        assert malformed_database["packageRecord"] == "filesystem-1-1"
        assert malformed_database["invalidFields"] == ["VERSION"]
        malformed_database_result = run_installer(
            paths, binaries, temporary / "malformed-pacman-database-result.json", False
        )
        assert malformed_database_result["validation"]["packageRecord"] == "filesystem-1-1"
        assert malformed_database_result["validation"]["invalidFields"] == ["VERSION"]
        filesystem_desc.write_text(valid_filesystem_desc, encoding="utf-8")

        for index, relative in enumerate((
            "etc/modprobe.d",
            f"usr/lib/modules/{KERNEL}/updates",
            "usr/lib/firmware",
            "var/lib",
            "dev",
            "proc",
            "sys",
        )):
            destination = paths["target"] / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            outside = temporary / f"outside-{index}"
            outside.mkdir()
            destination.symlink_to(outside, target_is_directory=True)
            unsafe_destination = run(
                paths, binaries, temporary / f"unsafe-destination-{index}.json", False
            )
            assert unsafe_destination["reason"] == "target_path_unsafe"
            destination.unlink()

        original_archive = paths["archive"]
        original_checksum_path = paths["checksum"]
        for alteration, expected_reason in (
            ("extra", "archive_layout_invalid"),
            ("duplicate", "archive_layout_invalid"),
            ("oversized", "archive_member_too_large"),
        ):
            altered = temporary / f"artifact-{alteration}.tar.gz"
            altered_module_archive(original_archive, altered, alteration)
            altered_checksum = temporary / f"artifact-{alteration}.sha256"
            altered_checksum.write_text(
                f"{hashlib.sha256(altered.read_bytes()).hexdigest()}  {altered.name}\n",
                encoding="utf-8",
            )
            paths["archive"] = altered
            paths["checksum"] = altered_checksum
            invalid_archive = run(
                paths, binaries, temporary / f"invalid-archive-{alteration}.json", False
            )
            assert invalid_archive["reason"] == expected_reason
        paths["archive"] = original_archive
        paths["checksum"] = original_checksum_path

        original_provenance_path = paths["provenance"]
        original_provenance = json.loads(original_provenance_path.read_text())
        duplicate_provenance = dict(original_provenance)
        duplicate_provenance["modules"] = [
            *original_provenance["modules"][:-1],
            original_provenance["modules"][0],
        ]
        duplicate_provenance_bytes = (
            json.dumps(duplicate_provenance, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        duplicate_provenance_path = temporary / "duplicate-module.provenance.json"
        duplicate_provenance_path.write_bytes(duplicate_provenance_bytes)
        duplicate_provenance_archive = temporary / "duplicate-module.tar.gz"
        provenance_archive(
            original_archive, duplicate_provenance_archive, duplicate_provenance_bytes
        )
        duplicate_provenance_checksum = temporary / "duplicate-module.tar.gz.sha256"
        duplicate_provenance_checksum.write_text(
            f"{hashlib.sha256(duplicate_provenance_archive.read_bytes()).hexdigest()}  "
            f"{duplicate_provenance_archive.name}\n",
            encoding="utf-8",
        )
        paths["archive"] = duplicate_provenance_archive
        paths["checksum"] = duplicate_provenance_checksum
        paths["provenance"] = duplicate_provenance_path
        invalid_provenance = run(
            paths, binaries, temporary / "duplicate-provenance-module.json", False
        )
        assert invalid_provenance["reason"] == "provenance_invalid"
        paths["archive"] = original_archive
        paths["checksum"] = original_checksum_path
        paths["provenance"] = original_provenance_path

        oversized_provenance = temporary / "oversized.provenance.json"
        with oversized_provenance.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)
        paths["provenance"] = oversized_provenance
        oversized_input = run(
            paths, binaries, temporary / "oversized-provenance.json", False
        )
        assert oversized_input["reason"] == "input_too_large"
        assert str(temporary) not in json.dumps(oversized_input)
        paths["provenance"] = original_provenance_path

        bad_signature = run(
            paths, binaries, temporary / "bad-signature.json", False,
            MOCK_BAD_SIGNATURE="1",
        )
        assert bad_signature["reason"] == "userspace_signature_invalid"
        assert bad_signature["packageName"] == "nvidia-utils"
        assert bad_signature["signerFingerprint"] is None
        bad_signature_result = run_installer(
            paths, binaries, temporary / "bad-signature-result.json", False,
            MOCK_BAD_SIGNATURE="1",
        )
        assert bad_signature_result["validation"]["packageName"] == "nvidia-utils"
        assert bad_signature_result["validation"]["signerFingerprint"] is None

        cross_package_signer = run(
            paths, binaries, temporary / "cross-package-signer.json", False,
            MOCK_FORCE_SIGNER=LIB32_SIGNER,
        )
        assert cross_package_signer["reason"] == "userspace_lock_mismatch"
        assert cross_package_signer["packageMismatches"][0]["packageName"] == "nvidia-utils"
        assert cross_package_signer["packageMismatches"][0]["invalidFields"] == [
            "signerFingerprint"
        ]

        make_package(
            paths["lib32"], "lib32-nvidia-utils",
            dependencies=("missing-runtime>=1",),
        )
        missing_dependency = run(
            paths, binaries, temporary / "missing-dependency.json", False
        )
        assert missing_dependency["reason"] == "package_dependency_unsatisfied"
        assert missing_dependency["missingDependencies"] == ["missing-runtime>=1"]
        assert missing_dependency["dependencyRequestedBy"] == "lib32-nvidia-utils"
        missing_dependency_result = run_installer(
            paths, binaries, temporary / "missing-dependency-result.json", False
        )
        assert missing_dependency_result["validation"]["missingDependencies"] == [
            "missing-runtime>=1"
        ]
        make_package(
            paths["lib32"], "lib32-nvidia-utils",
            dependencies=("nvidia-utils=575.64.05-2",),
        )

        make_package(
            paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=True,
            dependencies=("glibc>=1-1", "egl-wayland>=4.0.0-1"),
        )
        unstaged_arch_dependency = run(
            paths, binaries, temporary / "egl-wayland-missing.json", False
        )
        assert unstaged_arch_dependency["missingDependencies"] == ["egl-wayland>=4.0.0-1"]
        paths["stage_dependency"] = True
        complete_closure = run(
            paths, binaries, temporary / "complete-dependency-closure.json", True
        )
        assert any(
            package["name"] == "egl-wayland" and package["role"] == "dependency"
            for package in complete_closure["packages"]
        )
        assert any(
            package["name"] == "egl-wayland"
            for package in complete_closure["packageDependencyClosure"]
        )
        del paths["stage_dependency"]
        make_package(
            paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=True,
            dependencies=("glibc>=1-1",),
        )

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

        make_package(
            paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=True,
            duplicate_member=True,
        )
        duplicate_package_member = run(
            paths, binaries, temporary / "duplicate-package-member.json", False
        )
        assert duplicate_package_member["reason"] == "userspace_package_unsafe"

        make_package(
            paths["nvidia"], "nvidia-utils", pkgrel="2", gsp=True,
            special_member=True,
        )
        special_package_member = run(
            paths, binaries, temporary / "special-package-member.json", False
        )
        assert special_package_member["reason"] == "userspace_package_unsafe"

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
        paths["progress_attempt"] = 7
        successful = run_installer(paths, binaries, temporary / "install.json", True)
        assert successful["status"] == "success", successful
        installer_progress = [
            json.loads(line.removeprefix("STEAMOS_NVIDIA_PROGRESS "))
            for line in (temporary / "install.json.stderr").read_text(encoding="utf-8").splitlines()
            if line.startswith("STEAMOS_NVIDIA_PROGRESS ")
        ]
        assert installer_progress
        assert {record["attempt"] for record in installer_progress} == {7}
        del paths["progress_attempt"]
        assert successful["cleanup"]["mountsReleased"] is True
        assert successful["validation"]["keyring"]["name"] == "approved.gpg"
        assert successful["validation"]["provenanceSha256"] == valid["provenanceSha256"]
        assert successful["validation"]["userspaceLock"] == {
            "name": paths["userspace_lock"].name,
            "sha256": hashlib.sha256(paths["userspace_lock"].read_bytes()).hexdigest(),
        }
        assert successful["validation"]["pacmanDatabase"] == {
            "path": "/usr/lib/holo/pacmandb",
            "packageCount": 4,
        }
        assert successful["validation"]["boot"] == valid["boot"]
        assert len(successful["validation"]["keyring"]["sha256"]) == 64
        assert [
            package["fullVersion"]
            for package in successful["validation"]["packages"]
        ] == [f"{NVIDIA}-2", f"{NVIDIA}-1"]
        assert [
            package["signer"]
            for package in successful["validation"]["packages"]
        ] == [NVIDIA_SIGNER, LIB32_SIGNER]
        current_lock = json.loads(paths["userspace_lock"].read_text(encoding="utf-8"))
        current_locked_packages = {
            package["name"]: package for package in current_lock["packages"]
        }
        for package in successful["validation"]["packages"]:
            locked = current_locked_packages[package["name"]]
            assert package["filename"] == locked["filename"]
            assert package["signatureFilename"] == locked["signatureFilename"]
            assert package["fullVersion"] == locked["version"]
            assert package["architecture"] == locked["architecture"]
            assert package["sha256"] == locked["packageSha256"]
            assert package["signatureSha256"] == locked["signatureSha256"]
            assert package["installedSize"] == locked["installedSize"]
            assert package["dependencies"] == sorted(set(locked["dependencies"]))
            assert package["provides"] == sorted(set(locked["provides"]))
        module_root = paths["target"] / "usr/lib/modules" / KERNEL
        assert (module_root / "updates/open-gpu-kernel-modules-steamos/nvidia.ko.zst").is_file()
        assert (module_root / "modules.dep").is_file()
        assert (paths["target"] / "boot/initramfs-fixture.img").is_file()
        grub_path = paths["target"] / "efi/EFI/steamos/grub.cfg"
        first_grub = grub_path.read_bytes()
        for line in grub_path.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("linux ", "linuxefi ", "linux16 ", "steamenv_boot")):
                for argument in (
                    "rd.driver.blacklist=nouveau",
                    "modprobe.blacklist=nouveau",
                    "nvidia-drm.modeset=1",
                    "nvidia-drm.fbdev=1",
                ):
                    assert line.split().count(argument) == 1
                assert "nvidia-drm.modeset=0" not in line.split()
                if "#" in line:
                    assert line.index("nvidia-drm.fbdev=1") < line.index("#")
        assert b"steamenv_boot\tlinux /boot/vmlinuz-linux-neptune-616" in first_grub

        second = run_installer(paths, binaries, temporary / "install-again.json", True)
        assert second["status"] == "success"
        assert grub_path.read_bytes() == first_grub

        valid_grub = grub_path.read_bytes()
        grub_path.write_text("set default=0\n", encoding="utf-8")
        bad_grub = run_installer(
            paths, binaries, temporary / "install-bad-grub.json", False
        )
        assert bad_grub["reason"] == "target_grub_invalid"
        assert bad_grub["phase"] == "validation"
        grub_path.write_bytes(valid_grub)

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
