#!/usr/bin/env python3
"""Atomic, bounded delayed-network recovery transaction state."""

import argparse
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PHASES = {
    "offline_waiting", "retry_scheduled", "downloading", "verifying",
    "rebuilding", "installing", "restored", "cancelled", "failed",
}
KERNEL = re.compile(r"^[A-Za-z0-9._+\-]{1,192}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def load(path):
    if not path.exists():
        return None
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise ValueError("transaction state is unsafe or excessive")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or document.get("phase") not in PHASES:
        raise ValueError("transaction state is malformed")
    target = document.get("target", {})
    if not KERNEL.fullmatch(target.get("kernelVersion", "")):
        raise ValueError("transaction kernel identity is malformed")
    if not VERSION.fullmatch(target.get("nvidiaVersion", "")):
        raise ValueError("transaction NVIDIA identity is malformed")
    if not COMMIT.fullmatch(document.get("supportRevision", "")):
        raise ValueError("transaction support identity is malformed")
    return document


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("transaction state is excessive")
    fd, temporary = tempfile.mkstemp(prefix=".transaction.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("show", "begin", "set", "cancel"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--kernel")
    parser.add_argument("--nvidia")
    parser.add_argument("--support-revision")
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.operation == "show":
        document = load(args.state)
        print(json.dumps(document or {"schemaVersion": 1, "phase": "restored", "active": False}, sort_keys=True, separators=(",", ":")))
        return
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if args.operation == "begin":
        if not (args.kernel and args.nvidia and args.support_revision and args.phase):
            raise SystemExit("begin requires exact target, support revision, and phase")
        document = {
            "schemaVersion": 1, "active": True, "automaticRetry": True,
            "phase": args.phase, "reason": args.reason[:128],
            "target": {"kernelVersion": args.kernel, "nvidiaVersion": args.nvidia},
            "supportRevision": args.support_revision, "attempt": 0,
            "createdAt": now, "updatedAt": now,
        }
    else:
        document = load(args.state)
        if document is None:
            raise SystemExit("no recovery transaction exists")
        if args.operation == "cancel":
            document.update({"active": False, "automaticRetry": False, "phase": "cancelled", "reason": "cancelled_by_user"})
        else:
            if not args.phase:
                raise SystemExit("set requires --phase")
            document["phase"] = args.phase
            document["reason"] = args.reason[:128]
            document["attempt"] += 1
            document["active"] = args.phase not in ("restored", "cancelled")
        document["updatedAt"] = now
    write(args.state, document)
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"recovery_transaction.py: {error}") from None
