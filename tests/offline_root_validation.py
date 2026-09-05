#!/usr/bin/env python3
"""Synthetic x86 contract tests for offline-root immutable input validation."""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib/validate_install_inputs.py"
INSTALLER = ROOT / "bootstrap/install_to_root.sh"
RESULT_WRITER = ROOT / "lib/write_install_result.py"
PACMAN_RUNNER = ROOT / "lib/run_pacman_transaction.py"
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
INITRAMFS_REQUIRED_MODULES = (
    "nvidia.ko",
    "nvidia-modeset.ko",
    "nvidia-uvm.ko",
    "nvidia-drm.ko",
)

sys.path.insert(0, str(ROOT / "lib"))
from validate_install_contract import validate_progress  # noqa: E402


def add_bytes(archive, name, content, mode=0o644):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def compress_module_archive(paths):
    provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
    compressed = {}
    with tarfile.open(paths["archive"], "r:gz") as archive:
        for name in MODULES:
            payload = archive.extractfile(f"modules/{name}").read()
            completed = subprocess.run(
                ["zstd", "-q", "-c"], input=payload,
                stdout=subprocess.PIPE, check=True,
            )
            compressed[name] = completed.stdout
    hashes = {name: hashlib.sha256(payload).hexdigest()
              for name, payload in compressed.items()}
    for record in provenance["modules"]:
        record["sha256"] = hashes[record["name"]]
    provenance_bytes = (
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    paths["provenance"].write_bytes(provenance_bytes)
    with tarfile.open(paths["archive"], "w:gz") as archive:
        directory = tarfile.TarInfo("modules/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        for name, payload in compressed.items():
            add_bytes(archive, f"modules/{name}.zst", payload, mode=0o777)
        add_bytes(archive, "BUILD-INFO.txt", b"fixture\n")
        add_bytes(archive, "PROVENANCE.json", provenance_bytes)
    paths["checksum"].write_text(
        f"{hashlib.sha256(paths['archive'].read_bytes()).hexdigest()}  "
        f"{paths['archive'].name}\n",
        encoding="utf-8",
    )


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
    (target / "usr/bin").mkdir(parents=True)
    (target / "usr/bin/mkinitcpio").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (target / "usr/bin/mkinitcpio").chmod(0o755)
    (target / "usr/bin/lsinitcpio").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (target / "usr/bin/lsinitcpio").chmod(0o755)
    (target / "usr/lib/initcpio").mkdir(parents=True)
    (target / "usr/share/libalpm/hooks").mkdir(parents=True)
    (target / "etc/mkinitcpio.conf").write_text("HOOKS=(base)\n", encoding="utf-8")
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
    (target / "var/tmp").mkdir(mode=0o1777)
    (target / "var/tmp").chmod(0o1777)
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
            add_bytes(archive, f"modules/{name}", content, mode=0o755)
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
    (root / "appliance-var-tmp").mkdir(mode=0o1777)
    (root / "appliance-var-tmp").chmod(0o1777)
    (root / "appliance-tmp").mkdir(mode=0o700)
    (root / "pacman.conf").write_text(
        "[options]\nArchitecture = auto\nCheckSpace\n"
        "SigLevel = Required DatabaseOptional\nLocalFileSigLevel = Required\n\n"
        "[core]\nInclude = /etc/pacman.d/mirrorlist\n",
        encoding="utf-8",
    )
    real_install = shutil.which("install")
    real_zstd = shutil.which("zstd")
    assert real_install
    assert real_zstd
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
        r"""#!/bin/sh
root=
dbpath=
config=
previous=
for argument in "$@"; do
    [ "$previous" != --root ] || root=$argument
    [ "$previous" != --dbpath ] || dbpath=$argument
    [ "$previous" != --config ] || config=$argument
    previous=$argument
done
[ "$dbpath" = "$root/usr/lib/holo/pacmandb" ] || exit 91
printf '%s\n' "$*" >> "$MOCK_PACMAN_LOG"
case " $* " in
  *" -Dk "*) [ "${MOCK_FAIL_DATABASE_CHECK:-0}" = 0 ];;
  *" -Qkk "*) [ "${MOCK_FAIL_QKK:-0}" = 0 ];;
  *" -U "*)
    for runtime in dev proc sys var/tmp; do
      grep -F -x -q "$root/$runtime" "$MOCK_MOUNT_STATE" || exit 96
    done
    workspace="$root/var/tmp/pacman-hook-mkinitcpio.$$"
    : > "$workspace" || exit 98
    rm -f "$workspace"
    printf '%s\n' pacman-hooks >> "$MOCK_TRANSACTION_LOG"
    if [ "${MOCK_POST_HOOK_FAILURE:-0}" != 0 ]; then
      echo 'error: command failed to execute correctly' >&2
    fi
    if [ "${MOCK_REQUIRE_CHECKSPACE_BYPASS:-0}" != 0 ]; then
      [ -n "$config" ] && [ -f "$config" ] || exit 92
      ! grep -E '^[[:space:]]*CheckSpace([[:space:]]*(#.*)?)?$' "$config" || exit 93
      grep -F -q 'LocalFileSigLevel = Required' "$config" || exit 94
    fi
    if [ "${MOCK_REQUIRE_NORMAL_CHECKSPACE:-0}" != 0 ]; then
      [ -z "$config" ] || exit 95
    fi
    for package in "$@"; do
      case "$package" in *.pkg.tar.gz) bsdtar -xf "$package" -C "$root";; esac
    done
    if [ "${MOCK_CORRUPT_INSTALLED_PAYLOAD:-0}" != 0 ]; then
      printf corrupt > "$root/usr/lib/nvidia-utils/fixture"
    fi
    if [ "${MOCK_REMOVE_GRUB_AFTER_PACMAN:-0}" != 0 ]; then
      rm -f "$root/efi/EFI/steamos/grub.cfg"
    fi
    ;;
  *" -Q nvidia-utils "*)
    if [ "${MOCK_WRONG_INSTALLED_VERSION:-0}" = 0 ]; then
      echo "nvidia-utils $MOCK_NVIDIA-2"
    else
      echo "nvidia-utils 0-0"
    fi
    ;;
  *" -Q lib32-nvidia-utils "*) echo "lib32-nvidia-utils $MOCK_NVIDIA-1" ;;
  *" -Q egl-wayland "*) echo 'egl-wayland 4.0.0-1' ;;
esac
""",
        encoding="utf-8",
    )
    (binaries / "depmod").write_text(
        "#!/bin/sh\n[ \"${MOCK_FAIL_DEPMOD:-0}\" = 0 ] || exit 66\nroot=$2; kernel=$4; mkdir -p \"$root/usr/lib/modules/$kernel\"; echo fixture > \"$root/usr/lib/modules/$kernel/modules.dep\"\n"
        "[ \"${MOCK_DRIFT_TARGET_EXECUTION:-0}\" = 0 ] || printf 'HOOKS=(hostile)\\n' > \"$root/etc/mkinitcpio.conf\"\n",
        encoding="utf-8",
    )
    (binaries / "install").write_text(
        fr"""#!/bin/sh
{real_install} "$@" || exit $?
eval target=\${{$#}}
case "$target" in
  */open-gpu-kernel-modules-steamos/nvidia.ko.zst|\
  */open-gpu-kernel-modules-steamos/nvidia-drm.ko.zst)
    if [ "${{MOCK_CORRUPT_INSTALLED_MODULE:-0}}" != 0 ]; then
      printf corrupt >> "$target"
      case "$target" in *nvidia-drm.ko.zst) chmod 0600 "$target";; esac
    fi
    ;;
esac
""",
        encoding="utf-8",
    )
    (binaries / "zstd").write_text(
        f"""#!/bin/sh
{real_zstd} "$@" || exit $?
previous=
output=
for argument in "$@"; do
  [ "$previous" != -o ] || output=$argument
  previous=$argument
done
case "$output" in
  */module-compression/*)
    [ "${{MOCK_FAIL_MODULE_COMPRESSION:-0}}" = 0 ] || exit 88
    ;;
esac
[ "${{MOCK_CORRUPT_INSTALLED_MODULE:-0}}" = 0 ] && exit 0
previous=
for argument in "$@"; do
  if [ "$previous" = -o ]; then
    case "$argument" in
      */open-gpu-kernel-modules-steamos/nvidia.ko.zst) printf corrupt >> "$argument";;
    esac
  fi
  previous=$argument
done
""",
        encoding="utf-8",
    )
    (binaries / "mount").write_text(
        r"""#!/bin/sh
case " $* " in
  *' --rbind '*)
    eval target=\${$#}
    case "$target" in
      *"/${MOCK_FAIL_RBIND:-__never__}") exit 1;;
    esac
    echo "$target" >> "$MOCK_MOUNT_STATE"
    case "$target" in */dev) echo "$target/pts" >> "$MOCK_MOUNT_STATE";; esac
    ;;
  *' --bind '*) eval target=\${$#}; echo "$target" >> "$MOCK_MOUNT_STATE";;
  *' --make-private '*) :;;
  *' remount,compress-force=zstd:3 '*)
    [ "${MOCK_FAIL_COMPRESSION_ACTIVATE:-0}" = 0 ] || exit 1
    printf '%s\n' 'compress-force=zstd:3' > "$MOCK_COMPRESSION_STATE"
    ;;
  *' remount,compress=no '*) : > "$MOCK_COMPRESSION_STATE";;
  *' remount,compress='*) option=${2#remount,}; printf '%s\n' "$option" > "$MOCK_COMPRESSION_STATE";;
esac
""",
        encoding="utf-8",
    )
    (binaries / "mountpoint").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaries / "flock").write_text(
        "#!/bin/sh\n[ \"${MOCK_FAIL_FLOCK:-0}\" = 0 ]\n", encoding="utf-8"
    )
    (binaries / "findmnt").write_text(
        r"""#!/bin/sh
case " $* " in
  *' -M '*' -o MAJ:MIN'*)
    previous=
    target=
    for argument in "$@"; do
      [ "$previous" != -M ] || target=$argument
      previous=$argument
    done
    grep -F -x -q "$target" "$MOCK_MOUNT_STATE" 2>/dev/null || exit 1
    echo '7:1'
    exit 0
    ;;
  *' -T '*' -o MAJ:MIN'*) echo '7:1'; exit 0;;
  *' -o MAJ:MIN'*)
    echo '7:1'
    [ "${MOCK_SHARED_ROOT_MOUNT:-0}" = 0 ] || echo '7:1'
    exit 0
    ;;
  *' -T '*)
    if [ -z "${MOCK_COMPRESSION_STATE:-}" ]; then
      option='compress=zstd:3'
    else
      option=$(cat "$MOCK_COMPRESSION_STATE" 2>/dev/null || true)
    fi
    extra=${MOCK_MOUNT_EXTRA_OPTION:-}
    if [ -n "$option" ]; then
      output="btrfs rw,$option"
    else
      output='btrfs rw'
    fi
    [ -z "$extra" ] || output="$output,$extra"
    echo "$output"
    exit 0
    ;;
esac
[ "${MOCK_FINDMNT_HIDE_RUNTIME:-0}" = 0 ] || exit 1
eval target=\${$#}; grep -F -q "$target" "$MOCK_MOUNT_STATE" 2>/dev/null
""",
        encoding="utf-8",
    )
    (binaries / "umount").write_text(
        "#!/bin/sh\neval target=\\${$#}; printf '%s\\n' \"$target\" >> \"$MOCK_UMOUNT_LOG\"; grep -F -v \"$target\" \"$MOCK_MOUNT_STATE\" > \"$MOCK_MOUNT_STATE.next\" || true; mv \"$MOCK_MOUNT_STATE.next\" \"$MOCK_MOUNT_STATE\"\n",
        encoding="utf-8",
    )
    (binaries / "chroot").write_text(
        "#!/bin/sh\nif [ \"$2\" = /usr/bin/lsinitcpio ]; then\n"
        "  if [ \"${MOCK_BAD_INITRAMFS_LISTING:-0}\" != 0 ]; then printf 'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf\\n'; exit 0; fi\n"
        "  for module in nvidia nvidia-modeset nvidia-uvm nvidia-drm; do printf 'usr/lib/modules/%s/%s.ko.zst\\n' \"$MOCK_KERNEL\" \"$module\"; done\n"
        "  printf 'etc/modprobe.d/99-open-gpu-kernel-modules-steamos.conf\\n'\n  exit 0\nfi\n"
        "printf active > \"$MOCK_CHROOT_STATE\"\nsleep \"${MOCK_CHROOT_DELAY:-0}\"\n[ \"${MOCK_FAIL_CHROOT:-0}\" = 0 ] || exit 1\nfor runtime in dev proc sys var/tmp; do grep -F -x -q \"$1/$runtime\" \"$MOCK_MOUNT_STATE\" || exit 97; done\nworkspace=\"$1/var/tmp/explicit-mkinitcpio.$$\"\n: > \"$workspace\" || exit 98\nrm -f \"$workspace\"\nprintf '%s\\n' mkinitcpio >> \"$MOCK_TRANSACTION_LOG\"\nmkdir -p \"$1/boot\"; echo initramfs > \"$1/boot/initramfs-fixture.img\"\n[ \"${MOCK_DRIFT_COMPRESSION:-0}\" = 0 ] || : > \"$MOCK_COMPRESSION_STATE\"\n",
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
    if "compression_profile" in paths:
        command.extend(["--compression-profile", paths["compression_profile"]])
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
    if "compression_profile" in paths:
        command.extend(["--compression-profile", paths["compression_profile"]])
    return command


def installer_environment(binaries, mount_state, **environment):
    env = os.environ.copy()
    env.update(
        PATH=f"{binaries}:{env['PATH']}",
        MOCK_NVIDIA=NVIDIA,
        MOCK_KERNEL=KERNEL,
        PROJECT_TEST_MODE="1",
        PROJECT_TEST_APPLIANCE_ARCH="x86_64",
        PROJECT_TEST_PACMAN_CONFIG=str(binaries.parent / "pacman.conf"),
        PROJECT_INITRAMFS_SCRATCH_PARENT=str(binaries.parent / "appliance-var-tmp"),
        PROJECT_TEST_TEMP_ROOT=str(binaries.parent / "appliance-tmp"),
        MOCK_MOUNT_STATE=str(mount_state),
        MOCK_COMPRESSION_STATE=str(mount_state.with_suffix(".compression")),
        MOCK_PACMAN_LOG=str(mount_state.with_suffix(".pacman")),
        MOCK_CHROOT_STATE=str(mount_state.with_suffix(".chroot")),
        MOCK_TRANSACTION_LOG=str(mount_state.with_suffix(".transaction")),
        MOCK_UMOUNT_LOG=str(mount_state.with_suffix(".umount")),
    )
    env.update(environment)
    return env


def parse_progress_records(stderr):
    records = [
        json.loads(line.removeprefix("STEAMOS_NVIDIA_PROGRESS "))
        for line in stderr.splitlines()
        if line.startswith("STEAMOS_NVIDIA_PROGRESS ")
    ]
    for record in records:
        assert set(record) <= {
            "schemaVersion", "attempt", "phase", "indeterminate",
            "unit", "completed", "total",
        }
        assert record["schemaVersion"] == 1
        assert isinstance(record["attempt"], int)
        assert 0 <= record["attempt"] <= 1_000_000
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", record["phase"])
        assert isinstance(record["indeterminate"], bool)
        if record["indeterminate"]:
            assert set(record) == {
                "schemaVersion", "attempt", "phase", "indeterminate",
            }
        else:
            assert set(record) == {
                "schemaVersion", "attempt", "phase", "indeterminate",
                "unit", "completed", "total",
            }
            assert record["unit"] in {"bytes", "items"}
            assert all(
                isinstance(record[field], int) and not isinstance(record[field], bool)
                for field in ("completed", "total")
            )
            assert 0 <= record["completed"] <= record["total"]
    return records


def assert_item_progress(records, phase, total, *, complete=True):
    phase_records = [
        record for record in records
        if record["phase"] == phase and not record["indeterminate"]
    ]
    assert phase_records, phase
    assert all(record["unit"] == "items" for record in phase_records)
    assert all(record["total"] == total for record in phase_records)
    completions = [record["completed"] for record in phase_records]
    assert completions[0] == 0 or (total == 1 and completions[0] == 1)
    assert completions == sorted(completions)
    if complete:
        assert completions[-1] == total


def assert_aggregate_hash_progress(records, expected_total):
    hashing = [
        record for record in records
        if record["phase"] == "hashing" and not record["indeterminate"]
    ]
    assert hashing
    assert all(record["unit"] == "bytes" for record in hashing)
    assert {record["total"] for record in hashing} == {expected_total}
    completions = [record["completed"] for record in hashing]
    assert completions[0] == 0
    assert completions == sorted(completions)
    assert completions[-1] == expected_total


def assert_indeterminate_then_complete(records, phase):
    phase_records = [record for record in records if record["phase"] == phase]
    assert phase_records and phase_records[0]["indeterminate"] is True, phase
    assert phase_records[-1] == {
        "schemaVersion": 1,
        "attempt": phase_records[0]["attempt"],
        "phase": phase,
        "indeterminate": False,
        "unit": "items",
        "completed": 1,
        "total": 1,
    }


def run_installer(paths, binaries, result, success, preserve_lock=False, **environment):
    command = installer_command(paths, result, preserve_lock=preserve_lock)
    mount_state = result.with_suffix(".mounts")
    mount_state.write_text("", encoding="utf-8")
    initial_compression = environment.pop(
        "MOCK_INITIAL_COMPRESSION", "compress=zstd:3"
    )
    mount_state.with_suffix(".compression").write_text(
        initial_compression, encoding="utf-8"
    )
    test_temp_root = binaries.parent / "appliance-tmp"
    before_mutation_work = set(test_temp_root.glob("offline-root-mutation.*"))
    before_input_snapshots = set(test_temp_root.glob("offline-root-inputs.*"))
    before_workspace_results = set(test_temp_root.glob("offline-root-workspace.*"))
    scratch_parent = binaries.parent / "appliance-var-tmp"
    before_scratch = set(scratch_parent.glob("offline-root-initramfs.*"))
    env = installer_environment(binaries, mount_state, **environment)
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result.with_suffix(result.suffix + ".stderr").write_text(completed.stderr, encoding="utf-8")
    if (completed.returncode == 0) != success:
        raise AssertionError(f"installer result did not match expectation: {completed.stderr}")
    remaining_mounts = mount_state.read_text(encoding="utf-8").strip()
    if remaining_mounts:
        unmount_log = mount_state.with_suffix(".umount")
        raise AssertionError(
            "installer left simulated mounts after cleanup: "
            f"result={result.name!r}, remaining={remaining_mounts.splitlines()!r}, "
            f"unmounts={unmount_log.read_text(encoding='utf-8').splitlines() if unmount_log.exists() else []!r}, "
            f"stderr_tail={completed.stderr.splitlines()[-20:]!r}"
        )
    assert mount_state.with_suffix(".compression").read_text().strip() == initial_compression
    assert set(test_temp_root.glob("offline-root-mutation.*")) == before_mutation_work
    assert set(test_temp_root.glob("offline-root-inputs.*")) == before_input_snapshots
    assert set(test_temp_root.glob("offline-root-workspace.*")) == before_workspace_results
    assert set(scratch_parent.glob("offline-root-initramfs.*")) == before_scratch
    return json.loads(result.read_text(encoding="utf-8"))


def cancel_installer(paths, binaries, result, expected_phase, **environment):
    mount_state = result.with_suffix(".mounts")
    mount_state.write_text("", encoding="utf-8")
    initial_compression = environment.pop(
        "MOCK_INITIAL_COMPRESSION", "compress=zstd:3"
    )
    mount_state.with_suffix(".compression").write_text(
        initial_compression, encoding="utf-8"
    )
    env = installer_environment(binaries, mount_state, **environment)
    test_temp_root = binaries.parent / "appliance-tmp"
    before_validation_files = set(test_temp_root.glob("offline-root-validation.*"))
    before_mutation_work = set(test_temp_root.glob("offline-root-mutation.*"))
    before_input_snapshots = set(test_temp_root.glob("offline-root-inputs.*"))
    before_workspace_results = set(test_temp_root.glob("offline-root-workspace.*"))
    scratch_parent = binaries.parent / "appliance-var-tmp"
    before_scratch = set(scratch_parent.glob("offline-root-initramfs.*"))
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
    elif expected_phase == "initramfs":
        child_state = mount_state.with_suffix(".chroot")
        while time.monotonic() < deadline and not child_state.exists():
            time.sleep(0.05)
        assert child_state.exists(), "target mkinitcpio was not started"
    else:
        while (
            time.monotonic() < deadline
            and len(mount_state.read_text().splitlines()) < 4
        ):
            time.sleep(0.05)
        assert len(mount_state.read_text().splitlines()) == 4, (
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
    assert mount_state.with_suffix(".compression").read_text().strip() == initial_compression
    assert set(test_temp_root.glob("offline-root-validation.*")) == before_validation_files
    assert set(test_temp_root.glob("offline-root-mutation.*")) == before_mutation_work
    assert set(test_temp_root.glob("offline-root-inputs.*")) == before_input_snapshots
    assert set(test_temp_root.glob("offline-root-workspace.*")) == before_workspace_results
    assert set(scratch_parent.glob("offline-root-initramfs.*")) == before_scratch
    records = parse_progress_records(stderr)
    if expected_phase == "initramfs":
        assert any(
            record["phase"] == "initramfs" and record["indeterminate"]
            for record in records
        )
        assert_item_progress(records, "mount_cleanup", 4)


def main():
    with tempfile.TemporaryDirectory(prefix="pacman-runner-") as runner_temporary:
        runner_result = Path(runner_temporary) / "launch-failure.json"
        launch_failure = subprocess.run(
            [
                sys.executable, str(PACMAN_RUNNER), "--output", str(runner_result),
                "--", "definitely-not-a-real-pacman-command",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert launch_failure.returncode == 127
        assert launch_failure.stderr == ""
        assert json.loads(runner_result.read_text(encoding="utf-8")) == {
            "schemaVersion": 1,
            "status": "failed",
            "reason": "userspace_transaction_failed",
            "exitStatus": 127,
            "hookFailure": False,
        }
    with tempfile.TemporaryDirectory(prefix="install-result-invariant-") as result_temporary:
        result_temporary = Path(result_temporary)
        workspace_result = result_temporary / "workspace.json"
        workspace_result.write_text(
            json.dumps({
                "schemaVersion": 1,
                "status": "verified",
                "reason": "initramfs_workspace_available",
                "phase": "mounted_workspace",
                "condition": "available",
                "requiredBytes": 1,
                "requiredInodes": 1,
                "availableBytes": 2,
                "availableInodes": 2,
                "mode": "1777",
            }),
            encoding="utf-8",
        )
        missing_modules = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(result_temporary / "false-success.json"),
                "--status", "success", "--reason", "install_complete",
                "--message", "fixture", "--phase", "complete",
                "--root", "/target-root", "--steamos", "3.8.16",
                "--kernel", KERNEL, "--nvidia", NVIDIA,
                "--trust", "locally-built-verified",
                "--archive", "modules.tar.gz",
                "--provenance", "provenance.json",
                "--nvidia-utils", "nvidia-utils.pkg.tar.zst",
                "--lib32-nvidia-utils", "lib32-nvidia-utils.pkg.tar.zst",
                "--runtime-mounts-expected", "4",
                "--runtime-mounts-released", "4",
                "--initramfs-workspace", str(workspace_result),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert missing_modules.returncode != 0
        assert "five-module verification" in missing_modules.stderr
        assert not (result_temporary / "false-success.json").exists()
        module_result = result_temporary / "modules.json"
        module_result.write_text(
            json.dumps({
                "schemaVersion": 1,
                "status": "verified",
                "reason": "installed_modules_verified",
                "modules": [
                    {
                        "moduleName": name,
                        "targetRelativePath": (
                            f"usr/lib/modules/{KERNEL}/updates/"
                            f"open-gpu-kernel-modules-steamos/{name}.zst"
                        ),
                        "representation": ".ko.zst",
                        "expectedPayloadSha256": "0" * 64,
                        "actualPayloadSha256": "0" * 64,
                        "expectedMode": "0644",
                        "actualMode": "0644",
                        "expectedUid": 0,
                        "actualUid": 0,
                        "expectedGid": 0,
                        "actualGid": 0,
                        "compressedSizeBytes": 1,
                        "decompressionStatus": "verified",
                        "invalidFields": [],
                    }
                    for name in MODULES
                ],
            }),
            encoding="utf-8",
        )
        missing_userspace = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(result_temporary / "false-userspace-success.json"),
                "--status", "success", "--reason", "install_complete",
                "--message", "fixture", "--phase", "complete",
                "--root", "/target-root", "--steamos", "3.8.16",
                "--kernel", KERNEL, "--nvidia", NVIDIA,
                "--trust", "locally-built-verified",
                "--archive", "modules.tar.gz",
                "--provenance", "provenance.json",
                "--nvidia-utils", "nvidia-utils.pkg.tar.zst",
                "--lib32-nvidia-utils", "lib32-nvidia-utils.pkg.tar.zst",
                "--runtime-mounts-expected", "4",
                "--runtime-mounts-released", "4",
                "--initramfs-workspace", str(workspace_result),
                "--module-verification", str(module_result),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert missing_userspace.returncode != 0
        assert "userspace verification" in missing_userspace.stderr
        assert not (result_temporary / "false-userspace-success.json").exists()
        malformed_userspace = result_temporary / "malformed-userspace.json"
        malformed_userspace.write_text(
            json.dumps({
                "schemaVersion": 1,
                "status": "verified",
                "reason": "installed_userspace_verified",
                "validationBinding": {
                    "userspaceLockSha256": "a" * 64,
                    "provenanceSha256": "b" * 64,
                },
                "pacmanDatabase": {
                    "path": "/usr/lib/holo/pacmandb",
                    "status": "verified",
                    "verifiedPackageCount": 2,
                    "consistencyVerified": True,
                },
                "packages": [
                    {
                        "packageName": name,
                        "packageFilename": f"{name}-1-1-x86_64.pkg.tar.zst",
                        "version": "1-1",
                        "packageSha256": str(index) * 64,
                        "dependencies": [],
                        "provides": [],
                        "packageQueryVerified": True,
                        "pacmanIntegrityVerified": True,
                        "payloadVerified": True,
                        "payloadPathsConfined": True,
                        "payloadHashesVerified": True,
                        "payloadModesVerified": True,
                        "payloadOwnershipVerified": True,
                        "payloadLinksVerified": True,
                        "directories": 0,
                        "regularFiles": 1,
                        "symlinks": 0,
                        "hardlinks": 0,
                        "sharedLibraries": 0,
                    }
                    for index, name in enumerate(
                        ("nvidia-utils", "lib32-nvidia-utils"), start=1
                    )
                ],
                "gspFirmware": {
                    "version": NVIDIA,
                    "status": "verified",
                    "targetRelativeFiles": [{}],
                },
            }),
            encoding="utf-8",
        )
        malformed_result = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(result_temporary / "malformed-result.json"),
                "--status", "failed", "--reason", "fixture_failure",
                "--message", "fixture", "--phase", "fixture_failure",
                "--root", "/target-root", "--kernel", KERNEL,
                "--userspace-verification", str(malformed_userspace),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert malformed_result.returncode != 0
        assert "Userspace GSP firmware verification is malformed." in (
            malformed_result.stderr
        )
        assert "Traceback" not in malformed_result.stderr
        assert not (result_temporary / "malformed-result.json").exists()
        failed_userspace = result_temporary / "failed-userspace.json"
        failed_userspace_document = {
            "schemaVersion": 1,
            "status": "failed",
            "reason": "installed_userspace_mismatch",
            "message": "fixture diagnostic",
            "packageMismatches": [{
                "packageName": "nvidia-utils",
                "invalidFields": ["payloadHash", "payloadMode"],
                "affectedEntries": ["usr/lib/libnvidia-example.so"],
            }],
        }
        failed_userspace.write_text(
            json.dumps(failed_userspace_document), encoding="utf-8"
        )
        failed_result_path = result_temporary / "failed-userspace-result.json"
        failed_result = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(failed_result_path),
                "--status", "failed", "--reason", "userspace_verification",
                "--message", "fixture", "--phase", "userspace_verification",
                "--root", "/target-root", "--kernel", KERNEL,
                "--userspace-verification", str(failed_userspace),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert failed_result.returncode == 0, failed_result.stderr
        assert json.loads(failed_result_path.read_text(encoding="utf-8"))[
            "userspaceVerification"
        ] == failed_userspace_document
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
            ("compression-profile", ["--compression-profile", "unsafe-profile"]),
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
        # Exercise one aggregate over differently sized package, detached
        # signature, keyring, metadata, and module-archive inputs.
        paths["nvidia_sig"].write_bytes(b"nvidia-signature\n")
        paths["lib32_sig"].write_bytes(b"lib32-signature-with-a-different-size\n")
        paths["keyring"].write_bytes(b"reviewed-keyring-fixture-with-distinct-size\n")
        run(paths, binaries, temporary / "valid.json", True)
        valid = json.loads((temporary / "valid.json").read_text())
        authenticated_inputs = [
            paths[name] for name in (
                "archive", "checksum", "provenance", "nvidia", "nvidia_sig",
                "lib32", "lib32_sig", "keyring", "userspace_lock",
            )
        ]
        assert len({path.stat().st_size for path in authenticated_inputs}) >= 5
        progress_path = temporary / "valid.json.stderr"
        progress_records = parse_progress_records(
            progress_path.read_text(encoding="utf-8")
        )
        assert_aggregate_hash_progress(
            progress_records,
            sum(path.stat().st_size for path in authenticated_inputs),
        )
        assert validate_progress(progress_path) == len(progress_records)
        duplicate_validation = json.loads(json.dumps(valid))
        duplicate_validation["packages"].append(
            dict(duplicate_validation["packages"][0])
        )
        duplicate_validation_path = temporary / "duplicate-validation-package.json"
        duplicate_validation_path.write_text(
            json.dumps(duplicate_validation), encoding="utf-8"
        )
        duplicate_validation_result = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(temporary / "duplicate-validation-result.json"),
                "--status", "failed", "--reason", "fixture_failure",
                "--message", "fixture", "--phase", "fixture_failure",
                "--root", "/target-root", "--kernel", KERNEL,
                "--validation", str(duplicate_validation_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert duplicate_validation_result.returncode != 0
        assert "package identities are duplicated" in (
            duplicate_validation_result.stderr
        )
        assert not (temporary / "duplicate-validation-result.json").exists()
        invalid_database_validation = json.loads(json.dumps(valid))
        invalid_database_validation["pacmanDatabase"]["packageCount"] = True
        invalid_database_path = temporary / "invalid-validation-database.json"
        invalid_database_path.write_text(
            json.dumps(invalid_database_validation), encoding="utf-8"
        )
        invalid_database_result = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(temporary / "invalid-database-result.json"),
                "--status", "failed", "--reason", "fixture_failure",
                "--message", "fixture", "--phase", "fixture_failure",
                "--root", "/target-root", "--kernel", KERNEL,
                "--validation", str(invalid_database_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert invalid_database_result.returncode != 0
        assert "pacman database metadata is invalid" in invalid_database_result.stderr
        assert not (temporary / "invalid-database-result.json").exists()

        missing_root = temporary / "workspace-missing"
        missing_root.mkdir()
        missing_paths = make_fixture(missing_root)
        (missing_paths["target"] / "var/tmp").rmdir()
        missing_validation_result = temporary / "workspace-missing-validation.json"
        missing_mount_state = missing_validation_result.with_suffix(".mounts")
        missing_mount_state.write_text("", encoding="utf-8")
        missing_mount_state.with_suffix(".compression").write_text(
            "compress=zstd:3", encoding="utf-8"
        )
        missing_validation = subprocess.run(
            [*installer_command(missing_paths, missing_validation_result), "--validate-only"],
            env=installer_environment(binaries, missing_mount_state),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert missing_validation.returncode == 0, missing_validation.stderr
        missing_document = json.loads(
            missing_validation_result.read_text(encoding="utf-8")
        )
        assert missing_document["status"] == "validated"
        assert missing_document["initramfsWorkspace"]["status"] == (
            "preparation-required"
        )
        assert missing_document["initramfsWorkspace"]["condition"] == (
            "missing_directory"
        )
        assert not (missing_paths["target"] / "var/tmp").exists()
        missing_installed = run_installer(
            missing_paths,
            binaries,
            temporary / "workspace-missing-install.json",
            True,
        )
        assert missing_installed["initramfsWorkspace"]["status"] == "verified"
        assert stat.S_IMODE((missing_paths["target"] / "var/tmp").stat().st_mode) == 0o1777
        missing_repeated = run_installer(
            missing_paths,
            binaries,
            temporary / "workspace-missing-install-again.json",
            True,
        )
        assert missing_repeated["initramfsWorkspace"]["status"] == "verified"

        dynamic_root = temporary / "workspace-dynamic-inodes"
        dynamic_root.mkdir()
        dynamic_paths = make_fixture(dynamic_root)
        dynamic_result = run_installer(
            dynamic_paths,
            binaries,
            temporary / "workspace-dynamic-inodes.json",
            True,
            PROJECT_TEST_WORKSPACE_DYNAMIC_INODES="1",
        )
        assert dynamic_result["initramfsWorkspace"]["status"] == "verified"
        assert dynamic_result["initramfsWorkspace"]["inodeCapacityMode"] == (
            "dynamic-probed"
        )
        assert dynamic_result["initramfsWorkspace"]["availableInodes"] is None
        assert not list((binaries.parent / "appliance-var-tmp").glob(
            "offline-root-initramfs.*"
        ))

        workspace_cases = (
            ("symlink", "invalid_type", None),
            ("permissions", "permissions", None),
            ("target-bytes", "insufficient_bytes", {
                "PROJECT_TEST_TARGET_WORKSPACE_AVAILABLE_BYTES": "1",
            }),
            ("target-inodes", "insufficient_inodes", {
                "PROJECT_TEST_TARGET_WORKSPACE_AVAILABLE_BYTES": str(2**40),
                "PROJECT_TEST_TARGET_WORKSPACE_AVAILABLE_INODES": "0",
            }),
            ("bytes", "insufficient_bytes", {
                "PROJECT_TEST_WORKSPACE_AVAILABLE_BYTES": "1",
                # A stale discovery view must not strand mounts that the
                # installer itself recorded after successful bind operations.
                "MOCK_FINDMNT_HIDE_RUNTIME": "1",
            }),
            ("inodes", "insufficient_inodes", {
                "PROJECT_TEST_WORKSPACE_AVAILABLE_BYTES": str(2**40),
                "PROJECT_TEST_WORKSPACE_AVAILABLE_INODES": "1",
            }),
        )
        for label, condition, case_environment in workspace_cases:
            case_root = temporary / f"workspace-{label}"
            case_root.mkdir()
            case_paths = make_fixture(case_root)
            if label == "symlink":
                (case_paths["target"] / "var/tmp").rmdir()
                (case_paths["target"] / "var/tmp").symlink_to(
                    case_paths["target"] / "etc", target_is_directory=True
                )
            elif label == "permissions":
                (case_paths["target"] / "var/tmp").chmod(0o755)
            workspace_result = run_installer(
                case_paths,
                binaries,
                temporary / f"workspace-{label}.json",
                False,
                **(case_environment or {}),
            )
            assert workspace_result["reason"] == (
                "initramfs_workspace_unavailable"
            ), workspace_result
            assert workspace_result["initramfsWorkspace"]["condition"] == condition
            assert workspace_result["cleanup"]["mountsReleased"] is True
            preflight_failure = label in {
                "symlink", "permissions", "target-bytes", "target-inodes",
            }
            expected_mounts = 0 if preflight_failure else 4
            assert workspace_result["cleanup"]["runtimeMountsExpected"] == expected_mounts
            expected_released = 0 if preflight_failure else 3
            assert workspace_result["cleanup"]["runtimeMountsReleased"] == expected_released
            pacman_log = (temporary / f"workspace-{label}.mounts").with_suffix(
                ".pacman"
            )
            assert not pacman_log.exists() or " -U " not in (
                " " + pacman_log.read_text(encoding="utf-8") + " "
            )

        # Cleanup can supersede the original workspace failure as the
        # top-level reason.  Preserve the bounded workspace diagnostic rather
        # than allowing result serialization itself to fail and erase both
        # causes.
        cleanup_workspace = temporary / "cleanup-workspace.json"
        cleanup_workspace.write_text(json.dumps(
            json.loads((temporary / "workspace-bytes.json").read_text(
                encoding="utf-8"
            ))["initramfsWorkspace"]
        ), encoding="utf-8")
        cleanup_result = temporary / "cleanup-workspace-result.json"
        cleanup_write = subprocess.run([
            sys.executable, str(RESULT_WRITER),
            "--output", str(cleanup_result),
            "--status", "failed",
            "--reason", "mutation_cleanup_failed",
            "--message", "Offline-root cleanup was incomplete.",
            "--phase", "cleanup",
            "--root", "/target-root",
            "--kernel", KERNEL,
            "--initramfs-workspace", str(cleanup_workspace),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert cleanup_write.returncode == 0, cleanup_write.stderr
        cleanup_document = json.loads(cleanup_result.read_text(encoding="utf-8"))
        assert cleanup_document["reason"] == "mutation_cleanup_failed"
        assert cleanup_document["initramfsWorkspace"]["condition"] == (
            "insufficient_bytes"
        )
        invalid_cleanup_command = list(cleanup_write.args)
        invalid_cleanup_command[
            invalid_cleanup_command.index("cleanup")
        ] = "module_install"
        invalid_cleanup_write = subprocess.run(
            invalid_cleanup_command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert invalid_cleanup_write.returncode != 0
        assert "does not match result status" in invalid_cleanup_write.stderr

        hook_root = temporary / "post-hook-failure"
        hook_root.mkdir()
        hook_paths = make_fixture(hook_root)
        hook_result = run_installer(
            hook_paths,
            binaries,
            temporary / "post-hook-failure.json",
            False,
            MOCK_POST_HOOK_FAILURE="1",
        )
        assert hook_result["status"] == "failed"
        assert hook_result["reason"] == "userspace_hook_failed"
        assert hook_result["phase"] == "userspace_hook_failed"
        assert hook_result["cleanup"]["mountsReleased"] is True
        assert hook_result["cleanup"]["runtimeMountsReleased"] == 4
        assert hook_result["cleanup"]["compressionPolicyRestored"] is True
        hook_progress = parse_progress_records(
            (temporary / "post-hook-failure.json.stderr").read_text(
                encoding="utf-8"
            )
        )
        assert [
            record
            for record in hook_progress
            if record["phase"] == "userspace_install"
        ] == [
            {
                "attempt": 0,
                "completed": 0,
                "indeterminate": False,
                "phase": "userspace_install",
                "schemaVersion": 1,
                "total": len(hook_result["validation"]["packages"]),
                "unit": "items",
            }
        ]
        assert not any(
            record["phase"] in {
                "userspace_verification", "module_install", "module_verification",
                "grub_update", "depmod", "initramfs", "installation_state",
            }
            for record in hook_progress
        )
        assert_item_progress(hook_progress, "mount_cleanup", 4)
        assert (temporary / "post-hook-failure.transaction").read_text(
            encoding="utf-8"
        ).splitlines() == ["pacman-hooks"]
        symlink_root = temporary / "target-root-symlink"
        symlink_root.symlink_to(paths["target"], target_is_directory=True)
        symlink_paths = dict(paths)
        symlink_paths["target"] = symlink_root
        unsafe_root = run(
            symlink_paths, binaries, temporary / "unsafe-root-symlink.json", False
        )
        assert unsafe_root["reason"] == "unsafe_target_root"
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
        insufficient_log = (temporary / "install-insufficient-space.mounts").with_suffix(
            ".pacman"
        )
        assert not insufficient_log.exists() or " -U " not in (
            " " + insufficient_log.read_text(encoding="utf-8") + " "
        )

        ordinary_fixture_root = temporary / "ordinary-checkspace-fixture"
        ordinary_fixture_root.mkdir()
        ordinary_paths = make_fixture(ordinary_fixture_root)
        ordinary_result_path = temporary / "ordinary-checkspace.json"
        ordinary = run_installer(
            ordinary_paths,
            binaries,
            ordinary_result_path,
            True,
            MOCK_REQUIRE_NORMAL_CHECKSPACE="1",
        )
        assert ordinary["reason"] == "install_complete", (
            ordinary,
            ordinary_result_path.with_suffix(".json.stderr").read_text(
                encoding="utf-8"
            ),
        )
        assert ordinary["moduleVerification"]["status"] == "verified"
        assert ordinary["userspaceVerification"]["status"] == "verified"
        assert ordinary["userspaceVerification"]["reason"] == (
            "installed_userspace_verified"
        )
        ordinary_destination = (
            ordinary_paths["target"] / "usr/lib/modules" / KERNEL
            / "updates/open-gpu-kernel-modules-steamos"
        )
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o644
            for path in ordinary_destination.iterdir()
        )
        assert ordinary_result_path.with_suffix(".transaction").read_text(
            encoding="utf-8"
        ).splitlines() == ["pacman-hooks", "mkinitcpio"]
        ordinary_log = ordinary_result_path.with_suffix(".pacman").read_text(
            encoding="utf-8"
        )
        ordinary_transaction = next(
            line for line in ordinary_log.splitlines() if " -U " in f" {line} "
        )
        assert "--config" not in ordinary_transaction

        compressed_fixture_root = temporary / "compressed-module-fixture"
        compressed_fixture_root.mkdir()
        compressed_paths = make_fixture(compressed_fixture_root)
        compress_module_archive(compressed_paths)
        compressed_result = run_installer(
            compressed_paths,
            binaries,
            temporary / "compressed-module-install.json",
            True,
        )
        assert compressed_result["moduleVerification"]["status"] == "verified"
        compressed_destination = (
            compressed_paths["target"] / "usr/lib/modules" / KERNEL
            / "updates/open-gpu-kernel-modules-steamos"
        )
        assert {path.name for path in compressed_destination.iterdir()} == {
            f"{name}.zst" for name in MODULES
        }
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o644
            for path in compressed_destination.iterdir()
        )
        compressed_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in compressed_destination.iterdir()
        }
        compressed_again = run_installer(
            compressed_paths,
            binaries,
            temporary / "compressed-module-install-again.json",
            True,
        )
        assert compressed_again["moduleVerification"]["status"] == "verified"
        assert {
            record["representation"]
            for record in compressed_again["moduleVerification"]["modules"]
        } == {".ko.zst"}
        assert {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in compressed_destination.iterdir()
        } == compressed_hashes

        paths["compression_profile"] = "btrfs-zstd3"
        measured = run(
            paths,
            binaries,
            temporary / "measured-compression.json",
            True,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            PROJECT_TEST_BTRFS_DATA_ALLOCATED_BYTES=str(7 * 1024 * 1024),
            PROJECT_TEST_BTRFS_METADATA_ALLOCATED_BYTES=str(1024 * 1024),
        )
        assert measured["storage"]["rootConservativeRequiredBytes"] > 0
        assert measured["storage"]["rootMeasuredRequiredBytes"] == (
            8 * 1024 * 1024
            + measured["storage"]["initramfsReserveBytes"]
            + 64 * 1024 * 1024
        )
        assert measured["storage"]["rootRequiredBytes"] == measured["storage"][
            "rootMeasuredRequiredBytes"
        ]
        assert measured["compression"]["requestedProfile"] == "btrfs-zstd3"
        assert measured["compression"]["writePolicy"] == "compress-force=zstd:3"
        assert measured["compression"]["admissionAuthorized"] is True
        assert measured["compression"]["mutationProfileImplemented"] is True
        assert measured["compression"]["pacmanCheckSpaceBypassAuthorized"] is True
        assert measured["compression"]["pacmanCheckSpacePolicy"] == (
            "temporary-config-disable-after-live-revalidation"
        )
        assert measured["compression"]["allPayloadDestinationsOnRootFilesystem"] is True
        assert re.fullmatch(
            r"[0-9]+\.[0-9]{6}", measured["compression"]["compressionRatio"]
        )
        assert measured["storage"]["measuredPayloadAllocatedBytes"] == 8 * 1024 * 1024
        assert measured["storage"]["compressionReserveBytes"] == (
            measured["storage"]["initramfsReserveBytes"] + 64 * 1024 * 1024
        )
        assert measured["storage"]["rootFinalMarginBytes"] == (
            measured["storage"]["rootAvailableBytes"]
            - measured["storage"]["rootRequiredBytes"]
        )
        expected_payload_savings = max(
            0,
            measured["compression"]["measurement"]["declaredPayloadBytes"]
            - measured["compression"]["measurement"]["payloadAllocatedBytes"],
        )
        assert measured["compression"]["measuredPayloadSavingsBytes"] == (
            expected_payload_savings
        )
        assert measured["compression"]["declaredSizesLikelyConservative"] is (
            expected_payload_savings > 0
        )

        inconsistent_measurement = json.loads(json.dumps(measured))
        inconsistent_measurement["compression"]["admissionAuthorized"] = False
        inconsistent_validation = temporary / "inconsistent-measurement-validation.json"
        inconsistent_validation.write_text(
            json.dumps(inconsistent_measurement), encoding="utf-8"
        )
        inconsistent_result = temporary / "inconsistent-measurement-result.json"
        completed = subprocess.run(
            [
                sys.executable, str(RESULT_WRITER),
                "--output", str(inconsistent_result), "--status", "validated",
                "--reason", "validation_complete", "--message", "fixture",
                "--phase", "validated", "--root", "/target-root",
                "--steamos", "3.8.16", "--kernel", KERNEL,
                "--nvidia", NVIDIA, "--trust", "locally-built-verified",
                "--validation", str(inconsistent_validation),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode != 0
        assert not inconsistent_result.exists()

        for label, mutate in (
            (
                "ratio",
                lambda document: document["compression"].__setitem__(
                    "compressionRatio", "9.999999"
                ),
            ),
            (
                "margin-type",
                lambda document: document["storage"].__setitem__(
                    "rootFinalMarginBytes", True
                ),
            ),
            (
                "extra-measurement-field",
                lambda document: document["compression"]["measurement"].__setitem__(
                    "unexpected", 1
                ),
            ),
        ):
            tampered = json.loads(json.dumps(measured))
            mutate(tampered)
            tampered_validation = temporary / f"tampered-{label}.json"
            tampered_result = temporary / f"tampered-{label}-result.json"
            tampered_validation.write_text(json.dumps(tampered), encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable, str(RESULT_WRITER),
                    "--output", str(tampered_result), "--status", "validated",
                    "--reason", "validation_complete", "--message", "fixture",
                    "--phase", "validated", "--root", "/target-root",
                    "--steamos", "3.8.16", "--kernel", KERNEL,
                    "--nvidia", NVIDIA, "--trust", "locally-built-verified",
                    "--validation", str(tampered_validation),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert rejected.returncode != 0
            assert not tampered_result.exists()

        measured_insufficient = run(
            paths,
            binaries,
            temporary / "measured-compression-insufficient.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES="1",
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
        )
        assert measured_insufficient["reason"] == "target_space_insufficient"
        assert measured_insufficient["compression"]["admissionAuthorized"] is False
        assert measured_insufficient["compression"][
            "pacmanCheckSpaceBypassAuthorized"
        ] is False
        assert measured_insufficient["compression"]["pacmanCheckSpacePolicy"] == "preserve"

        measurement_failed = run(
            paths,
            binaries,
            temporary / "compression-measurement-failed.json",
            False,
            PROJECT_TEST_BTRFS_MEASUREMENT_FAIL="1",
        )
        assert measurement_failed["reason"] == "compression_measurement_mkfs_failed"
        assert measurement_failed["measurementFailure"] == {
            "phase": "filesystem_create",
            "command": "mkfs.btrfs",
            "exitStatus": 1,
            "stderr": "synthetic measurement failure",
        }
        propagated_measurement = temporary / "compression-measurement-result.json"
        propagated = subprocess.run([
            sys.executable, str(RESULT_WRITER), "--output", str(propagated_measurement),
            "--status", "failed", "--reason", measurement_failed["reason"],
            "--message", measurement_failed["message"], "--phase", "validation",
            "--root", "/target-root", "--kernel", KERNEL,
            "--validation", str(temporary / "compression-measurement-failed.json"),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert propagated.returncode == 0, propagated.stderr
        propagated_document = json.loads(propagated_measurement.read_text(encoding="utf-8"))
        assert (propagated_document["validation"]["measurementFailure"]
                == measurement_failed["measurementFailure"])
        oversized_measurement = dict(measurement_failed)
        oversized_measurement["measurementFailure"] = dict(
            measurement_failed["measurementFailure"], stderr="x" * 513
        )
        oversized_validation = temporary / "oversized-measurement-failure.json"
        oversized_validation.write_text(json.dumps(oversized_measurement), encoding="utf-8")
        rejected_measurement_result = temporary / "rejected-measurement-result.json"
        rejected = subprocess.run([
            sys.executable, str(RESULT_WRITER), "--output", str(rejected_measurement_result),
            "--status", "failed", "--reason", measurement_failed["reason"],
            "--message", measurement_failed["message"], "--phase", "validation",
            "--root", "/target-root", "--kernel", KERNEL,
            "--validation", str(oversized_validation),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert rejected.returncode != 0 and not rejected_measurement_result.exists()

        measurement_invalid = run(
            paths,
            binaries,
            temporary / "compression-measurement-invalid.json",
            False,
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES="1024",
            PROJECT_TEST_BTRFS_DATA_ALLOCATED_BYTES="2048",
        )
        assert measurement_invalid["reason"] == "compression_measurement_invalid"

        ineligible_destination = run(
            paths,
            binaries,
            temporary / "compression-destination-ineligible.json",
            False,
            PROJECT_TEST_COMPRESSION_DESTINATION_INELIGIBLE="1",
        )
        assert ineligible_destination["reason"] == "compression_target_ineligible"

        shared_mount = run(
            paths,
            binaries,
            temporary / "compression-shared-mount.json",
            False,
            PROJECT_TEST_BTRFS_SHARED_MOUNT="1",
        )
        assert shared_mount["reason"] == "compression_mount_not_exclusive"

        incompatible_mount = run(
            paths,
            binaries,
            temporary / "compression-incompatible-mount.json",
            False,
            MOCK_MOUNT_EXTRA_OPTION="nodatacow",
        )
        assert incompatible_mount["reason"] == "compression_profile_unsupported"

        dependency_install_fixture = temporary / "dependency-install-fixture"
        dependency_install_fixture.mkdir()
        dependency_install_paths = make_fixture(dependency_install_fixture)
        make_package(
            dependency_install_paths["nvidia"],
            "nvidia-utils",
            pkgrel="2",
            gsp=True,
            dependencies=("glibc>=1-1", "egl-wayland>=4.0.0-1"),
        )
        dependency_install_paths["stage_dependency"] = True
        dependency_install = run_installer(
            dependency_install_paths,
            binaries,
            temporary / "dependency-install.json",
            True,
        )
        assert [
            package["name"] for package in dependency_install["validation"]["packages"]
        ] == ["nvidia-utils", "lib32-nvidia-utils", "egl-wayland"]

        profile_fixture_root = temporary / "compression-profile-install-fixture"
        profile_fixture_root.mkdir()
        profile_paths = make_fixture(profile_fixture_root)
        profile_paths["compression_profile"] = "btrfs-zstd3"
        activation_failed = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-activation-failed.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_FAIL_COMPRESSION_ACTIVATE="1",
        )
        assert activation_failed["reason"] == "compression_policy_activation"
        assert activation_failed["cleanup"]["mountsReleased"] is True
        assert activation_failed["cleanup"]["compressionPolicyRestored"] is True
        activation_log = (temporary / "compression-profile-activation-failed.pacman")
        assert not activation_log.exists() or " -U " not in (
            " " + activation_log.read_text(encoding="utf-8") + " "
        )
        shared_activation = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-shared-activation.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_SHARED_ROOT_MOUNT="1",
        )
        assert shared_activation["reason"] == "compression_policy_activation"
        assert shared_activation["cleanup"]["compressionPolicyRestored"] is True
        test_temp_root = binaries.parent / "appliance-tmp"
        mutation_work_before = set(test_temp_root.glob("offline-root-mutation.*"))
        profile_mutation_path = temporary / "compression-profile-mutation.json"
        profile_mutation = run_installer(
            profile_paths,
            binaries,
            profile_mutation_path,
            True,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_REQUIRE_CHECKSPACE_BYPASS="1",
        )
        assert profile_mutation["reason"] == "install_complete"
        assert profile_mutation["validation"]["compression"][
            "mutationProfileImplemented"
        ] is True
        assert profile_mutation["cleanup"]["mountsReleased"] is True
        assert profile_mutation["cleanup"]["compressionPolicyRestored"] is True
        profile_transaction = next(
            line for line in profile_mutation_path.with_suffix(".pacman").read_text(
                encoding="utf-8"
            ).splitlines()
            if " -U " in f" {line} "
        )
        assert "--config" in profile_transaction
        assert set(test_temp_root.glob("offline-root-mutation.*")) == mutation_work_before

        invalid_pacman_config = temporary / "invalid-pacman.conf"
        invalid_pacman_config.write_text(
            "[options]\nSigLevel = Required DatabaseOptional\n",
            encoding="utf-8",
        )
        policy_fixture_root = temporary / "pacman-policy-failure-fixture"
        policy_fixture_root.mkdir()
        policy_paths = make_fixture(policy_fixture_root)
        policy_paths["compression_profile"] = "btrfs-zstd3"
        policy_result_path = temporary / "pacman-policy-failure.json"
        policy_failed = run_installer(
            policy_paths,
            binaries,
            policy_result_path,
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            PROJECT_TEST_PACMAN_CONFIG=str(invalid_pacman_config),
        )
        assert policy_failed["reason"] == "pacman_checkspace_policy"
        assert policy_failed["phase"] == "pacman_checkspace_policy"
        assert policy_failed["cleanup"]["mountsReleased"] is True
        assert policy_failed["cleanup"]["compressionPolicyRestored"] is True
        policy_log = policy_result_path.with_suffix(".pacman")
        assert not policy_log.exists() or " -U " not in (
            " " + policy_log.read_text(encoding="utf-8") + " "
        )
        profile_database = profile_paths["target"] / "usr/lib/holo/pacmandb/local"
        for package_name, version, installed_size in (
            ("nvidia-utils", f"{NVIDIA}-2", 8192),
            ("lib32-nvidia-utils", f"{NVIDIA}-1", 4096),
        ):
            record = profile_database / f"{package_name}-{version}"
            record.mkdir()
            (record / "desc").write_text(
                f"%NAME%\n{package_name}\n\n%VERSION%\n{version}\n\n"
                f"%ISIZE%\n{installed_size}\n",
                encoding="utf-8",
            )
        integrity_failed = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-integrity-failed.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_FAIL_QKK="1",
        )
        assert integrity_failed["reason"] == "existing_package_integrity_unverified"
        assert integrity_failed["phase"] == "validation"
        repeated_profile = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-repeat.json",
            True,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_INITIAL_COMPRESSION="",
        )
        assert repeated_profile["validation"]["compression"]["modulePayloadNoop"] is True
        assert repeated_profile["validation"]["storage"]["moduleNoopCreditBytes"] == (
            8 * 1024 * 1024
        )
        wrong_installed_version = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-wrong-installed-version.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_WRONG_INSTALLED_VERSION="1",
        )
        assert wrong_installed_version["status"] == "failed"
        assert wrong_installed_version["reason"] == "userspace_verification"
        assert wrong_installed_version["phase"] == "userspace_verification"
        assert wrong_installed_version["cleanup"]["mountsReleased"] is True
        assert wrong_installed_version["cleanup"]["runtimeMountsReleased"] == 4
        assert wrong_installed_version["cleanup"]["compressionPolicyRestored"] is True
        wrong_version_progress = parse_progress_records(
            (
                temporary
                / "compression-profile-wrong-installed-version.json.stderr"
            ).read_text(encoding="utf-8")
        )
        assert [
            record
            for record in wrong_version_progress
            if record["phase"] == "userspace_verification"
        ] == [
            {
                "attempt": 0,
                "completed": 0,
                "indeterminate": False,
                "phase": "userspace_verification",
                "schemaVersion": 1,
                "total": len(wrong_installed_version["validation"]["packages"]),
                "unit": "items",
            }
        ]
        assert not any(
            record["phase"] in {
                "module_install", "module_verification", "grub_update", "depmod",
                "initramfs", "installation_state",
            }
            for record in wrong_version_progress
        )
        assert_item_progress(wrong_version_progress, "mount_cleanup", 4)
        corrupt_installed_payload = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-corrupt-installed-payload.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_CORRUPT_INSTALLED_PAYLOAD="1",
        )
        assert corrupt_installed_payload["reason"] == "userspace_verification"
        assert corrupt_installed_payload["cleanup"][
            "compressionPolicyRestored"
        ] is True
        inconsistent_database = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-database-inconsistent.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_FAIL_DATABASE_CHECK="1",
        )
        assert inconsistent_database["reason"] == "userspace_verification"
        assert inconsistent_database["cleanup"][
            "compressionPolicyRestored"
        ] is True
        drifted_profile = run_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-drift.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_DRIFT_COMPRESSION="1",
            MOCK_INITIAL_COMPRESSION="",
        )
        assert drifted_profile["reason"] == "initramfs"
        assert drifted_profile["cleanup"]["mountsReleased"] is True
        assert drifted_profile["cleanup"]["compressionPolicyRestored"] is True
        cancel_installer(
            profile_paths,
            binaries,
            temporary / "compression-profile-cancel.json",
            "initramfs",
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_CHROOT_DELAY="30",
            MOCK_INITIAL_COMPRESSION="",
        )
        corrupt_module_fixture = temporary / "corrupt-module-fixture"
        corrupt_module_fixture.mkdir()
        corrupt_module_paths = make_fixture(corrupt_module_fixture)
        corrupt_module_paths["compression_profile"] = "btrfs-zstd3"
        corrupt_module = run_installer(
            corrupt_module_paths,
            binaries,
            temporary / "compression-profile-corrupt-module.json",
            False,
            PROJECT_TEST_ROOT_AVAILABLE_BYTES=str(256 * 1024 * 1024),
            PROJECT_TEST_BTRFS_PAYLOAD_ALLOCATED_BYTES=str(8 * 1024 * 1024),
            MOCK_CORRUPT_INSTALLED_MODULE="1",
        )
        assert corrupt_module["reason"] == "module_install"
        assert corrupt_module["cleanup"]["compressionPolicyRestored"] is True
        mismatches = corrupt_module["moduleVerification"]["moduleMismatches"]
        assert [record["moduleName"] for record in mismatches] == [
            "nvidia-drm.ko", "nvidia.ko",
        ]
        assert "mode" in mismatches[0]["invalidFields"]
        assert all("payloadSha256" in record["invalidFields"] for record in mismatches)
        assert all(record["expectedPayloadSha256"] for record in mismatches)
        assert all(record["compressedSizeBytes"] > 0 for record in mismatches)
        compression_failure_root = temporary / "module-compression-failure-fixture"
        compression_failure_root.mkdir()
        compression_failure_paths = make_fixture(compression_failure_root)
        compression_failure = run_installer(
            compression_failure_paths,
            binaries,
            temporary / "module-compression-failure.json",
            False,
            MOCK_FAIL_MODULE_COMPRESSION="1",
        )
        assert compression_failure["status"] == "failed"
        assert compression_failure["reason"] == "module_install"
        assert compression_failure["phase"] == "module_install"
        assert compression_failure["cleanup"]["mountsReleased"] is True
        assert compression_failure["cleanup"]["runtimeMountsReleased"] == 4
        assert compression_failure["cleanup"]["compressionPolicyRestored"] is True
        compression_failure_progress = parse_progress_records(
            (temporary / "module-compression-failure.json.stderr").read_text(
                encoding="utf-8"
            )
        )
        assert [
            record
            for record in compression_failure_progress
            if record["phase"] == "module_install"
        ] == [
            {
                "attempt": 0,
                "completed": 0,
                "indeterminate": False,
                "phase": "module_install",
                "schemaVersion": 1,
                "total": 5,
                "unit": "items",
            }
        ]
        assert not any(
            record["phase"] in {
                "module_verification", "grub_update", "depmod", "initramfs",
                "installation_state",
            }
            for record in compression_failure_progress
        )
        assert_item_progress(compression_failure_progress, "mount_cleanup", 4)
        del paths["compression_profile"]

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
        validation_mount_drift = run_installer(
            paths,
            binaries,
            temporary / "validation-mount-identity-drift.json",
            False,
            MOCK_PREVALIDATION_MOUNT_IDENTITY_DRIFT="1",
        )
        assert validation_mount_drift["reason"] == "target_mount_identity"
        assert validation_mount_drift["phase"] == "mutation_preflight"
        assert not (temporary / "validation-mount-identity-drift.pacman").exists()

        failed_first_bind = run_installer(
            paths,
            binaries,
            temporary / "first-runtime-bind-failed.json",
            False,
            MOCK_FAIL_RBIND="dev",
        )
        assert failed_first_bind["reason"] == "runtime_mounts"
        assert failed_first_bind["cleanup"]["runtimeMountsReleased"] == 0
        assert not (temporary / "first-runtime-bind-failed.umount").exists()

        drifted_mount_identity = run_installer(
            paths,
            binaries,
            temporary / "target-mount-identity-drift.json",
            False,
            MOCK_MOUNT_IDENTITY_DRIFT="1",
        )
        assert drifted_mount_identity["reason"] == "target_mount_identity"
        assert drifted_mount_identity["phase"] == "target_mount_identity"
        assert not (temporary / "target-mount-identity-drift.pacman").exists()

        locked = run_installer(
            paths,
            binaries,
            temporary / "target-lifecycle-locked.json",
            False,
            MOCK_FAIL_FLOCK="1",
        )
        assert locked["status"] == "failed"
        assert locked["reason"] == "target_lifecycle_locked"
        assert locked["phase"] == "validation"
        assert locked["cleanup"]["mountsReleased"] is True
        assert not (temporary / "target-lifecycle-locked.pacman").exists()

        # Leading zeroes are accepted at the CLI but records must contain a
        # canonical JSON integer, never an invalid JSON numeric literal.
        paths["progress_attempt"] = "0000007"
        successful = run_installer(paths, binaries, temporary / "install.json", True)
        assert successful["status"] == "success", successful
        installer_progress = parse_progress_records(
            (temporary / "install.json.stderr").read_text(encoding="utf-8")
        )
        assert installer_progress
        assert {record["attempt"] for record in installer_progress} == {7}
        installed_package_count = 2 + len(paths.get("dependency_packages", []))
        assert_indeterminate_then_complete(installer_progress, "pacman_policy")
        assert_item_progress(installer_progress, "runtime_mounts", 4)
        assert_item_progress(
            installer_progress, "userspace_install", installed_package_count
        )
        assert_item_progress(
            installer_progress, "userspace_verification", installed_package_count
        )
        assert_item_progress(installer_progress, "module_install", 5)
        assert_item_progress(installer_progress, "module_verification", 5)
        for phase in (
            "grub_update", "depmod", "initramfs", "installation_state",
        ):
            assert_indeterminate_then_complete(installer_progress, phase)
        assert_item_progress(installer_progress, "mount_cleanup", 4)
        del paths["progress_attempt"]
        assert successful["cleanup"]["mountsReleased"] is True
        assert successful["cleanup"]["runtimeMountsExpected"] == 4
        assert successful["cleanup"]["runtimeMountsReleased"] == 4
        assert successful["userspaceVerification"]["status"] == "verified"
        assert successful["userspaceVerification"]["pacmanDatabase"] == {
            "path": "/usr/lib/holo/pacmandb",
            "status": "verified",
            "verifiedPackageCount": len(successful["validation"]["packages"]),
            "consistencyVerified": True,
        }
        assert {
            package["packageName"]
            for package in successful["userspaceVerification"]["packages"]
        } == {
            package["name"] for package in successful["validation"]["packages"]
        }
        assert successful["userspaceVerification"]["gspFirmware"]["version"] == NVIDIA
        assert successful["userspaceVerification"]["gspFirmware"][
            "targetRelativeFiles"
        ] == [f"usr/lib/firmware/nvidia/{NVIDIA}/gsp_ga10x.bin"]
        assert successful["initramfsWorkspace"]["status"] == "verified"
        assert successful["initramfsWorkspace"]["phase"] == "mounted_workspace"
        assert successful["initramfsWorkspace"]["mode"] == "1777"
        assert successful["initramfsVerification"]["status"] == "verified"
        assert successful["initramfsVerification"]["kernelVersion"] == KERNEL
        assert successful["initramfsVerification"]["requiredModules"] == list(
            INITRAMFS_REQUIRED_MODULES
        )
        assert successful["initramfsVerification"]["rootfsOnlyModules"] == [
            "nvidia-peermem.ko"
        ]
        assert len(successful["initramfsVerification"]["images"]) == 1
        assert set(successful["initramfsVerification"]["images"][0]["modules"]) == set(
            INITRAMFS_REQUIRED_MODULES
        )
        receipt = successful["payloadReceipt"]
        assert receipt["status"] == "verified"
        assert receipt["reason"] == "payload_receipt_verified"
        assert receipt["target"] == {
            "steamosVersion": "3.8.16", "kernelVersion": KERNEL,
            "nvidiaVersion": NVIDIA, "architecture": "x86_64",
        }
        assert [record["role"] for record in receipt["records"]] == [
            "buildInfo", "provenance", "validation", "moduleVerification",
            "userspaceVerification", "initramfsVerification",
        ]
        receipt_root = (
            paths["target"]
            / "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install"
        )
        assert (receipt_root / "receipt.json").is_file()
        propagated_verification = temporary / "propagated-receipt.json"
        propagated = subprocess.run([
            sys.executable, str(ROOT / "lib/payload_receipt.py"), "verify",
            "--root", str(paths["target"]),
            "--output", str(propagated_verification),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert propagated.returncode == 0, propagated.stderr
        assert json.loads(propagated_verification.read_text())["receiptId"] == (
            receipt["receiptId"]
        )
        cloned_root = temporary / "repair-device-cloned-root"
        cloned_receipt_root = (
            cloned_root
            / "usr/lib/open-gpu-kernel-modules-steamos-support/offline-install"
        )
        cloned_receipt_root.parent.mkdir(parents=True)
        shutil.copytree(receipt_root, cloned_receipt_root)
        cloned_verification = temporary / "cloned-receipt.json"
        cloned = subprocess.run([
            sys.executable, str(ROOT / "lib/payload_receipt.py"), "verify",
            "--root", str(cloned_root), "--output", str(cloned_verification),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert cloned.returncode == 0, cloned.stderr
        assert json.loads(cloned_verification.read_text())["receiptId"] == receipt["receiptId"]
        assert successful["validation"]["keyring"]["name"] == "approved.gpg"
        assert successful["validation"]["inputSource"] == valid["inputSource"]
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

        raw_first_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (module_root / "updates/open-gpu-kernel-modules-steamos").iterdir()
        }
        second = run_installer(paths, binaries, temporary / "install-again.json", True)
        assert second["status"] == "success"
        assert re.fullmatch(r"[0-9a-f]{64}", second["payloadReceipt"]["receiptId"])
        assert second["payloadReceipt"]["target"] == receipt["target"]
        assert second["moduleVerification"]["status"] == "verified"
        assert {
            record["representation"]
            for record in second["moduleVerification"]["modules"]
        } == {".ko.zst"}
        assert {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (module_root / "updates/open-gpu-kernel-modules-steamos").iterdir()
        } == raw_first_hashes
        assert grub_path.read_bytes() == first_grub

        valid_grub = grub_path.read_bytes()
        grub_path.write_text("set default=0\n", encoding="utf-8")
        bad_grub = run_installer(
            paths, binaries, temporary / "install-bad-grub.json", False
        )
        assert bad_grub["reason"] == "target_grub_invalid"
        assert bad_grub["phase"] == "validation"
        grub_path.write_bytes(valid_grub)

        drifted_execution = run_installer(
            paths,
            binaries,
            temporary / "install-target-execution-drift.json",
            False,
            MOCK_DRIFT_TARGET_EXECUTION="1",
        )
        assert drifted_execution["reason"] == "target_execution_trust"
        assert drifted_execution["phase"] == "target_execution_trust"
        assert drifted_execution["message"] == (
            "Target-owned execution trust validation failed; discard the "
            "disposable overlay."
        )
        assert drifted_execution["targetExecutionFailure"]["status"] == "failed"
        assert drifted_execution["targetExecutionFailure"]["reason"] == (
            "target_execution_trust_failed"
        )
        assert drifted_execution["targetExecutionFailure"]["condition"] == (
            "execution_inputs_changed"
        )
        assert drifted_execution["cleanup"]["mountsReleased"] is True
        (paths["target"] / "etc/mkinitcpio.conf").write_text(
            "HOOKS=(base)\n", encoding="utf-8"
        )

        failed_verification = run_installer(
            paths,
            binaries,
            temporary / "install-initramfs-verification-failed.json",
            False,
            MOCK_BAD_INITRAMFS_LISTING="1",
        )
        assert failed_verification["reason"] == "initramfs_verification"
        assert failed_verification["cleanup"]["mountsReleased"] is True
        assert failed_verification["cleanup"]["runtimeMountsReleased"] == 4

        depmod_failed = run_installer(
            paths,
            binaries,
            temporary / "depmod-failed.json",
            False,
            MOCK_FAIL_DEPMOD="1",
        )
        assert depmod_failed["status"] == "failed"
        assert depmod_failed["reason"] == "depmod"
        assert depmod_failed["phase"] == "depmod"
        assert depmod_failed["cleanup"]["mountsReleased"] is True
        depmod_progress = parse_progress_records(
            (temporary / "depmod-failed.json.stderr").read_text(encoding="utf-8")
        )
        depmod_records = [
            record for record in depmod_progress if record["phase"] == "depmod"
        ]
        assert depmod_records[-1]["indeterminate"] is True
        assert not any(record["phase"] == "initramfs" for record in depmod_progress)
        assert_item_progress(depmod_progress, "mount_cleanup", 4)

        grub_failed = run_installer(
            paths,
            binaries,
            temporary / "grub-failed.json",
            False,
            MOCK_REMOVE_GRUB_AFTER_PACMAN="1",
        )
        assert grub_failed["status"] == "failed"
        assert grub_failed["reason"] == "bootloader_config"
        assert grub_failed["phase"] == "bootloader_config"
        assert grub_failed["cleanup"]["mountsReleased"] is True
        assert grub_failed["cleanup"]["runtimeMountsReleased"] == 4
        grub_progress = parse_progress_records(
            (temporary / "grub-failed.json.stderr").read_text(encoding="utf-8")
        )
        grub_records = [
            record for record in grub_progress if record["phase"] == "grub_update"
        ]
        assert grub_records == [
            {
                "attempt": 0,
                "indeterminate": True,
                "phase": "grub_update",
                "schemaVersion": 1,
            }
        ]
        assert not any(
            record["phase"] in {"depmod", "initramfs"}
            for record in grub_progress
        )
        assert_item_progress(grub_progress, "mount_cleanup", 4)
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
        assert failed["phase"] == "initramfs"
        assert failed["cleanup"]["mountsReleased"] is True
        assert failed["cleanup"]["runtimeMountsReleased"] == 4
        assert failed["cleanup"]["compressionPolicyRestored"] is True
        failed_progress = parse_progress_records(
            (temporary / "install-failed.json.stderr").read_text(encoding="utf-8")
        )
        failed_initramfs = [
            record for record in failed_progress if record["phase"] == "initramfs"
        ]
        assert failed_initramfs == [
            {
                "attempt": 0,
                "indeterminate": True,
                "phase": "initramfs",
                "schemaVersion": 1,
            }
        ]
        assert not any(
            record["phase"] == "installation_state"
            for record in failed_progress
        )
        assert_item_progress(failed_progress, "mount_cleanup", 4)

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
