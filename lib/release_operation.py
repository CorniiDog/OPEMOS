#!/usr/bin/env python3
"""Plan and reconcile one immutable, create-only OPEMOS release operation."""

import argparse
import hashlib
import json
import re
from pathlib import Path

MAX_DOCUMENT = 1024 * 1024
MAX_ASSETS = 16
MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024


def fail(message):
    raise SystemExit(message)


def strict_object(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DOCUMENT:
            fail("Release operation input is not a bounded regular file.")
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("Release operation input is not valid JSON.")


def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail("Release operation input contains a duplicate field.")
        value[key] = item
    return value


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def validate_plan(plan):
    required = {"schemaVersion", "status", "repository", "tag", "title", "notes", "targetCommit", "trust", "archiveSha256", "assets"}
    if not isinstance(plan, dict) or set(plan) != required or plan.get("schemaVersion") != 1 or plan.get("status") != "ready":
        fail("Publication plan is not the closed schema-1 ready plan.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", plan.get("repository", "")):
        fail("Publication repository is invalid.")
    if not re.fullmatch(r"[0-9a-f]{40}", plan.get("targetCommit", "")):
        fail("Publication target commit is invalid.")
    if not isinstance(plan.get("tag"), str) or not 0 < len(plan["tag"]) <= 200:
        fail("Publication tag is invalid.")
    paths = plan.get("assets")
    if not isinstance(paths, list) or not 4 <= len(paths) <= MAX_ASSETS:
        fail("Publication asset inventory is invalid.")
    records = []
    names = set()
    for raw in paths:
        if not isinstance(raw, str):
            fail("Publication asset path is invalid.")
        path = Path(raw)
        try:
            size = path.stat().st_size
        except OSError:
            fail("Publication asset is unreadable.")
        if path.is_symlink() or not path.is_file() or not 0 < size <= MAX_ASSET_BYTES:
            fail("Publication asset is not a bounded regular file.")
        if path.name in names or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}", path.name):
            fail("Publication asset name is duplicate or invalid.")
        names.add(path.name)
        records.append({"name": path.name, "sha256": digest(path), "bytes": size, "state": "pending"})
    identity = {"repository": plan["repository"], "tag": plan["tag"], "targetCommit": plan["targetCommit"], "assets": [{k: r[k] for k in ("name", "sha256", "bytes")} for r in records]}
    return identity, records


def operation(plan, attempt, observed=None):
    identity, assets = validate_plan(plan)
    operation_id = hashlib.sha256(canonical({"schemaVersion": 1, **identity})).hexdigest()
    lifecycle, decision, message = "planned", "create", "Exact release is absent and may be created after explicit authorization."
    if observed is not None:
        lifecycle = "reconciling"
        if not isinstance(observed, dict) or set(observed) != {"repository", "tag", "targetCommit", "assets"}:
            fail("Observed release inventory is malformed.")
        if observed["repository"] != identity["repository"] or observed["tag"] != identity["tag"]:
            fail("Observed release identity does not match the operation.")
        if observed["targetCommit"] != identity["targetCommit"]:
            decision, lifecycle, message = "conflict", "failed", "Existing tag targets a different commit."
            for asset in assets: asset["state"] = "conflict"
        else:
            remote = observed["assets"]
            if not isinstance(remote, list) or len(remote) > MAX_ASSETS:
                fail("Observed release asset inventory is malformed.")
            indexed = {}
            for item in remote:
                if not isinstance(item, dict) or set(item) != {"name", "sha256", "bytes"} or item.get("name") in indexed:
                    fail("Observed release asset inventory is malformed.")
                indexed[item["name"]] = item
            expected_names = {item["name"] for item in assets}
            if set(indexed) - expected_names:
                decision, lifecycle, message = "conflict", "failed", "Existing release contains an unexpected asset."
            else:
                conflict = False
                missing = False
                for asset in assets:
                    item = indexed.get(asset["name"])
                    if item is None:
                        asset["state"] = "missing"; missing = True
                    elif item.get("sha256") != asset["sha256"] or item.get("bytes") != asset["bytes"]:
                        asset["state"] = "conflict"; conflict = True
                    else:
                        asset["state"] = "present"
                if conflict:
                    decision, lifecycle, message = "conflict", "failed", "Existing asset identity conflicts with the immutable plan."
                elif missing:
                    decision, message = "retry-missing", "Only missing assets may be uploaded; existing assets are immutable."
                else:
                    decision, lifecycle, message = "already-complete", "succeeded", "Remote release exactly matches the immutable plan."
    return {"schemaVersion": 1, "operationId": operation_id, **{k: identity[k] for k in ("repository", "tag", "targetCommit")}, "attempt": attempt, "lifecycle": lifecycle, "decision": decision, "assets": assets, "message": message}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--observed", type=Path)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.attempt <= 1000:
        fail("Release operation attempt is out of bounds.")
    plan = strict_object(args.plan)
    observed = strict_object(args.observed) if args.observed else None
    print(json.dumps(operation(plan, args.attempt, observed), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
