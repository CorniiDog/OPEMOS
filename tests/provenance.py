#!/usr/bin/env python3
"""Contract test for structured offline-target build provenance."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / "lib" / "write_build_provenance.py"
MODULES = (
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko",
    "nvidia-peermem.ko", "nvidia-uvm.ko",
)


BUILD_INFO = """schema_version=1
build_started_at=2026-08-30T12:00:00+00:00
build_completed_at=2026-08-30T12:30:00+00:00
steamos_version=3.8.14
kernel_version=kernel-exact
nvidia_version=575.64.05
release_tag=release-tag
release_asset=artifact.tar.gz
source_repository=owner/source
source_branch=nvidia/575.64.05
source_commit=source-commit
source_dirty=0
support_repository=owner/support
support_commit=support-commit
support_dirty=0
build_mode=offline-target-fedora
build_architecture=x86_64
build_os=Fedora Linux 44
trust_classification=locally-built-verified
compiler_command=gcc-15
compiler_version=15.1.1
kernel_compiler_version=15.1.1
compiler_major_match=1
kernel_compiler_definition=gcc 15.1.1
binutils_version=GNU ld 2.45
make_version=GNU Make 4.4
kmod_version=kmod version 34
header_package=headers.pkg.tar.zst
header_url=https://example.invalid/headers.pkg.tar.zst
header_sha256=header-sha256
header_package_name=linux-neptune-headers
header_package_version=1-1
header_package_architecture=x86_64
header_signature_status=verified
header_signing_key_fingerprint=SIGNER
header_primary_key_fingerprint=PRIMARY
header_authentication=detached-signature-verified-with-pinned-keyring
"""


def main():
    with tempfile.TemporaryDirectory(prefix="provenance-") as temporary:
        temporary = Path(temporary)
        build_info = temporary / "BUILD-INFO.txt"
        modules = temporary / "modules.json"
        output = temporary / "provenance.json"
        build_info.write_text(BUILD_INFO, encoding="utf-8")
        modules.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "verified",
                    "modules": [
                        {
                            "name": name,
                            "sha256": "module-sha256",
                            "version": "575.64.05",
                            "architecture": "x86_64",
                            "vermagic": "kernel-exact SMP",
                        }
                        for name in MODULES
                    ],
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(WRITER),
                "--build-info",
                str(build_info),
                "--modules",
                str(modules),
                "--output",
                str(output),
            ],
            check=True,
        )
        result = json.loads(output.read_text(encoding="utf-8"))
        assert result["schemaVersion"] == 1
        assert result["trust"] == "locally-built-verified"
        assert result["target"]["kernelVersion"] == "kernel-exact"
        assert result["build"]["toolchain"]["compilerMajorMatch"] == "1"
        assert result["headers"]["signatureStatus"] == "verified"
        assert result["source"]["commit"] == "source-commit"
        assert result["support"]["commit"] == "support-commit"
        assert result["modules"][0]["sha256"] == "module-sha256"

        duplicate_info = temporary / "duplicate-BUILD-INFO.txt"
        duplicate_info.write_text(
            BUILD_INFO + "kernel_version=other-kernel\n", encoding="utf-8"
        )
        rejected = subprocess.run(
            [
                sys.executable, str(WRITER), "--build-info", str(duplicate_info),
                "--modules", str(modules), "--output", str(temporary / "rejected.json"),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert rejected.returncode != 0
        assert "duplicates field" in rejected.stderr
        assert "Traceback" not in rejected.stderr
        assert not (temporary / "rejected.json").exists()

        modules_link = temporary / "modules-link.json"
        modules_link.symlink_to(modules)
        rejected_link = subprocess.run(
            [
                sys.executable, str(WRITER), "--build-info", str(build_info),
                "--modules", str(modules_link),
                "--output", str(temporary / "rejected-link.json"),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert rejected_link.returncode != 0
        assert "unreadable or excessive" in rejected_link.stderr
        assert "Traceback" not in rejected_link.stderr


if __name__ == "__main__":
    main()
