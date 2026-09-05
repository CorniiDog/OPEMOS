#!/usr/bin/env python3
"""Regression coverage for bounded credential/path-safe diagnostics."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from diagnostic_safety import sanitize_diagnostic
from measure_btrfs_payload import sanitize_stderr
from validate_install_inputs import sanitized_measurement_stderr
assert sanitize_diagnostic(None, 32) is None
assert sanitize_diagnostic(b"", 32) is None
assert sanitize_diagnostic("device busy", 32) == "device busy"
source = ("Authorization: Bearer bearer-secret authorization=Basic basic-secret "
          "password=hunter2 token=abc credential:xyz secret=value "
          "/home/alice/private/key https://host.invalid/path?token=url-secret")
clean = sanitize_diagnostic(source, 512)
for secret in ("bearer-secret", "basic-secret", "hunter2", "abc", "xyz", "value",
               "/home/alice", "host.invalid", "url-secret"):
    assert secret not in clean, (secret, clean)
assert clean.count("authorization=<redacted>") == 2
assert "password=<redacted>" in clean and "token=<redacted>" in clean
assert "<path>" in clean and "<url>" in clean
assert sanitize_diagnostic(b"bad\x00line\nnon-ascii:\xff", 64) == "bad line non-ascii:?"
assert sanitize_diagnostic("x" * 65, 64) == "x" * 64
for wrapper in (sanitize_stderr, sanitized_measurement_stderr):
    wrapped = wrapper(source)
    assert "bearer-secret" not in wrapped and "/home/alice" not in wrapped
    assert "authorization=<redacted>" in wrapped and "<path>" in wrapped
for invalid in (0, -1, True, "64"):
    try:
        sanitize_diagnostic("value", invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted invalid diagnostic bound: {invalid!r}")
print("diagnostic safety checks passed")
