#!/usr/bin/env python3
"""Freeze build.sh as the sole owner of build-environment preparation."""

from pathlib import Path
import os
import subprocess
import tempfile

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

setup = (ROOT / "bootstrap/setup_build_env.sh").read_text(encoding="utf-8")
install_guard = '[[ "$INSTALL_PODMAN" == "1" ]]'
pacman = 'sudo pacman -Sy --needed --noconfirm podman'
assert setup.count("--install-podman") >= 3, "setup must document, parse, and report the opt-in"
assert setup.count(install_guard) == 1, "host package mutation must have one explicit opt-in guard"
assert setup.index(install_guard) < setup.index("need_cmd sudo") < setup.index(pacman), (
    "missing-Podman refusal must happen before sudo and pacman"
)
assert CALLERS["compile.sh"].count(BUILD_CALL) == 1
assert BUILD.count(SETUP_CALL) == 1 and SETUP_CALL + " --install-podman" not in BUILD, (
    "ordinary build dispatch must never opt into host mutation"
)

with tempfile.TemporaryDirectory(prefix="podman-opt-in-") as temporary:
    fixture = Path(temporary)
    system = fixture / "system/etc"
    system.mkdir(parents=True)
    (system / "os-release").write_text(
        'ID=steamos\nNAME="SteamOS"\nVERSION_ID="3.8.14"\n', encoding="utf-8"
    )
    bash_env = fixture / "bash-env"
    bash_env.write_text(
        'command() { if [[ "$1" == -v && "$2" == podman ]]; then return 1; fi; '
        'builtin command "$@"; }\n', encoding="utf-8"
    )
    environment = {
        **os.environ,
        "BASH_ENV": str(bash_env),
        "PROJECT_TEST_MODE": "1",
        "PROJECT_TEST_ROOT": str(fixture / "system"),
    }
    missing = subprocess.run(
        ["bash", str(ROOT / "bootstrap/setup_build_env.sh")],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "Podman is required. Review and run:" in missing.stderr
    assert "--install-podman" in missing.stderr
    assert "Installing Podman" not in missing.stdout + missing.stderr

print("build environment ownership checks passed")
