#!/usr/bin/env python3
"""Freeze canonical mode names and behavior disclosures in mode-facing CLIs."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "bootstrap/setup_nvidia.sh",
    "bootstrap/install_upstream.sh",
    "bootstrap/online_setup_nvidia.sh",
)
outputs = {}
for relative in SCRIPTS:
    result = subprocess.run(
        [str(ROOT / relative), "--help"], cwd="/",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    assert result.returncode == 0, (relative, result.stdout, result.stderr)
    assert result.stderr == "", (relative, result.stderr)
    assert result.stdout.startswith("Usage: "), relative
    outputs[relative] = result.stdout

setup = outputs["bootstrap/setup_nvidia.sh"]
for term in ("certified", "development", "upstream-development", "Project fixes"):
    assert term in setup, f"setup help omits canonical behavior term: {term}"
for disclosure in (
    "NVIDIA userspace is installed at that exact version",
    "Kernel modules are",
    "not installed or replaced by this mode",
    "Project fixes are not applied",
):
    assert disclosure in setup, f"setup help omits behavior disclosure: {disclosure}"

upstream = outputs["bootstrap/install_upstream.sh"]
assert "upstream-development" in upstream
assert "Project patches are never applied" in upstream
assert "do not install modules" in upstream

all_help = "\n".join(outputs.values())
for stale in ("driver mode", "explicit mode", "pristine-upstream", "upstream-control"):
    assert stale not in all_help.lower(), f"stale mode term remains: {stale}"

print("CLI consistency checks passed")
