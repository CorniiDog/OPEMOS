#!/usr/bin/env python3
"""Cross-platform interstitial preview launcher contract checks."""

import json
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "test_update_macos.sh"
LINUX = ROOT / "test_update_linux.sh"
WINDOWS = ROOT / "test_update_windows.ps1"
DOC = ROOT / "docs" / "interstitial.md"
README = ROOT / "README.md"

for path in (MACOS, LINUX, WINDOWS):
    assert path.is_file(), path

macos = MACOS.read_text(encoding="utf-8")
linux = LINUX.read_text(encoding="utf-8")
windows = WINDOWS.read_text(encoding="utf-8")
for shell in (macos, linux):
    assert "--duration 5..600" in shell
    assert "--no-open|--headless" in shell
    assert '127.0.0.1' in shell
    assert "interstitial_demo_server.py" in shell
    assert "Demo health contract failed." in shell
    assert "CHECKING EXACT NVIDIA SUPPORT" in shell
    assert "GENERATING INITRAMFS" in shell
    assert "NVIDIA GRAPHICS READY" in shell
    assert "rm -rf \"$RUNTIME\"" in shell

assert "xdg-open" in linux and "gio" in linux
assert '"platform":"linux"' in linux
assert "no driver update performed" in linux
assert '"platform":"macos"' in macos
assert "no macOS driver update performed" in macos

for required in (
    "[Net.IPAddress]::Loopback",
    "[ValidateRange(5, 600)]",
    "Invoke-WebRequest",
    "Start-Process $url",
    "$quotedScript",
    "$quotedPortFile",
    "Stop-Process -Id $serverProcess.Id",
    "Remove-Item -LiteralPath $runtime",
    "CHECKING EXACT NVIDIA SUPPORT",
    "GENERATING INITRAMFS",
    "NVIDIA GRAPHICS READY",
    'platform = "windows"',
    "no Windows driver update performed",
):
    assert required in windows
for forbidden in ("start-process powershell -verb runas", "qemu-system", "diskpart", "pnputil"):
    assert forbidden not in windows.lower()

commands = (
    "./test_update_macos.sh --no-open --duration 5",
    "./test_update_linux.sh --no-open --duration 5",
    ".\\test_update_windows.ps1 -NoOpen -Duration 5",
)
for document in (DOC, README):
    text = document.read_text(encoding="utf-8")
    for command in commands:
        assert command in text

if platform.system() == "Linux":
    completed = subprocess.run(
        [str(LINUX), "--no-open", "--duration", "5"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence == {
        "schemaVersion": 1,
        "status": "passed",
        "platform": "linux",
        "linuxBrowserSimulation": "passed",
        "scope": "Linux/SteamOS interstitial preview; no driver update performed",
    }
    assert subprocess.run(
        [str(LINUX), "--no-open", "--duration", "4"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    ).returncode == 2

pwsh = shutil.which("pwsh")
if pwsh:
    syntax = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e)|Out-Null;if($e.Count){exit 1}",
            str(WINDOWS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert syntax.returncode == 0, syntax.stderr.decode(errors="replace")

print("Cross-platform interstitial preview launcher checks passed.")
