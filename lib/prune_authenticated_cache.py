#!/usr/bin/env python3
"""Prune immutable authenticated-cache generations under exact safe limits."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import tempfile
from pathlib import Path

IDENTITY = re.compile(r"[0-9a-f]{64}")
MAX_SCAN_ENTRIES = 4096
MAX_GENERATION_FILES = 256


def fail(message):
    raise SystemExit(message)


def generation_size(path):
    total = 0
    files = 0
    for current, directories, names in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            value = current_path / name
            info = value.lstat()
            if not stat.S_ISDIR(info.st_mode) or value.is_symlink():
                fail(f"generation {path.name} contains an unsafe directory")
        for name in names:
            value = current_path / name
            info = value.lstat()
            files += 1
            if files > MAX_GENERATION_FILES or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                fail(f"generation {path.name} contains unsafe or excessive files")
            total += info.st_size
    return total


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def validate_generation(path):
    manifest = path / "manifest.json"
    data = manifest.read_bytes()
    if len(data) > 64 * 1024:
        fail("generation manifest exceeds its size limit")
    try:
        document = json.loads(data)
    except (UnicodeError, json.JSONDecodeError):
        fail("generation manifest is invalid")
    canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if data != canonical:
        fail("generation manifest is not canonical")
    if hashlib.sha256(data).hexdigest() != path.name:
        fail("generation manifest identity is inconsistent")
    records = []
    kind = document.get("kind")
    if kind == "detached-signature-artifact":
        records = [document.get("artifact"), document.get("signature")]
    elif kind == "authenticated-artifact-set":
        records = [document.get("policy"), document.get("provenance")]
        for artifact in document.get("artifacts", []):
            records.extend((artifact, {"path": artifact.get("signature"),
                                       "size": artifact.get("signatureSize"),
                                       "sha256": artifact.get("signatureSha256")}))
    else:
        fail("generation manifest kind is unsupported")
    expected = {"manifest.json"}
    for record in records:
        if not isinstance(record, dict):
            fail("generation manifest record is invalid")
        relative = record.get("path")
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or ".." in Path(relative).parts or relative in expected):
            fail("generation manifest path is unsafe or duplicated")
        expected.add(relative)
        value = path / relative
        info = value.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size != record.get("size")
                or file_digest(value) != record.get("sha256")):
            fail("generation payload does not match its manifest")
    actual = {str(value.relative_to(path)) for value in path.rglob("*") if value.is_file()}
    if actual != expected:
        fail("generation contains missing or unexpected files")


def active_leases(store):
    leases = store / ".leases"
    if not leases.exists():
        return set()
    if leases.is_symlink() or not leases.is_dir():
        fail("cache lease directory has an unsafe type")
    protected = set()
    for entry in leases.iterdir():
        info = entry.lstat()
        identity = entry.name.split(".", 1)[0]
        if not IDENTITY.fullmatch(identity) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail("cache lease entry has an unsafe type or identity")
        protected.add(identity)
    return protected


def current_generation(store):
    current = store / ".current"
    if not current.exists():
        return set()
    info = current.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65:
        fail("current cache-generation marker has an unsafe type")
    identity = current.read_text(encoding="ascii").strip()
    if not IDENTITY.fullmatch(identity):
        fail("current cache-generation marker is invalid")
    return {identity}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--max-count", required=True, type=int)
    parser.add_argument("--max-bytes", required=True, type=int)
    parser.add_argument("--protect", action="append", default=[])
    args = parser.parse_args()
    if args.max_count < 1 or args.max_bytes < 1 or any(not IDENTITY.fullmatch(x) for x in args.protect):
        fail("cache retention limits or protected identities are invalid")
    if args.store.is_symlink() or not args.store.is_dir():
        fail("cache store must be a real existing directory")
    moved = []
    interrupted = False
    def stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True
        raise InterruptedError
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with (args.store / ".import.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = list(args.store.iterdir())
        if len(entries) > MAX_SCAN_ENTRIES:
            fail("cache store exceeds its scan-entry limit")
        protected = set(args.protect) | active_leases(args.store) | current_generation(args.store)
        generations = []
        unsafe = []
        for entry in entries:
            if entry.name in (".import.lock", ".leases", ".current"):
                continue
            if not IDENTITY.fullmatch(entry.name):
                unsafe.append(entry)
                continue
            info = entry.lstat()
            if not stat.S_ISDIR(info.st_mode) or entry.is_symlink():
                if entry.name in protected:
                    fail("a protected cache generation has an unsafe type")
                unsafe.append(entry)
                continue
            try:
                size = generation_size(entry)
                validate_generation(entry)
            except (OSError, SystemExit):
                if entry.name in protected:
                    fail("a protected cache generation is corrupt")
                unsafe.append(entry)
                continue
            generations.append((entry, size, info.st_mtime_ns))
        missing = protected - {entry.name for entry, _, _ in generations}
        if missing:
            fail("a protected cache generation is missing")
        kept = [item for item in generations if item[0].name in protected]
        count, used = len(kept), sum(item[1] for item in kept)
        if count > args.max_count or used > args.max_bytes:
            fail("protected cache generations exceed configured retention limits")
        candidates = sorted((item for item in generations if item[0].name not in protected),
                            key=lambda item: (item[2], item[0].name), reverse=True)
        removed = list(unsafe)
        for item in candidates:
            if count + 1 <= args.max_count and used + item[1] <= args.max_bytes:
                kept.append(item)
                count += 1
                used += item[1]
            else:
                removed.append(item[0])
        trash = Path(tempfile.mkdtemp(prefix=".gc-trash-", dir=args.store))
        try:
            for entry in removed:
                target = trash / entry.name
                os.replace(entry, target)
                moved.append((target, entry))
            if interrupted:
                raise InterruptedError
            decisions = [{"cacheId": item[0].name, "decision": "keep", "bytes": item[1],
                          "protected": item[0].name in protected} for item in kept]
            decisions.extend({"cacheId": original.name, "decision": "remove", "bytes": None,
                              "protected": False} for _, original in moved)
            shutil.rmtree(trash)
            moved.clear()
        except BaseException:
            for source, destination in reversed(moved):
                if source.exists() or source.is_symlink():
                    os.replace(source, destination)
            shutil.rmtree(trash, ignore_errors=True)
            raise
    print(json.dumps({"schemaVersion": 1, "status": "pruned", "maxCount": args.max_count,
                      "maxBytes": args.max_bytes, "keptCount": count, "keptBytes": used,
                      "decisions": sorted(decisions, key=lambda item: item["cacheId"])},
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
