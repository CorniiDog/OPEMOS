#!/usr/bin/env python3
"""Freeze setup_nvidia prerequisite inventory and pre-use ordering."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bootstrap/setup_nvidia.sh"
RESOLUTION = ("curl", "python3", "awk", "sort", "tar", "grep", "tail", "mkdir", "rm")
MUTATION = ("sudo", "pacman", "ldconfig", "modinfo", "cp", "install", "sed", "tee")

def main():
    text = SCRIPT.read_text(encoding="utf-8")
    declared = tuple(re.findall(r"(?m)^need_cmd ([a-z0-9-]+)$", text))
    assert declared == RESOLUTION + MUTATION
    temp_index = text.index('TMP="$(project_mktemp_dir setup-nvidia)"')
    for command in RESOLUTION:
        assert text.index(f"need_cmd {command}") < temp_index
    privilege_index = text.index('log "Requesting administrator privileges..."')
    for command in MUTATION:
        assert text.index(f"need_cmd {command}") < privilege_index
    assert 'command -v steamos-readonly' in text
    assert 'command -v update-grub' in text
    assert 'systemctl cat "$service"' in text

    malformed = "need_cmd curl\nTMP=work\nneed_cmd grep\n"
    assert malformed.index("need_cmd grep") > malformed.index("TMP=work")

if __name__ == "__main__":
    main()
