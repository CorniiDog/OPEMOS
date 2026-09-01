#!/usr/bin/env python3
"""Measure an authenticated NVIDIA payload on disposable compressed Btrfs."""

import argparse
import errno
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
from pathlib import Path
from atomic_output import atomic_write_bytes


PROFILE = "btrfs-zstd3"
MOUNT_OPTIONS = "loop,compress-force=zstd:3"
MAX_PACKAGES = 64
MAX_DECLARED_BYTES = 32 * 1024**3
MAX_PACKAGE_MEMBERS = 250_000
MAX_PACKAGE_EXPANDED_BYTES = 16 * 1024**3
MIN_IMAGE_BYTES = 2 * 1024**3
IMAGE_OVERHEAD_BYTES = 1024**3
PACMAN_METADATA = {".BUILDINFO", ".CHANGELOG", ".INSTALL", ".MTREE", ".PKGINFO"}


MAX_FAILURE_STDERR = 512
MAX_COMMAND_STDOUT = 1024 * 1024


class MeasurementFailure(RuntimeError):
    def __init__(self, reason, message, phase, command=None, exit_status=None,
                 stderr=None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.phase = phase
        self.command = command
        self.exit_status = exit_status
        self.stderr = sanitize_stderr(stderr)

    def document(self):
        return {
            "phase": self.phase,
            "command": self.command,
            "exitStatus": self.exit_status,
            "stderr": self.stderr,
        }


ACTIVE_PROCESS = None
MOUNTED = False


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", action="append", default=[], type=Path)
    parser.add_argument("--module-archive", required=True, type=Path)
    parser.add_argument("--declared-payload-bytes", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sanitize_stderr(value):
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s/:]+/)*[^\s:]*", "<path>", value)
    value = re.sub(
        r"(?i)\b(token|password|secret|authorization|credential)\s*[:=]\s*\S+",
        r"\1=<redacted>", value,
    )
    value = " ".join(value.replace("\x00", " ").split())
    value = "".join(character if 32 <= ord(character) < 127 else "?" for character in value)
    return value[:MAX_FAILURE_STDERR] or None


def failure_for_command(phase, identity, status, stderr):
    typed = {
        "mkfs.btrfs": ("compression_measurement_mkfs_failed",
                       "Scratch Btrfs filesystem creation failed."),
        "mount": ("compression_measurement_mount_failed",
                  "Scratch Btrfs loop mount failed."),
        "btrfs-filesystem-usage": ("compression_measurement_usage_failed",
                                   "Scratch-Btrfs usage collection failed."),
        "zstd-compress": ("compression_measurement_zstd_failed",
                          "Scratch payload compression failed."),
        "zstd-decompress": ("compression_measurement_zstd_failed",
                            "Scratch payload decompression failed."),
        "umount": ("compression_measurement_cleanup_failed",
                   "Scratch-Btrfs measurement cleanup failed."),
        "findmnt": ("compression_measurement_cleanup_failed",
                    "Scratch-Btrfs mount verification failed."),
    }
    reason, message = typed.get(
        identity,
        ("compression_measurement_command_failed",
         "A scratch-Btrfs measurement command failed."),
    )
    searchable_stderr = (
        stderr.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes) else (stderr or "")
    )
    if status is None:
        reason = "compression_measurement_tool_missing"
        message = "A required scratch-Btrfs measurement tool is unavailable."
    elif "no space left on device" in searchable_stderr.lower():
        reason = "compression_measurement_enospc"
        message = "Scratch-Btrfs measurement exhausted its disposable filesystem."
    return MeasurementFailure(reason, message, phase, identity, status, stderr)


def run(command, *, phase, identity, stdout=subprocess.PIPE):
    global ACTIVE_PROCESS
    with tempfile.TemporaryFile() as captured_stdout, tempfile.TemporaryFile() as captured_stderr:
        try:
            ACTIVE_PROCESS = subprocess.Popen(
                command, stdin=subprocess.DEVNULL,
                stdout=captured_stdout if stdout == subprocess.PIPE else stdout,
                stderr=captured_stderr,
            )
            ACTIVE_PROCESS.wait()
            captured_stderr.seek(0)
            error = captured_stderr.read(MAX_FAILURE_STDERR + 1)
            if ACTIVE_PROCESS.returncode != 0:
                raise failure_for_command(
                    phase, identity, ACTIVE_PROCESS.returncode, error
                )
            if stdout != subprocess.PIPE:
                return ""
            captured_stdout.seek(0)
            output = captured_stdout.read(MAX_COMMAND_STDOUT + 1)
            if len(output) > MAX_COMMAND_STDOUT:
                raise MeasurementFailure(
                    "compression_measurement_usage_invalid",
                    "Scratch-Btrfs command output exceeds its size limit.",
                    phase, identity, ACTIVE_PROCESS.returncode, None,
                )
            try:
                return output.decode("utf-8", errors="strict")
            except UnicodeError as error:
                raise MeasurementFailure(
                    "compression_measurement_usage_invalid",
                    "Scratch-Btrfs command output is not UTF-8.",
                    phase, identity, ACTIVE_PROCESS.returncode, None,
                ) from error
        except OSError as error:
            if error.errno == errno.ENOSPC:
                raise MeasurementFailure(
                    "compression_measurement_enospc",
                    "Scratch-Btrfs measurement exhausted storage.",
                    phase, identity, None, str(error),
                ) from error
            raise failure_for_command(phase, identity, None, str(error)) from error
        finally:
            ACTIVE_PROCESS = None


def cancel(_signum, _frame):
    if ACTIVE_PROCESS is not None:
        ACTIVE_PROCESS.kill()
        ACTIVE_PROCESS.wait()
    raise KeyboardInterrupt


def filesystem_usage(path, phase):
    output = run(["btrfs", "filesystem", "usage", "--raw", str(path)],
                 phase=phase, identity="btrfs-filesystem-usage")
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
        raise MeasurementFailure(
            "compression_measurement_usage_invalid",
            "Scratch-Btrfs usage output is incomplete.", phase,
            "btrfs-filesystem-usage", 0, None,
        )
    values.setdefault("system", 0)
    return values


def _install_module_payload(archive_path, destination):
    destination.mkdir(parents=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [
            member for member in archive
            if member.isfile() and member.name.startswith("modules/")
        ]
        if len(members) != 5:
            raise MeasurementFailure("compression_measurement_module_invalid",
                                     "Module payload is incomplete.",
                                     "module_extraction", "module-archive", None, None)
        for member in members:
            source = archive.extractfile(member)
            if source is None:
                raise MeasurementFailure("compression_measurement_module_invalid",
                                         "A module payload member is unreadable.",
                                         "module_extraction", "module-archive", None, None)
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
                        error = ACTIVE_PROCESS.stderr.read()
                        ACTIVE_PROCESS.wait()
                        if ACTIVE_PROCESS.returncode != 0:
                            raise failure_for_command(
                                "module_compression", "zstd-compress",
                                ACTIVE_PROCESS.returncode, error,
                            )
                    except OSError as error:
                        raise failure_for_command(
                            "module_compression", "zstd-compress", None, str(error)
                        ) from error
                    finally:
                        if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
                            ACTIVE_PROCESS.kill()
                            ACTIVE_PROCESS.wait()
                        ACTIVE_PROCESS = None


def install_module_payload(archive_path, destination):
    try:
        _install_module_payload(archive_path, destination)
    except MeasurementFailure:
        raise
    except (OSError, tarfile.TarError) as error:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise MeasurementFailure(
                "compression_measurement_enospc",
                "Scratch-Btrfs measurement exhausted storage.",
                "module_extraction", "module-archive", None, str(error),
            ) from error
        raise MeasurementFailure(
            "compression_measurement_module_invalid",
            "The module payload is unreadable.",
            "module_extraction", "module-archive", None, str(error),
        ) from error


def install_package_payload(package, destination):
    """Write only regular installed payload bytes; never materialize archive paths."""
    global ACTIVE_PROCESS
    package_stream = None
    archive = None
    try:
        destination.mkdir(parents=True)
        if package.name.endswith(".zst"):
            try:
                ACTIVE_PROCESS = subprocess.Popen(
                    ["zstd", "-q", "-d", "-c", str(package)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as error:
                raise failure_for_command(
                    "package_extraction", "zstd-decompress", None, str(error)
                ) from error
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
                raise MeasurementFailure("compression_measurement_package_invalid",
                                         "A userspace package exceeds its member bound.",
                                         "package_extraction", "package-archive", None, None)
            if member.size < 0 or member.size > MAX_PACKAGE_EXPANDED_BYTES:
                raise MeasurementFailure("compression_measurement_package_invalid",
                                         "A userspace package member exceeds its size bound.",
                                         "package_extraction", "package-archive", None, None)
            expanded_bytes += member.size
            if expanded_bytes > MAX_PACKAGE_EXPANDED_BYTES:
                raise MeasurementFailure("compression_measurement_package_invalid",
                                         "A userspace package exceeds its expansion bound.",
                                         "package_extraction", "package-archive", None, None)
            if (not member.isfile()
                    or member.name.removeprefix("./") in PACMAN_METADATA):
                continue
            source = archive.extractfile(member)
            if source is None:
                raise MeasurementFailure("compression_measurement_package_invalid",
                                         "A userspace package member is unreadable.",
                                         "package_extraction", "package-archive", None, None)
            with source, (destination / str(regular_index)).open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            regular_index += 1
        if regular_index == 0:
            raise MeasurementFailure("compression_measurement_package_invalid",
                                     "A userspace package has no installed payload files.",
                                     "package_extraction", "package-archive", None, None)
        if ACTIVE_PROCESS is not None:
            package_stream.close()
            error = ACTIVE_PROCESS.stderr.read()
            ACTIVE_PROCESS.wait()
            if ACTIVE_PROCESS.returncode != 0:
                raise failure_for_command(
                    "package_extraction", "zstd-decompress",
                    ACTIVE_PROCESS.returncode, error,
                )
    except (OSError, tarfile.TarError) as error:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise MeasurementFailure("compression_measurement_enospc",
                                     "Scratch-Btrfs measurement exhausted storage.",
                                     "package_extraction", "package-archive", None,
                                     str(error)) from error
        raise MeasurementFailure("compression_measurement_package_invalid",
                                 "A userspace package payload is unreadable.",
                                 "package_extraction", "package-archive", None,
                                 str(error)) from error
    finally:
        if archive is not None:
            archive.close()
        if ACTIVE_PROCESS is not None:
            if ACTIVE_PROCESS.poll() is None:
                ACTIVE_PROCESS.terminate()
                ACTIVE_PROCESS.wait()
            ACTIVE_PROCESS = None


def write_result(path, document):
    atomic_write_bytes(
        path,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )


def main():
    global MOUNTED
    args = arguments()
    failure = None
    try:
        if (not 2 <= len(args.package) <= MAX_PACKAGES
                or not 0 < args.declared_payload_bytes <= MAX_DECLARED_BYTES):
            raise MeasurementFailure("compression_measurement_input_invalid",
                                     "Scratch-Btrfs measurement inputs exceed their bounds.",
                                     "dependency_check", None, None, None)
        for path in [*args.package, args.module_archive]:
            if path.is_symlink() or not path.is_file():
                raise MeasurementFailure("compression_measurement_input_invalid",
                                         "A scratch-Btrfs measurement input is unsafe.",
                                         "dependency_check", None, None, None)
        for name in ("btrfs", "findmnt", "mkfs.btrfs", "mount", "umount", "zstd"):
            if shutil.which(name) is None:
                raise MeasurementFailure("compression_measurement_tool_missing",
                                         "A required measurement tool is unavailable.",
                                         "dependency_check", name, None, None)

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
            try:
                with image.open("wb") as stream:
                    stream.truncate(image_bytes)
            except OSError as error:
                reason = ("compression_measurement_enospc" if error.errno == errno.ENOSPC
                          else "compression_measurement_image_failed")
                raise MeasurementFailure(reason, "Scratch filesystem image creation failed.",
                                         "image_create", "image-create", None,
                                         str(error)) from error
            try:
                run(["mkfs.btrfs", "-q", "-f", str(image)],
                    phase="filesystem_create", identity="mkfs.btrfs")
                run(["mount", "-o", MOUNT_OPTIONS, str(image), str(mount_path)],
                    phase="mount", identity="mount")
                MOUNTED = True
                baseline = filesystem_usage(mount_path, "baseline_usage")
                previous = baseline
                package_measurements = []
                package_root = mount_path / "payload/packages"
                try:
                    package_root.mkdir(parents=True)
                except OSError as error:
                    reason = ("compression_measurement_enospc"
                              if error.errno == errno.ENOSPC
                              else "compression_measurement_package_invalid")
                    raise MeasurementFailure(
                        reason, "Scratch package destination creation failed.",
                        "package_extraction", "package-archive", None,
                        str(error),
                    ) from error
                for index, package in enumerate(args.package):
                    destination = package_root / str(index)
                    install_package_payload(package, destination)
                    os.sync()
                    current = filesystem_usage(mount_path, "package_usage")
                    package_measurements.append({
                        "filename": package.name,
                        "allocatedBytes": max(0, current["used"] - previous["used"]),
                    })
                    previous = current
                install_module_payload(args.module_archive, mount_path / "payload/modules")
                os.sync()
                measured = filesystem_usage(mount_path, "final_usage")
                module_allocated_bytes = max(0, measured["used"] - previous["used"])
                delta = {
                    key: max(0, measured[key] - baseline[key])
                    for key in ("used", "data", "metadata", "system")
                }
                if delta["used"] <= 0 or delta["data"] <= 0:
                    raise MeasurementFailure("compression_measurement_usage_invalid",
                                             "Scratch-Btrfs payload allocation is empty.",
                                             "final_usage", "btrfs-filesystem-usage",
                                             0, None)
                success = {
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
                }
            except MeasurementFailure as error:
                failure = error
                success = None
            finally:
                try:
                    probe = subprocess.run(
                        ["findmnt", "-rn", "-M", str(mount_path)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        check=False, text=True,
                    )
                    if probe.returncode not in (0, 1):
                        raise failure_for_command(
                            "cleanup", "findmnt", probe.returncode, probe.stderr
                        )
                    mount_is_active = MOUNTED or probe.returncode == 0
                    if mount_is_active:
                        unmount = subprocess.run(
                            ["umount", str(mount_path)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, check=False, text=True,
                        )
                        if unmount.returncode != 0:
                            raise failure_for_command(
                                "cleanup", "umount", unmount.returncode,
                                unmount.stderr,
                            )
                        MOUNTED = False
                    verify = subprocess.run(
                        ["findmnt", "-rn", "-M", str(mount_path)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        check=False, text=True,
                    )
                    if verify.returncode not in (0, 1):
                        raise failure_for_command(
                            "cleanup", "findmnt", verify.returncode, verify.stderr
                        )
                    if verify.returncode == 0:
                        raise MeasurementFailure(
                            "compression_measurement_cleanup_failed",
                            "Scratch-Btrfs measurement cleanup did not release its mount.",
                            "cleanup", "findmnt", verify.returncode, verify.stderr,
                        )
                except OSError as error:
                    failure = failure_for_command("cleanup", "findmnt", None, str(error))
                except MeasurementFailure as error:
                    error.reason = "compression_measurement_cleanup_failed"
                    error.message = "Scratch-Btrfs measurement cleanup failed."
                    failure = error
            if failure is None and success is not None:
                write_result(args.output, success)
                return 0
    except MeasurementFailure as error:
        failure = error
    except OSError as error:
        failure = MeasurementFailure(
            "compression_measurement_enospc" if error.errno == errno.ENOSPC
            else "compression_measurement_image_failed",
            "Scratch measurement workspace creation failed.",
            "image_create", "image-create", None, str(error),
        )
    if failure is not None:
        write_result(args.output, {
            "schemaVersion": 1,
            "status": "failed",
            "reason": failure.reason,
            "message": failure.message,
            "measurementFailure": failure.document(),
        })
        print(f"measure_btrfs_payload.py: {failure.reason}: {failure.message}",
              file=__import__("sys").stderr)
        return 1
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
