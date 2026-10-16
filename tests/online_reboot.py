#!/usr/bin/env python3
"""Unauthenticated local input cannot reach installation or a reboot prompt.

Positive two-boot behavior uses the actual ordinary installer and installed
health consumer in tests/vm/ordinary_online_install_acceptance.py. The historical
fixture replaced installer/guardian with successful stubs and is not evidence of
that workflow under private product authentication.
"""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "bootstrap/online_install.sh"


def main():
    with tempfile.TemporaryDirectory(prefix="online-reboot-refusal-") as name:
        root = Path(name)
        product = root / "nvidia-open-unauthenticated.tar.gz"
        product.write_bytes(b"not an authenticated product")
        before = product.read_bytes()
        home = root / "home"
        home.mkdir()
        for arguments, reason in (
            (["--local", str(product)], "required together"),
            (["--local", str(product), "--local-sha256", "not-a-hash"], "required together"),
            (["--local", str(product), "--local-sha256", "0" * 64,
              "--build-as-fallback"], "cannot be combined"),
        ):
            result = subprocess.run(
                ["bash", str(ENTRYPOINT), *arguments],
                cwd="/", env={**os.environ, "HOME": str(home)},
                input="y\n", text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=20,
            )
            assert result.returncode != 0, (result.stdout, result.stderr)
            assert reason.lower() in (result.stdout + result.stderr).lower(), (
                result.stdout, result.stderr)
            assert "Restart the system" not in result.stdout + result.stderr
            assert "Restarting system" not in result.stdout + result.stderr
            assert not list(home.iterdir()), "refusal must precede checkout/cache effects"
            assert product.read_bytes() == before


if __name__ == "__main__":
    main()
