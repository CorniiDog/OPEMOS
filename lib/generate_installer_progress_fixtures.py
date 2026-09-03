#!/usr/bin/env python3
"""Emit the canonical bounded installer-progress schema-1 fixture matrix."""

import json
import sys


PREFIX = "STEAMOS_NVIDIA_PROGRESS "
MAX_OUTPUT_BYTES = 512 * 1024
MAX_PROGRESS_BYTES = 16 * 1024 * 1024
MAX_PROGRESS_LINE = 4096


def record(attempt, phase, indeterminate, **fields):
    return {
        "schemaVersion": 1,
        "attempt": attempt,
        "phase": phase,
        "indeterminate": indeterminate,
        **fields,
    }


def line(value):
    return PREFIX + json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def stream(*records):
    return "".join(line(value) for value in records)


def accepted(name, value, count):
    return {
        "name": name,
        "expected": {"accepted": True, "progressRecords": count},
        "stream": value,
    }


def rejected(name, value):
    return {"name": name, "expected": {"accepted": False}, "stream": value}


def matrix():
    cases = [
        accepted(
            "indeterminate-heartbeats",
            stream(
                record(1, "initramfs", True),
                record(1, "initramfs", True),
                record(1, "initramfs", True),
            ),
            3,
        ),
        accepted(
            "monotonic-bytes",
            stream(
                record(1, "hashing", False, completed=0, total=8192, unit="bytes"),
                record(1, "hashing", False, completed=4096, total=8192, unit="bytes"),
                record(1, "hashing", False, completed=8192, total=8192, unit="bytes"),
            ),
            3,
        ),
        accepted(
            "monotonic-items",
            stream(
                record(1, "module_install", False, completed=0, total=5, unit="items"),
                record(1, "module_install", False, completed=2, total=5, unit="items"),
                record(1, "module_install", False, completed=5, total=5, unit="items"),
            ),
            3,
        ),
        accepted(
            "phase-transition-reset",
            stream(
                record(1, "userspace_install", False, completed=6, total=6, unit="items"),
                record(1, "module_install", False, completed=0, total=5, unit="items"),
                record(1, "module_install", False, completed=5, total=5, unit="items"),
            ),
            3,
        ),
        accepted(
            "attempt-advancement-reset",
            stream(
                record(1, "hashing", False, completed=8192, total=8192, unit="bytes"),
                record(2, "hashing", False, completed=0, total=4096, unit="bytes"),
                record(2, "hashing", False, completed=4096, total=4096, unit="bytes"),
            ),
            3,
        ),
        accepted(
            "unknown-additive-fields",
            stream(record(
                1, "storage", True,
                message="Human-readable additive wording is intentionally unfrozen.",
                futureMetadata={"bounded": True},
            )),
            1,
        ),
        accepted(
            "unknown-phase-token",
            stream(record(1, "future_phase", True)),
            1,
        ),
        accepted(
            "non-protocol-noise-ignored",
            "bounded tool output\n" + stream(record(1, "cleanup", True)),
            1,
        ),
        rejected(
            "attempt-regression",
            stream(record(2, "hashing", True), record(1, "cleanup", True)),
        ),
        rejected(
            "completed-regression",
            stream(
                record(1, "hashing", False, completed=2, total=3, unit="items"),
                record(1, "hashing", False, completed=1, total=3, unit="items"),
            ),
        ),
        rejected(
            "total-change",
            stream(
                record(1, "hashing", False, completed=1, total=2, unit="bytes"),
                record(1, "hashing", False, completed=2, total=3, unit="bytes"),
            ),
        ),
        rejected(
            "unit-change",
            stream(
                record(1, "hashing", False, completed=1, total=2, unit="bytes"),
                record(1, "hashing", False, completed=2, total=2, unit="items"),
            ),
        ),
        rejected(
            "determinate-fields-on-indeterminate",
            stream(record(1, "initramfs", True, completed=0, total=1, unit="items")),
        ),
        rejected(
            "missing-determinate-fields",
            stream(record(1, "hashing", False, completed=0)),
        ),
        rejected(
            "completed-exceeds-total",
            stream(record(1, "hashing", False, completed=2, total=1, unit="bytes")),
        ),
        rejected(
            "zero-total",
            stream(record(1, "hashing", False, completed=0, total=0, unit="bytes")),
        ),
        rejected(
            "unsupported-schema-version",
            stream({"schemaVersion": 2, "attempt": 1, "phase": "hashing", "indeterminate": True}),
        ),
        rejected(
            "invalid-phase-token",
            stream(record(1, "Invalid Phase", True)),
        ),
        rejected("malformed-json", PREFIX + '{"schemaVersion":1,\n'),
        rejected(
            "duplicate-json-key",
            PREFIX
            + '{"schemaVersion":1,"schemaVersion":1,"attempt":1,'
            + '"phase":"hashing","indeterminate":true}\n',
        ),
        rejected(
            "non-finite-json",
            PREFIX
            + '{"schemaVersion":1,"attempt":1,"phase":"hashing",'
            + '"indeterminate":true,"future":NaN}\n',
        ),
        rejected(
            "oversized-line",
            stream(record(1, "hashing", True, futurePadding="x" * MAX_PROGRESS_LINE)),
        ),
        {
            "name": "oversized-stream",
            "expected": {"accepted": False},
            "streamRecipe": {
                "kind": "repeat",
                "text": "bounded non-protocol output\n",
                "count": MAX_PROGRESS_BYTES // len("bounded non-protocol output\n") + 1,
            },
        },
        rejected("no-progress-records", "bounded non-protocol output\n"),
    ]
    return {
        "schemaVersion": 1,
        "kind": "opemos-installer-progress-compatibility-fixtures",
        "progressSchemaVersion": 1,
        "unfrozenFields": ["message"],
        "limits": {
            "maxLineBytes": MAX_PROGRESS_LINE,
            "maxStreamBytes": MAX_PROGRESS_BYTES,
        },
        "cases": cases,
    }


def main():
    payload = (json.dumps(matrix(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("installer-progress compatibility matrix exceeds its size limit")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
