#!/usr/bin/env python3
"""Verify structured diagnostics when the measurement helper cannot launch."""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import validate_install_inputs as validator  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="measurement-launcher-") as temporary:
        root = Path(temporary)
        archive = root / "modules.tar.gz"
        package = root / "userspace.pkg.tar.zst"
        archive.write_bytes(b"archive")
        package.write_bytes(b"package")
        args = SimpleNamespace(archive=archive)
        permission_error = PermissionError(
            13, "Permission denied", "/sensitive/helper/location"
        )
        environment = os.environ.copy()
        environment.pop("PROJECT_TEST_MODE", None)
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(validator.os, "geteuid", return_value=0), \
                mock.patch.object(validator.subprocess, "run",
                                  side_effect=permission_error) as launched:
            try:
                validator.measured_btrfs_payload(args, [package], 1024)
            except validator.ValidationFailure as failure:
                assert failure.reason == "compression_measurement_launcher_failed"
                detail = failure.details["measurementFailure"]
                assert detail["phase"] == "launcher"
                assert detail["command"] == "measurement-helper"
                assert detail["exitStatus"] is None
                assert "Permission denied" in detail["stderr"]
                assert "/sensitive/" not in detail["stderr"]
            else:
                raise AssertionError("measurement helper launch failure was accepted")
        invoked = launched.call_args.args[0]
        assert invoked[0] == sys.executable
        assert Path(invoked[1]).name == "measure_btrfs_payload.py"


if __name__ == "__main__":
    main()
