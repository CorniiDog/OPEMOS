#!/usr/bin/env python3
"""Enforce early Bash nounset mode for every tracked shell script."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_PREAMBLE_LINES = 8

def strict_preamble(text: str) -> bool:
    lines = text.splitlines()
    if not lines or lines[0] not in ("#!/usr/bin/env bash", "#!/bin/bash"):
        return False
    for line in lines[1:MAX_PREAMBLE_LINES]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped == "set -euo pipefail"
    return False

def main() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "*.sh"], cwd=ROOT, check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.splitlines()
    assert tracked
    rejected = []
    for relative in tracked:
        path = ROOT / relative
        if not strict_preamble(path.read_text(encoding="utf-8")):
            rejected.append(relative)
    assert not rejected, f"shell scripts lack early 'set -euo pipefail': {rejected}"

    assert strict_preamble("#!/usr/bin/env bash\n\n# comment\nset -euo pipefail\necho ok\n")
    for malformed in (
        "#!/usr/bin/env bash\nset -eo pipefail\n",
        "#!/usr/bin/env bash\necho early\nset -euo pipefail\n",
        "#!/bin/sh\nset -euo pipefail\n",
        "#!/usr/bin/env bash\n" + "\n" * MAX_PREAMBLE_LINES + "set -euo pipefail\n",
    ):
        assert not strict_preamble(malformed)

if __name__ == "__main__":
    main()
