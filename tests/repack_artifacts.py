#!/usr/bin/env python3
"""Contract tests for deterministic, revisioned module repacking."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from publisher import KERNEL, NVIDIA, SUPPORT_COMMIT, fixture


ROOT = Path(__file__).resolve().parent.parent
REPACK = ROOT / "lib/repack_module_artifact.py"
PUBLISH = ROOT / "bootstrap/publish_artifacts.sh"


def run(paths, output, env, *extra):
    return subprocess.run([
        sys.executable, str(REPACK), "--archive", str(paths[0]),
        "--checksum", str(paths[1]), "--build-info", str(paths[2]),
        "--provenance", str(paths[3]), "--output-dir", str(output),
        "--support-commit", SUPPORT_COMMIT, *extra,
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def main():
    with tempfile.TemporaryDirectory(prefix="repack-contract-") as temporary:
        root = Path(temporary)
        paths = fixture(root / "source")
        tools = root / "bin"
        tools.mkdir()
        (tools / "modinfo").write_text(
            "#!/bin/sh\n[ \"$2\" = version ] && echo '" + NVIDIA + "' || echo '" + KERNEL + " SMP'\n")
        (tools / "readelf").write_text(
            "#!/bin/sh\necho 'Machine: Advanced Micro Devices X86-64'\n")
        for tool in ("modinfo", "readelf"):
            (tools / tool).chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{tools}:{env['PATH']}"
        output = root / "output"

        dry = run(paths, output, env, "--dry-run")
        assert dry.returncode == 0, dry.stderr
        plan = json.loads(dry.stdout)
        assert plan["createOnly"] is True
        assert plan["modulePayloadsByteIdentical"] is True
        assert not output.exists()

        first = run(paths, output, env)
        assert first.returncode == 0, first.stderr
        first_plan = json.loads(first.stdout)
        first_archive = output / first_plan["output"]["archive"]
        first_bytes = first_archive.read_bytes()
        second_output = root / "output-second"
        second = run(paths, second_output, env)
        assert second.returncode == 0, second.stderr
        second_plan = json.loads(second.stdout)
        assert (second_output / second_plan["output"]["archive"]).read_bytes() == first_bytes
        assert run(paths, output, env).returncode != 0
        publish = subprocess.run([
            str(PUBLISH), "--dry-run", "--archive", str(first_archive),
            "--checksum", str(output / (first_archive.name + ".sha256")),
            "--build-info", str(output / first_plan["output"]["buildInfo"]),
            "--provenance", str(output / first_plan["output"]["provenance"]),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert publish.returncode == 0, publish.stderr
        assert json.loads(publish.stdout)["tag"].endswith("-modules-zstd-r1")

        paths[1].write_text("0" * 64 + f"  {paths[0].name}\n")
        assert run(paths, root / "bad", env, "--dry-run").returncode != 0


if __name__ == "__main__":
    main()
