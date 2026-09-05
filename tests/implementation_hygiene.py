#!/usr/bin/env python3
"""Reject stale mode labels and orphaned shared shell globals."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tracked = subprocess.check_output(
    ["git", "-C", str(ROOT), "ls-files"], text=True
).splitlines()
implementation = [
    name for name in tracked
    if (name.endswith(".sh") and not name.startswith("tests/"))
    or name.startswith(".github/workflows/")
]
sources = {name: (ROOT / name).read_text(encoding="utf-8") for name in implementation}
combined_shell = "\n".join(
    source for name, source in sources.items() if name.endswith(".sh")
)

assignments = set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", combined_shell))
for variable in sorted(assignments):
    references = re.findall(
        rf"(?<![A-Z0-9_]){re.escape(variable)}(?![A-Z0-9_])", combined_shell
    )
    assert len(references) > 1, f"orphaned implementation global: {variable}"

for name, source in sources.items():
    lowered = source.lower()
    for stale in (
        "--driver", "driver mode", "explicit:", "driver_spec",
        "pristine-upstream", "upstream-control", "pristine upstream",
    ):
        assert stale not in lowered, f"{name}: stale mode terminology {stale!r}"

print("implementation hygiene checks passed")
