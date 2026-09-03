#!/usr/bin/env python3
"""Write the bounded root-owned OPEMOS boot-interstitial progress contract."""

import argparse
import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path

MAX_BYTES = 64 * 1024
MAX_SEQUENCE = 1_000_000_000
PHASES = {
    "starting", "inspecting", "waiting_for_network", "downloading", "verifying",
    "building", "installing_userspace", "installing_modules", "updating_boot",
    "generating_initramfs", "cleaning_up", "complete", "recovery_required",
}
TERMINAL = {"succeeded", "failed"}
REQUIRED_FIELDS = {"schemaVersion", "sequence", "status", "phase", "completed", "total"}
STEP_FIELDS = {"stepCompleted", "stepTotal"}


def confined_regular(path: Path, *, allow_missing: bool) -> None:
    if not path.is_absolute():
        raise ValueError("progress path must be absolute")
    parent = path.parent
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise ValueError("progress directory is unavailable or unsafe")
    info = parent.stat()
    if os.geteuid() == 0 and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
        raise ValueError("progress directory ownership or permissions are unsafe")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError("progress document is unsafe or excessive")
    if os.geteuid() == 0 and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
        raise ValueError("progress document ownership or permissions are unsafe")


def load(path: Path):
    confined_regular(path, allow_missing=False)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        payload = os.read(descriptor, MAX_BYTES + 1)
        if len(payload) != info.st_size or len(payload) > MAX_BYTES:
            raise ValueError("progress document changed or exceeded its bound")
    finally:
        os.close(descriptor)
    value = json.loads(payload)
    validate(value)
    return value


def validate(value):
    fields = set(value)
    if not REQUIRED_FIELDS <= fields or fields - REQUIRED_FIELDS - STEP_FIELDS:
        raise ValueError("progress document fields are not canonical")
    if ("stepCompleted" in fields) != ("stepTotal" in fields):
        raise ValueError("step progress fields must be supplied together")
    value.setdefault("stepCompleted", None)
    value.setdefault("stepTotal", None)
    if value["schemaVersion"] != 1 or value["status"] not in {"working", *TERMINAL}:
        raise ValueError("progress document identity is unsupported")
    if value["phase"] not in PHASES:
        raise ValueError("progress phase is unsupported")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= MAX_SEQUENCE:
        raise ValueError("progress sequence is invalid")
    completed, total = value["completed"], value["total"]
    validate_counters(completed, total, "progress")
    validate_counters(value["stepCompleted"], value["stepTotal"], "step progress")
    if value["status"] == "succeeded" and (
        value["phase"] != "complete" or completed is None or completed != total
    ):
        raise ValueError("successful progress is incomplete")
    if value["status"] == "failed" and value["phase"] != "recovery_required":
        raise ValueError("failed progress does not require recovery")
    if value["status"] == "working" and value["phase"] in {"complete", "recovery_required"}:
        raise ValueError("working progress is terminal")


def validate_counters(completed, total, label):
    if (completed is None) != (total is None):
        raise ValueError(f"{label} counters are inconsistent")
    if completed is None:
        return
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (completed, total)):
        raise ValueError(f"{label} counters are invalid")
    if total <= 0 or completed < 0 or completed > total or total > MAX_SEQUENCE:
        raise ValueError(f"{label} counters are invalid")


def atomic_write(path: Path, value) -> None:
    validate(value)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("progress document exceeded its bound")
    descriptor, temporary = tempfile.mkstemp(prefix=".progress.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary:
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("reset", "set", "succeed", "fail", "show"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--completed", type=int)
    parser.add_argument("--total", type=int)
    parser.add_argument("--step-completed", type=int)
    parser.add_argument("--step-total", type=int)
    args = parser.parse_args()
    args.state.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    confined_regular(args.state, allow_missing=True)
    lock_path = args.state.with_name("progress.lock")
    lock_descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if args.operation == "show":
            print(json.dumps(load(args.state), sort_keys=True, separators=(",", ":")))
            return
        if args.operation == "reset":
            if (args.phase not in (None, "starting") or args.completed is not None
                    or args.total is not None or args.step_completed is not None
                    or args.step_total is not None):
                raise ValueError("reset accepts no counters and only the starting phase")
            value = {"schemaVersion": 1, "sequence": 0, "status": "working", "phase": "starting",
                     "completed": None, "total": None, "stepCompleted": None, "stepTotal": None}
        else:
            value = load(args.state)
            if value["status"] in TERMINAL:
                raise ValueError("terminal progress cannot be changed without reset")
            if value["sequence"] >= MAX_SEQUENCE:
                raise ValueError("progress sequence is exhausted")
            value["sequence"] += 1
            if args.operation == "set":
                if not args.phase or args.phase in {"complete", "recovery_required"}:
                    raise ValueError("set requires a nonterminal phase")
                if (args.completed is None) != (args.total is None):
                    raise ValueError("progress counters must be supplied together")
                if (args.step_completed is None) != (args.step_total is None):
                    raise ValueError("step progress counters must be supplied together")
                validate_counters(args.completed, args.total, "progress")
                validate_counters(args.step_completed, args.step_total, "step progress")
                if args.completed is not None and value["completed"] is not None:
                    if args.completed * value["total"] < value["completed"] * args.total:
                        raise ValueError("progress completion regressed")
                if (args.phase == value["phase"] and args.step_completed is not None
                        and value["stepCompleted"] is not None
                        and args.step_completed * value["stepTotal"]
                        < value["stepCompleted"] * args.step_total):
                    raise ValueError("step progress completion regressed")
                value.update(status="working", phase=args.phase,
                             completed=args.completed, total=args.total,
                             stepCompleted=args.step_completed, stepTotal=args.step_total)
            elif args.operation == "succeed":
                if (args.phase or args.completed is not None or args.total is not None
                        or args.step_completed is not None or args.step_total is not None):
                    raise ValueError("succeed accepts no phase or counters")
                value.update(status="succeeded", phase="complete", completed=1, total=1,
                             stepCompleted=1, stepTotal=1)
            else:
                if (args.phase or args.completed is not None or args.total is not None
                        or args.step_completed is not None or args.step_total is not None):
                    raise ValueError("fail accepts no phase or counters")
                value.update(status="failed", phase="recovery_required", completed=None, total=None,
                             stepCompleted=None, stepTotal=None)
        atomic_write(args.state, value)
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    finally:
        os.close(lock_descriptor)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"interstitial_progress.py: {error}") from None
