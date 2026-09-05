#!/usr/bin/env python3
"""Freeze build.sh as the sole owner of build-environment preparation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = (ROOT / "bootstrap/build.sh").read_text(encoding="utf-8")
CALLERS = {
    path.name: path.read_text(encoding="utf-8")
    for path in (
        ROOT / "bootstrap/compile.sh",
        ROOT / "bootstrap/install_upstream.sh",
    )
}
SETUP_CALL = '"${SCRIPT_DIR}/setup_build_env.sh"'
BUILD_CALL = '"${SCRIPT_DIR}/build.sh"'

assert BUILD.count(SETUP_CALL) == 1, "build.sh must invoke setup_build_env.sh exactly once"
assert BUILD.index(SETUP_CALL) < BUILD.index("need_cmd podman"), (
    "build.sh must prepare the environment before requiring Podman"
)
for name, source in CALLERS.items():
    assert SETUP_CALL not in source, f"{name} must delegate environment preparation to build.sh"
    assert source.count(BUILD_CALL) == 1, f"{name} must invoke build.sh exactly once"

print("build environment ownership checks passed")
