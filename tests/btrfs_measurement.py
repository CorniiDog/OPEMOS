#!/usr/bin/env python3
"""Exercise scratch-Btrfs measurement success, failure, and cleanup."""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEASURER = ROOT / "lib/measure_btrfs_payload.py"
MODULES = ("nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko", "nvidia-uvm.ko")


def add_bytes(archive, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, __import__("io").BytesIO(data))


def fixtures(root):
    packages = []
    for index in range(2):
        package = root / f"package-{index}.pkg.tar.gz"
        with tarfile.open(package, "w:gz") as archive:
            add_bytes(archive, ".PKGINFO", b"pkgname = fixture\n")
            add_bytes(archive, f"usr/lib/fixture/{index}", b"package payload\n")
        packages.append(package)
    modules = root / "modules.tar.gz"
    with tarfile.open(modules, "w:gz") as archive:
        directory = tarfile.TarInfo("modules")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for name in MODULES:
            add_bytes(archive, f"modules/{name}", (name + " payload\n").encode())
    return packages, modules


def mocks(root):
    binary = root / "bin"
    binary.mkdir()
    real_zstd = shutil.which("zstd")
    assert real_zstd
    scripts = {
        "mkfs.btrfs": "#!/bin/sh\nexit 0\n",
        "mount": "#!/bin/sh\nprintf mounted > \"$MEASURE_MOUNT_STATE\"\n",
        "umount": "#!/bin/sh\nrm -f \"$MEASURE_MOUNT_STATE\"\n",
        "findmnt": "#!/bin/sh\n[ -f \"$MEASURE_MOUNT_STATE\" ]\n",
        "btrfs": """#!/bin/sh
count=0
[ ! -f "$MEASURE_USAGE_COUNT" ] || count=$(cat "$MEASURE_USAGE_COUNT")
count=$((count + 1)); printf '%s\n' "$count" > "$MEASURE_USAGE_COUNT"
[ "${MEASURE_FAIL_USAGE:-0}" = 0 ] || [ "$count" = 1 ] || exit 1
if [ "$count" = 1 ]; then
    used=1000; data=600; metadata=300; system=100
else
    used=91000; data=80600; metadata=9300; system=1100
fi
cat <<EOF
Overall:
    Used: $used
Data,single: Size:1000000, Used:$data (1.00%)
Metadata,DUP: Size:1000000, Used:$metadata (1.00%)
System,DUP: Size:1000000, Used:$system (1.00%)
EOF
""",
        "zstd": (
            "#!/bin/sh\n"
            "if [ \"${MEASURE_DELAY:-0}\" != 0 ]; then "
            "printf active > \"$MEASURE_CHILD_STATE\"; exec sleep 30; fi\n"
            f"exec {real_zstd} \"$@\"\n"
        ),
    }
    for name, script in scripts.items():
        path = binary / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    return binary


def command(packages, modules, output):
    result = [
        str(MEASURER), "--module-archive", str(modules),
        "--declared-payload-bytes", "1048576", "--output", str(output),
    ]
    for package in packages:
        result.extend(("--package", str(package)))
    return result


def environment(binary, root, **extra):
    result = os.environ.copy()
    result.update(
        PATH=f"{binary}:{result['PATH']}",
        MEASURE_MOUNT_STATE=str(root / "mount-state"),
        MEASURE_USAGE_COUNT=str(root / "usage-count"),
        MEASURE_CHILD_STATE=str(root / "child-state"),
        **extra,
    )
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="btrfs-measurement-test-") as temporary:
        temporary = Path(temporary)
        packages, modules = fixtures(temporary)
        binary = mocks(temporary)
        output = temporary / "result.json"
        subprocess.run(
            command(packages, modules, output),
            check=True,
            env=environment(binary, temporary),
        )
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["schemaVersion"] == 1 and document["status"] == "measured"
        assert document["profile"] == "btrfs-zstd3"
        assert document["writePolicy"] == "compress-force=zstd:3"
        assert document["payloadAllocatedBytes"] == 90000
        assert document["dataAllocatedBytes"] == 80000
        assert document["metadataAllocatedBytes"] == 9000
        assert document["systemAllocatedBytes"] == 1000
        assert document["filesystemOverheadBytes"] == 10000
        assert not (temporary / "mount-state").exists()

        (temporary / "usage-count").unlink()
        output.unlink()
        subprocess.run(
            command(packages, modules, output),
            check=True,
            env=environment(binary, temporary),
        )
        repeated = json.loads(output.read_text(encoding="utf-8"))
        for field in (
            "payloadAllocatedBytes", "dataAllocatedBytes", "metadataAllocatedBytes",
            "systemAllocatedBytes", "filesystemOverheadBytes",
        ):
            assert repeated[field] == document[field]
        assert not (temporary / "mount-state").exists()

        (temporary / "usage-count").unlink()
        output.unlink()
        failed = subprocess.run(
            command(packages, modules, output),
            env=environment(binary, temporary, MEASURE_FAIL_USAGE="1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert failed.returncode != 0 and not output.exists()
        assert not (temporary / "mount-state").exists()

        (temporary / "usage-count").unlink()
        child_state = temporary / "child-state"
        process = subprocess.Popen(
            command(packages, modules, output),
            env=environment(binary, temporary, MEASURE_DELAY="1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_state.exists():
            time.sleep(0.05)
        assert (temporary / "mount-state").exists(), "scratch mount was not established"
        assert child_state.exists(), "cancellable measurement child was not started"
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode != 0 and not output.exists()
        assert not (temporary / "mount-state").exists()


if __name__ == "__main__":
    main()
