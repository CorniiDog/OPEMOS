#!/usr/bin/env python3
"""Enforce shared cache-root creation after common.sh is available."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = (ROOT / "lib/common.sh").read_text(encoding="utf-8")
assert 'root="$(project_cache_root)"' in COMMON
assert COMMON.count('mkdir -p "$root"') == 2

# These network bootstraps must allocate before common.sh exists. All scripts
# that source common.sh use its cache helpers instead of recreating the root.
prebootstrap = {
    "compile_online.sh", "online_commit.sh", "online_dev.sh",
    "online_install.sh", "online_setup_nvidia.sh",
}
for script in (ROOT / "bootstrap").glob("*.sh"):
    source = script.read_text(encoding="utf-8")
    hardcoded = 'mkdir -p "${HOME}/.cache/open-gpu-kernel-modules-steamos-support"' in source
    if script.name in prebootstrap:
        assert hardcoded, script
    elif 'source "${SUPPORT_ROOT}/lib/common.sh"' in source:
        assert not hardcoded, script
        assert 'mkdir -p "${HOME}/.cache/${PROJECT_ID}"' not in source, script

print("Cache-root contract checks passed.")
