#!/usr/bin/env python3
"""File-backed regression tests for built NVIDIA module validation."""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "lib" / "validate_built_modules.py"
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfe145653a794"
NVIDIA = "575.64.05"
MODULES = (
    "nvidia.ko",
    "nvidia-drm.ko",
    "nvidia-modeset.ko",
    "nvidia-peermem.ko",
    "nvidia-uvm.ko",
)


MOCK_MODINFO = """#!/usr/bin/env python3
import os, pathlib, sys
field, module = sys.argv[2], pathlib.Path(sys.argv[3]).name
bad = os.environ.get('MOCK_BAD_MODULE') == module
if os.environ.get('MOCK_METADATA_FAILURE') == module:
    raise SystemExit(1)
if field == 'version':
    print('0.0.0' if bad and os.environ.get('MOCK_FAILURE') == 'version' else os.environ['MOCK_NVIDIA'])
elif field == 'vermagic':
    kernel = 'wrong-kernel' if bad and os.environ.get('MOCK_FAILURE') == 'vermagic' else os.environ['MOCK_KERNEL']
    print(kernel + ' SMP preempt mod_unload')
"""

MOCK_READELF = """#!/usr/bin/env python3
import os, pathlib, sys
module = pathlib.Path(sys.argv[-1]).name
bad = os.environ.get('MOCK_BAD_MODULE') == module and os.environ.get('MOCK_FAILURE') == 'architecture'
print('Machine: AArch64' if bad else 'Machine: Advanced Micro Devices X86-64')
"""


def executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def validate(directory, modules, *, failure=None, bad_module="nvidia.ko", metadata=False):
    output = directory / "result.json"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{directory / 'bin'}:{environment['PATH']}",
            "MOCK_KERNEL": KERNEL,
            "MOCK_NVIDIA": NVIDIA,
            "MOCK_BAD_MODULE": bad_module,
            "MOCK_FAILURE": failure or "",
            "MOCK_METADATA_FAILURE": bad_module if metadata else "",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--kernel",
            KERNEL,
            "--nvidia",
            NVIDIA,
            "--architecture",
            "x86_64",
            "--output",
            str(output),
            *map(str, modules),
        ],
        env=environment,
    )
    return completed.returncode, json.loads(output.read_text(encoding="utf-8"))


def main():
    with tempfile.TemporaryDirectory(prefix="module-validation-") as temporary:
        temporary = Path(temporary)
        binary_directory = temporary / "bin"
        binary_directory.mkdir()
        executable(binary_directory / "modinfo", MOCK_MODINFO)
        executable(binary_directory / "readelf", MOCK_READELF)
        module_directory = temporary / "modules"
        module_directory.mkdir()
        modules = []
        for name in MODULES:
            module = module_directory / name
            module.write_text(f"fixture:{name}\n", encoding="utf-8")
            modules.append(module)

        returncode, result = validate(temporary, modules)
        assert returncode == 0 and result["status"] == "verified"
        assert {item["name"] for item in result["modules"]} == set(MODULES)
        assert all(item["version"] == NVIDIA for item in result["modules"])

        cases = (
            (modules[:-1], None, False, "module_set_incomplete"),
            (modules[:-1] + [modules[0]], None, False, "module_set_incomplete"),
            (modules, "version", False, "module_version_mismatch"),
            (modules, "vermagic", False, "vermagic_mismatch"),
            (modules, "architecture", False, "module_architecture_mismatch"),
            (modules, None, True, "module_metadata_invalid"),
        )
        for selected, failure, metadata, expected_reason in cases:
            returncode, result = validate(
                temporary, selected, failure=failure, metadata=metadata
            )
            assert returncode != 0
            assert result["status"] == "failed"
            assert result["reason"] == expected_reason

        unsafe = module_directory / "unsafe"
        unsafe.mkdir()
        unsafe_modules = []
        for module in modules:
            link = unsafe / module.name
            link.symlink_to(module)
            unsafe_modules.append(link)
        returncode, result = validate(temporary, unsafe_modules)
        assert returncode != 0
        assert result["reason"] == "module_set_incomplete"


if __name__ == "__main__":
    main()
