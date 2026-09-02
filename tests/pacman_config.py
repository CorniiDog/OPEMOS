#!/usr/bin/env python3
"""Contract tests for the measured-admission pacman configuration."""

import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib" / "prepare_pacman_config.py"


def run(source, output, *options):
    return subprocess.run(
        [sys.executable, str(HELPER), "--source", str(source), "--output", str(output),
         *options],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="pacman-config-") as temporary:
        root = Path(temporary)
        source = root / "pacman.conf"
        output = root / "transaction.conf"
        source.write_text(
            "# fixture\n[options]\nArchitecture = auto\nCheckSpace\n"
            "SigLevel = Required DatabaseOptional\nLocalFileSigLevel = Required\n\n"
            "[core]\nInclude = /etc/pacman.d/mirrorlist\n",
            encoding="utf-8",
        )
        completed = run(source, output)
        assert completed.returncode == 0, completed.stderr
        result = output.read_text(encoding="utf-8")
        assert result.startswith("[options]\n")
        assert "CheckSpace disabled after measured Btrfs admission" in result
        assert not any(
            line.strip() == "CheckSpace" for line in result.splitlines()
        )
        assert "SigLevel = Required DatabaseOptional" in result
        assert "LocalFileSigLevel = Required" in result
        assert "[core]" not in result and "mirrorlist" not in result
        assert stat.S_IMODE(output.stat().st_mode) == 0o600

        gaming = root / "gaming.conf"
        completed = run(
            source, gaming, "--check-space-policy", "preserve",
            "--local-file-policy", "validated-derived",
        )
        assert completed.returncode == 0, completed.stderr
        gaming_text = gaming.read_text(encoding="utf-8")
        assert "\nCheckSpace\n" in gaming_text
        assert "LocalFileSigLevel = Never" in gaming_text
        assert "SigLevel = Required DatabaseOptional" in gaming_text

        for name, contents in (
            ("missing", "[options]\nSigLevel = Required\n"),
            ("duplicate", "[options]\nCheckSpace\nCheckSpace\n"),
            ("ambiguous", "[options]\nCheckSpace = true\n"),
            ("include", "[options]\nCheckSpace\nInclude = options.conf\n"),
            ("sections", "[options]\nCheckSpace\n[options]\n"),
        ):
            invalid = root / f"{name}.conf"
            rejected_output = root / f"{name}.out"
            invalid.write_text(contents, encoding="utf-8")
            rejected = run(invalid, rejected_output)
            assert rejected.returncode != 0, name
            assert not rejected_output.exists(), name

        existing = root / "existing.conf"
        existing.write_text("keep\n", encoding="utf-8")
        refused = run(source, existing)
        assert refused.returncode != 0
        assert existing.read_text(encoding="utf-8") == "keep\n"


if __name__ == "__main__":
    main()
