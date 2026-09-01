#!/usr/bin/env python3
"""Symlink-safe atomic output helpers for machine-readable contracts."""

import os
import tempfile
from pathlib import Path


def _staged_file(path, payload, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def atomic_write_bytes(path, payload, mode=0o644):
    staged = _staged_file(path, payload, mode)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def atomic_create_bytes(path, payload, mode=0o644):
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    staged = _staged_file(path, payload, mode)
    try:
        os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)
