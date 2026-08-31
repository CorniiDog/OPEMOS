#!/usr/bin/env python3
"""Contract tests for canonical artifact publication."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLISHER = ROOT / "bootstrap" / "publish_artifacts.sh"
STEAMOS = "3.8.16"
NVIDIA = "575.64.05"
KERNEL = "6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45"
TAG = f"steamos-{STEAMOS}-nvidia-{NVIDIA}-k{KERNEL}"
ARCHIVE_NAME = f"nvidia-open-{TAG}-x86_64.tar.gz"
SUPPORT_COMMIT = "a" * 40
SOURCE_COMMIT = "b" * 40
MODULE_NAMES = (
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko",
    "nvidia-uvm.ko",
)


def add_bytes(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def fixture(
    root, *, duplicate_metadata=False, unsafe_member=False,
    wrong_module_hash=False, extra_member=False,
):
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ARCHIVE_NAME
    checksum = root / f"{ARCHIVE_NAME}.sha256"
    build_info = root / f"{ARCHIVE_NAME[:-7]}.build-info.txt"
    provenance_path = root / f"{ARCHIVE_NAME[:-7]}.provenance.json"
    module_bytes = {name: f"fixture:{name}\n".encode() for name in MODULE_NAMES}
    modules = [
        {
            "name": name,
            "sha256": ("0" * 64 if wrong_module_hash and index == 0 else
                       hashlib.sha256(module_bytes[name]).hexdigest()),
            "version": NVIDIA,
            "architecture": "x86_64",
            "vermagic": f"{KERNEL} SMP preempt",
        }
        for index, name in enumerate(MODULE_NAMES)
    ]
    provenance = {
        "schemaVersion": 1,
        "trust": "locally-built-verified",
        "target": {
            "steamosVersion": STEAMOS,
            "kernelVersion": KERNEL,
            "nvidiaVersion": NVIDIA,
            "architecture": "x86_64",
        },
        "artifact": {"releaseTag": TAG, "archive": ARCHIVE_NAME},
        "support": {
            "repository": "CorniiDog/open-gpu-kernel-modules-steamos-support",
            "commit": SUPPORT_COMMIT,
            "dirty": 0,
        },
        "source": {
            "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
            "commit": SOURCE_COMMIT,
            "dirty": 0,
        },
        "modules": modules,
    }
    provenance_bytes = (json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n").encode()
    info = "\n".join((
        "schema_version=1",
        f"steamos_version={STEAMOS}",
        f"kernel_version={KERNEL}",
        f"nvidia_version={NVIDIA}",
        "build_architecture=x86_64",
        "trust_classification=locally-built-verified",
        f"release_tag={TAG}",
        f"release_asset={ARCHIVE_NAME}",
        "support_repository=CorniiDog/open-gpu-kernel-modules-steamos-support",
        f"support_commit={SUPPORT_COMMIT}",
        "source_repository=CorniiDog/open-gpu-kernel-modules-steamos",
        f"source_commit={SOURCE_COMMIT}",
        "",
    )).encode()
    provenance_path.write_bytes(provenance_bytes)
    build_info.write_bytes(info)
    with tarfile.open(archive, "w:gz") as bundle:
        directory = tarfile.TarInfo("modules/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        bundle.addfile(directory)
        add_bytes(bundle, "BUILD-INFO.txt", info)
        add_bytes(bundle, "PROVENANCE.json", provenance_bytes)
        for name in MODULE_NAMES:
            add_bytes(bundle, f"modules/{name}", module_bytes[name])
        if duplicate_metadata:
            add_bytes(bundle, "./PROVENANCE.json", provenance_bytes)
        if unsafe_member:
            add_bytes(bundle, "../escape", b"unsafe\n")
        if extra_member:
            add_bytes(bundle, "unexpected.txt", b"unexpected\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {ARCHIVE_NAME}\n", encoding="utf-8")
    return archive, checksum, build_info, provenance_path


def command(paths, *extra, env=None):
    return subprocess.run(
        [str(PUBLISHER), "--archive", str(paths[0]), "--checksum", str(paths[1]),
         "--build-info", str(paths[2]), "--provenance", str(paths[3]), *extra],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="publisher-") as temporary:
        root = Path(temporary)
        paths = fixture(root)
        dry_run = command(paths, "--dry-run")
        assert dry_run.returncode == 0, dry_run.stderr
        plan = json.loads(dry_run.stdout)
        assert plan["status"] == "ready"
        assert plan["tag"] == TAG
        assert plan["title"] == f"NVIDIA {NVIDIA} for SteamOS {STEAMOS} ({KERNEL})"
        assert [Path(item).name for item in plan["assets"]] == [path.name for path in paths]
        assert f"[{SUPPORT_COMMIT[:7]}]" in plan["notes"]
        assert f"[{SOURCE_COMMIT[:7]}]" in plan["notes"]

        original = paths[1].read_text(encoding="utf-8")
        paths[1].write_text("0" * 64 + f"  {ARCHIVE_NAME}\n", encoding="utf-8")
        assert command(paths, "--dry-run").returncode != 0
        paths[1].write_text(original, encoding="utf-8")

        for name, options in (
            ("duplicate", {"duplicate_metadata": True}),
            ("unsafe", {"unsafe_member": True}),
            ("module-hash", {"wrong_module_hash": True}),
            ("extra", {"extra_member": True}),
        ):
            invalid_paths = fixture(root / name, **options)
            assert command(invalid_paths, "--dry-run").returncode != 0

        mock_bin = root / "bin"
        mock_bin.mkdir()
        log = root / "gh.log"
        gh = mock_bin / "gh"
        gh.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GH_LOG\"\n"
            "case \"$1 $2\" in\n"
            "  'auth status') exit 0;;\n"
            "  'api repos/CorniiDog/open-gpu-kernel-modules-steamos-support') echo true; exit 0;;\n"
            "  'api repos/CorniiDog/open-gpu-kernel-modules-steamos-support/commits/'*) echo \"${GH_TAG_COMMIT:-}\"; exit 0;;\n"
            "  'release view') [ \"${GH_RELEASE_EXISTS:-0}\" = 1 ]; exit $?;;\n"
            "esac\nexit 0\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = os.environ.copy()
        env.update({"PATH": f"{mock_bin}:{env['PATH']}", "GH_LOG": str(log)})

        env["GH_RELEASE_EXISTS"] = "1"
        refused = command(paths, "--create-only", env=env)
        assert refused.returncode != 0
        assert "release already exists" in refused.stderr
        assert "release edit" not in log.read_text(encoding="utf-8")
        assert "release upload" not in log.read_text(encoding="utf-8")

        log.write_text("", encoding="utf-8")
        env.update({"GH_RELEASE_EXISTS": "1", "GH_TAG_COMMIT": "c" * 40})
        mismatched = command(paths, env=env)
        assert mismatched.returncode != 0
        assert "does not point to provenance support commit" in mismatched.stderr
        assert "release edit" not in log.read_text(encoding="utf-8")
        assert "release upload" not in log.read_text(encoding="utf-8")

        log.write_text("", encoding="utf-8")
        env["GH_RELEASE_EXISTS"] = "0"
        created = command(paths, "--create-only", env=env)
        assert created.returncode == 0, created.stderr
        create_line = next(
            line for line in log.read_text(encoding="utf-8").splitlines()
            if line.startswith("release create ")
        )
        positions = [create_line.index(str(path)) for path in paths]
        assert positions == sorted(positions)


if __name__ == "__main__":
    main()
