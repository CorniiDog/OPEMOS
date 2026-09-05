#!/usr/bin/env python3
"""Pre-trust online installer failure and partial-tree cleanup tests."""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "bootstrap/online_install.sh"
REVISION = "a" * 40


def run_case(root: Path, mode: str) -> None:
    home = root / f"home with spaces {mode}"
    fake_bin = root / f"bin-{mode}"
    log = root / f"git-{mode}.log"
    fake_bin.mkdir()
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/bash\nset -eu\n"
        "printf '%s\\n' \"$*\" >> \"$MOCK_GIT_LOG\"\n"
        "if [[ \"$MOCK_GIT_MODE\" == clone-fail ]]; then exit 81; fi\n"
        "if [[ \"${1:-}\" == clone ]]; then mkdir -p \"${*: -1}\"; exit 0; fi\n"
        "if [[ \"${1:-}\" == -C && \"${3:-}\" == fetch ]]; then exit 82; fi\n"
        "exit 83\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SUPPORT_REVISION": REVISION,
        "MOCK_GIT_MODE": mode,
        "MOCK_GIT_LOG": str(log),
    }
    completed = subprocess.run(
        [str(ENTRYPOINT), "--yes"], cwd="/", env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    expected = 81 if mode == "clone-fail" else 82
    assert completed.returncode == expected, (completed.returncode, completed.stderr)
    cache = home / ".cache/open-gpu-kernel-modules-steamos-support"
    assert cache.is_dir()
    assert not list(cache.glob("online-install.*"))
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == (1 if mode == "clone-fail" else 2)
    assert calls[0].startswith("clone --quiet --depth 1 ")
    if mode == "fetch-fail":
        assert " fetch --quiet --depth 1 origin " in calls[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="online-bootstrap-") as name:
        root = Path(name)
        run_case(root, "clone-fail")
        run_case(root, "fetch-fail")


if __name__ == "__main__":
    main()
