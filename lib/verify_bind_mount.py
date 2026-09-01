#!/usr/bin/env python3
"""Verify that an exact mountpoint is bound from the claimed source topology."""

import argparse
import json
import os
import posixpath
import re
import subprocess
from pathlib import Path


FIELDS = "SOURCE,TARGET,FSTYPE,MAJ:MIN,FSROOT"
SOURCE_ROOT_SUFFIX = re.compile(r"\[[^\[\]]+\]$")


def mount_record(selector, path):
    completed = subprocess.run(
        ["findmnt", "--json", "-n", selector, str(path), "-o", FIELDS],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10,
    )
    if completed.returncode != 0:
        raise ValueError("mount identity is unavailable")
    document = json.loads(completed.stdout)
    records = document.get("filesystems")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("mount identity is ambiguous")
    record = records[0]
    required = ("source", "target", "fstype", "maj:min", "fsroot")
    if any(not isinstance(record.get(field), str) or not record[field]
           for field in required):
        raise ValueError("mount identity is incomplete")
    return record


def expected_fsroot(source_path, source):
    mount_target = os.path.realpath(source["target"])
    resolved_source = os.path.realpath(source_path)
    relative = os.path.relpath(resolved_source, mount_target)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise ValueError("source escapes its reported mount")
    suffix = "" if relative == "." else relative.replace(os.sep, "/")
    return posixpath.normpath(posixpath.join(source["fsroot"], suffix))


def base_source(value):
    return SOURCE_ROOT_SUFFIX.sub("", value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    options = parser.parse_args()
    try:
        source = mount_record("-T", options.source)
        target = mount_record("-M", options.target)
        expected_root = expected_fsroot(options.source, source)
        exact_target = os.path.realpath(options.target)
        valid = (
            os.path.realpath(target["target"]) == exact_target
            and base_source(target["source"]) == base_source(source["source"])
            and target["fstype"] == source["fstype"]
            and target["maj:min"] == source["maj:min"]
            and posixpath.normpath(target["fsroot"]) == expected_root
        )
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError):
        valid = False
    raise SystemExit(0 if valid else 1)


if __name__ == "__main__":
    main()
