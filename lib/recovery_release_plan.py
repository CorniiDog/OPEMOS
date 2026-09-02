#!/usr/bin/env python3
"""Create and enforce one immutable exact-release plan across repair retries."""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

TOKEN = re.compile(r"^[A-Za-z0-9._+\-]{1,256}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def read(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise ValueError("release plan is unsafe or excessive")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = ("steamosVersion", "nvidiaVersion", "kernelTag", "releaseTag", "assetName")
    if value.get("schemaVersion") != 1 or any(not TOKEN.fullmatch(value.get(key, "")) for key in required):
        raise ValueError("release plan is malformed")
    digest = value.get("archiveSha256")
    if digest is not None and not SHA.fullmatch(digest):
        raise ValueError("release plan archive identity is malformed")
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".release-plan.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path); temporary = None
    finally:
        if temporary: os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("create", "show", "bind-archive"))
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--steamos")
    parser.add_argument("--nvidia")
    parser.add_argument("--kernel-tag")
    parser.add_argument("--release-tag")
    parser.add_argument("--asset-name")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.operation == "create":
        if args.plan.exists():
            raise ValueError("release plan already exists")
        value = {"schemaVersion": 1, "steamosVersion": args.steamos,
                 "nvidiaVersion": args.nvidia, "kernelTag": args.kernel_tag,
                 "releaseTag": args.release_tag, "assetName": args.asset_name,
                 "archiveSha256": None}
        if any(not TOKEN.fullmatch(value[key] or "") for key in
               ("steamosVersion", "nvidiaVersion", "kernelTag", "releaseTag", "assetName")):
            raise ValueError("release plan identity is malformed")
        write(args.plan, value)
    elif args.operation == "bind-archive":
        value = read(args.plan)
        if not args.archive or args.archive.is_symlink() or not args.archive.is_file():
            raise ValueError("release archive is unsafe")
        size = args.archive.stat().st_size
        if size <= 0 or size > 2 * 1024 * 1024 * 1024:
            raise ValueError("release archive size is outside policy")
        hasher = hashlib.sha256()
        with args.archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if value["archiveSha256"] not in (None, digest):
            raise ValueError("release archive changed across repair attempts")
        value["archiveSha256"] = digest
        write(args.plan, value)
    else:
        value = read(args.plan)
    print("\t".join(str(value[key] or "") for key in
                    ("steamosVersion", "nvidiaVersion", "kernelTag", "releaseTag", "assetName", "archiveSha256")))


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"recovery_release_plan.py: {error}") from None
