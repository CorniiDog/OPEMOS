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
        "if [[ \"${1:-}\" == -C && \"${3:-}\" == fetch ]]; then "
        "[[ \"$MOCK_GIT_MODE\" != fetch-fail ]] || exit 82; exit 0; fi\n"
        "if [[ \"${1:-}\" == -C && \"${3:-}\" == checkout ]]; then exit 83; fi\n"
        "exit 84\n",
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
    expected = {"clone-fail": 81, "fetch-fail": 82, "checkout-fail": 83}[mode]
    assert completed.returncode == expected, (completed.returncode, completed.stderr)
    cache = home / ".cache/open-gpu-kernel-modules-steamos-support"
    assert cache.is_dir()
    assert not list(cache.glob("online-install.*"))
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == {"clone-fail": 1, "fetch-fail": 2, "checkout-fail": 3}[mode]
    assert calls[0].startswith("clone --quiet --depth 1 ")
    if mode != "clone-fail":
        assert " fetch --quiet --depth 1 origin " in calls[1]
    if mode == "checkout-fail":
        assert calls[2].endswith(f"checkout --quiet --detach {REVISION}")


def reject_revision(root: Path, revision: str, name: str) -> None:
    home = root / f"invalid-{name}"
    fake_bin = root / f"invalid-bin-{name}"
    marker = root / f"git-called-{name}"
    fake_bin.mkdir()
    git = fake_bin / "git"
    git.write_text(f"#!/bin/sh\ntouch {str(marker)!r}\nexit 99\n", encoding="utf-8")
    git.chmod(0o755)
    completed = subprocess.run(
        [str(ENTRYPOINT), "--yes"], cwd="/",
        env={**os.environ, "HOME": str(home),
             "PATH": f"{fake_bin}:{os.environ['PATH']}",
             "SUPPORT_REVISION": revision},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert completed.returncode == 1
    assert completed.stderr == "Could not resolve support revision.\n"
    assert not marker.exists()
    assert not home.exists()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="online-bootstrap-") as name:
        root = Path(name)
        run_case(root, "clone-fail")
        run_case(root, "fetch-fail")
        run_case(root, "checkout-fail")
        reject_revision(root, "a" * 39, "short")
        reject_revision(root, "a" * 41, "long")
        reject_revision(root, "g" * 40, "nonhex")
        reject_revision(root, "a" * 20 + " " + "a" * 19, "whitespace")


if __name__ == "__main__":
    main()
