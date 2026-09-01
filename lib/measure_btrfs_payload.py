#!/usr/bin/env python3
"""Measure an authenticated NVIDIA payload on disposable compressed Btrfs."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from pathlib import Path


PROFILE = "btrfs-zstd3"
MOUNT_OPTIONS = "loop,compress-force=zstd:3"
MAX_PACKAGES = 64
MAX_DECLARED_BYTES = 32 * 1024**3
MAX_PACKAGE_MEMBERS = 250_000
MAX_PACKAGE_EXPANDED_BYTES = 16 * 1024**3
MIN_IMAGE_BYTES = 2 * 1024**3
IMAGE_OVERHEAD_BYTES = 1024**3
PACMAN_METADATA = {".BUILDINFO", ".CHANGELOG", ".INSTALL", ".MTREE", ".PKGINFO"}


class MeasurementFailure(RuntimeError):
    pass


ACTIVE_PROCESS = None
MOUNTED = False


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", action="append", default=[], type=Path)
    parser.add_argument("--module-archive", required=True, type=Path)
    parser.add_argument("--declared-payload-bytes", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def run(command, *, stdout=subprocess.PIPE):
    global ACTIVE_PROCESS
    try:
        ACTIVE_PROCESS = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=stdout == subprocess.PIPE,
        )
        output, _ = ACTIVE_PROCESS.communicate()
        if ACTIVE_PROCESS.returncode != 0:
            raise MeasurementFailure("a scratch-Btrfs measurement command failed")
        return output or ""
    except OSError as error:
        raise MeasurementFailure("a required scratch-Btrfs command is unavailable") from error
    finally:
        ACTIVE_PROCESS = None


def cancel(_signum, _frame):
    if ACTIVE_PROCESS is not None:
        ACTIVE_PROCESS.kill()
    raise KeyboardInterrupt


def filesystem_usage(path):
    output = run(["btrfs", "filesystem", "usage", "--raw", str(path)])
    values = {}
    for line in output.splitlines():
        overall = re.fullmatch(r"\s*Used:\s*([0-9]+)\s*", line)
        if overall:
            values["used"] = int(overall.group(1))
            continue
        category = re.fullmatch(
            r"\s*(Data|Metadata|System),[^:]+:\s*Size:[^,]+,\s*"
            r"Used:([0-9]+)(?:\s+\([^)]*\))?\s*",
            line,
        )
        if category:
            key = category.group(1).lower()
            values[key] = values.get(key, 0) + int(category.group(2))
    if "used" not in values or "data" not in values or "metadata" not in values:
        raise MeasurementFailure("scratch-Btrfs usage output is incomplete")
    values.setdefault("system", 0)
    return values


def install_module_payload(archive_path, destination):
    destination.mkdir(parents=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [
            member for member in archive
            if member.isfile() and member.name.startswith("modules/")
        ]
        if len(members) != 5:
            raise MeasurementFailure("module archive does not contain five payload members")
        for member in members:
            source = archive.extractfile(member)
            if source is None:
                raise MeasurementFailure("a module payload member is unreadable")
            name = Path(member.name).name
            output = destination / (name if name.endswith(".zst") else f"{name}.zst")
            with source, output.open("xb") as stream:
                if name.endswith(".zst"):
                    shutil.copyfileobj(source, stream, length=1024 * 1024)
                else:
                    global ACTIVE_PROCESS
                    try:
                        ACTIVE_PROCESS = subprocess.Popen(
                            ["zstd", "-q", "-T0", "-c"],
                            stdin=subprocess.PIPE,
                            stdout=stream,
                            stderr=subprocess.PIPE,
                        )
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            ACTIVE_PROCESS.stdin.write(chunk)
                        ACTIVE_PROCESS.stdin.close()
                        ACTIVE_PROCESS.wait()
                        if ACTIVE_PROCESS.returncode != 0:
                            raise MeasurementFailure("module compression failed")
                    finally:
                        if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
                            ACTIVE_PROCESS.kill()
                            ACTIVE_PROCESS.wait()
                        ACTIVE_PROCESS = None


def install_package_payload(package, destination):
    """Write only regular installed payload bytes; never materialize archive paths."""
    global ACTIVE_PROCESS
    destination.mkdir(parents=True)
    package_stream = None
    archive = None
    try:
        if package.name.endswith(".zst"):
            ACTIVE_PROCESS = subprocess.Popen(
                ["zstd", "-q", "-d", "-c", str(package)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            package_stream = ACTIVE_PROCESS.stdout
            archive = tarfile.open(fileobj=package_stream, mode="r|")
        else:
            archive = tarfile.open(package, mode="r:*")
        member_count = 0
        expanded_bytes = 0
        regular_index = 0
        for member in archive:
            member_count += 1
            if member_count > MAX_PACKAGE_MEMBERS:
                raise MeasurementFailure("a userspace package has too many members")
            if member.size < 0 or member.size > MAX_PACKAGE_EXPANDED_BYTES:
                raise MeasurementFailure("a userspace package member exceeds its size bound")
            expanded_bytes += member.size
            if expanded_bytes > MAX_PACKAGE_EXPANDED_BYTES:
                raise MeasurementFailure("a userspace package exceeds its expansion bound")
            if (not member.isfile()
                    or member.name.removeprefix("./") in PACMAN_METADATA):
                continue
            source = archive.extractfile(member)
            if source is None:
                raise MeasurementFailure("a userspace package member is unreadable")
            with source, (destination / str(regular_index)).open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            regular_index += 1
        if regular_index == 0:
            raise MeasurementFailure("a userspace package has no installed payload files")
        if ACTIVE_PROCESS is not None:
            package_stream.close()
            ACTIVE_PROCESS.wait()
            if ACTIVE_PROCESS.returncode != 0:
                raise MeasurementFailure("userspace package decompression failed")
    except (OSError, tarfile.TarError) as error:
        raise MeasurementFailure("a userspace package payload is unreadable") from error
    finally:
        if archive is not None:
            archive.close()
        if ACTIVE_PROCESS is not None:
            if ACTIVE_PROCESS.poll() is None:
                ACTIVE_PROCESS.terminate()
                ACTIVE_PROCESS.wait()
            ACTIVE_PROCESS = None


def write_result(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staged.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)


def main():
    global MOUNTED
    args = arguments()
    if (not 2 <= len(args.package) <= MAX_PACKAGES
            or not 0 < args.declared_payload_bytes <= MAX_DECLARED_BYTES):
        raise SystemExit("Scratch-Btrfs measurement inputs exceed their bounds.")
    for path in [*args.package, args.module_archive]:
        if path.is_symlink() or not path.is_file():
            raise SystemExit("Scratch-Btrfs measurement input is absent or unsafe.")
    for name in ("btrfs", "findmnt", "mkfs.btrfs", "mount", "umount", "zstd"):
        if shutil.which(name) is None:
            raise SystemExit("Scratch-Btrfs measurement dependencies are incomplete.")

    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    image_bytes = max(
        MIN_IMAGE_BYTES,
        ((args.declared_payload_bytes + IMAGE_OVERHEAD_BYTES + 1024**3 - 1) // 1024**3)
        * 1024**3,
    )
    with tempfile.TemporaryDirectory(prefix="steamos-nvidia-btrfs-measurement-") as work:
        work = Path(work)
        image = work / "scratch.btrfs"
        mount_path = work / "mount"
        mount_path.mkdir()
        with image.open("wb") as stream:
            stream.truncate(image_bytes)
        try:
            run(["mkfs.btrfs", "-q", "-f", str(image)])
            run(["mount", "-o", MOUNT_OPTIONS, str(image), str(mount_path)])
            MOUNTED = True
            baseline = filesystem_usage(mount_path)
            previous = baseline
            package_measurements = []
            package_root = mount_path / "payload/packages"
            package_root.mkdir(parents=True)
            for index, package in enumerate(args.package):
                destination = package_root / str(index)
                install_package_payload(package, destination)
                os.sync()
                current = filesystem_usage(mount_path)
                package_measurements.append({
                    "filename": package.name,
                    "allocatedBytes": max(0, current["used"] - previous["used"]),
                })
                previous = current
            install_module_payload(args.module_archive, mount_path / "payload/modules")
            os.sync()
            measured = filesystem_usage(mount_path)
            module_allocated_bytes = max(0, measured["used"] - previous["used"])
            delta = {
                key: max(0, measured[key] - baseline[key])
                for key in ("used", "data", "metadata", "system")
            }
            if delta["used"] <= 0 or delta["data"] <= 0:
                raise MeasurementFailure("scratch-Btrfs payload allocation is empty")
            write_result(args.output, {
                "schemaVersion": 1,
                "status": "measured",
                "profile": PROFILE,
                "writePolicy": "compress-force=zstd:3",
                "measurementMethod": "scratch-btrfs-filesystem-usage-used-delta",
                "declaredPayloadBytes": args.declared_payload_bytes,
                "scratchFilesystemBytes": image_bytes,
                "payloadAllocatedBytes": delta["used"],
                "dataAllocatedBytes": delta["data"],
                "metadataAllocatedBytes": delta["metadata"],
                "systemAllocatedBytes": delta["system"],
                "filesystemOverheadBytes": max(0, delta["used"] - delta["data"]),
                "packageMeasurements": package_measurements,
                "moduleAllocatedBytes": module_allocated_bytes,
            })
        except MeasurementFailure as error:
            raise SystemExit(f"Scratch-Btrfs measurement failed: {error}") from error
        finally:
            mount_is_active = MOUNTED or subprocess.run(
                ["findmnt", "-rn", "-M", str(mount_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            if mount_is_active:
                subprocess.run(
                    ["umount", str(mount_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                MOUNTED = False
            # Fail closed if cleanup did not actually release the scratch tree.
            if subprocess.run(
                ["findmnt", "-rn", "-M", str(mount_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0:
                raise SystemExit("Scratch-Btrfs measurement mount cleanup failed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
