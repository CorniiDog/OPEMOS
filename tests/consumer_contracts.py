#!/usr/bin/env python3
"""Canonical cross-frontend schema and immutable bundle-manifest tests."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from installer_bundle_manifest import (  # noqa: E402
    ContractError,
    FILES,
    build_manifest,
    canonical,
    validate_inventory,
    validate_manifest,
    write_create_only,
)
from resolve_target import resolve_target  # noqa: E402


def command(*arguments, cwd):
    subprocess.run(arguments, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def expect_failure(function, *arguments):
    try:
        function(*arguments)
    except (ContractError, OSError):
        return
    raise AssertionError("unsafe contract input was accepted")


def validate_resolver_fixture(document):
    assert document["schemaVersion"] == 2
    assert document["status"] in {
        "compatible", "invalid_target", "no_compatible_artifact",
        "resolver_error", "unsupported_target",
    }
    target = document["target"]
    assert set(target) == {"steamosVersion", "kernelVersion", "architecture"}
    if document["status"] == "compatible":
        assert document["compatibility"] in {"exact", "same_series_fallback"}
        assert document["artifact"]["trust"]["classification"] == (
            "pending-provenance-verification"
        )
        assert document["artifact"]["checksum"]["algorithm"] == "sha256"
        assert {"optionalCudaOmission"} <= set(document["capabilities"])
    else:
        assert isinstance(document["reason"], str) and isinstance(document["message"], str)
        if document["reason"] == "no_compatible_release":
            assert document["nextAction"] == {
                "schemaVersion": 1,
                "kind": "build_exact_target",
                "entrypoint": "bootstrap/build_for_target.sh",
                "executionArchitecture": "x86_64",
                "kernelPolicy": "exact",
            }
        else:
            assert "nextAction" not in document


def main():
    validate_inventory(FILES)
    for relative, _role, expected_mode in FILES:
        path = ROOT / relative
        info = path.lstat()
        assert path.is_file() and not path.is_symlink(), relative
        actual_mode = "0755" if info.st_mode & 0o111 else "0644"
        assert actual_mode == expected_mode, relative

    schema_root = ROOT / "contracts/schemas"
    resolver_schema = json.loads(
        (schema_root / "resolver-result-v2.schema.json").read_text(encoding="utf-8")
    )
    progress_schema = json.loads(
        (schema_root / "installer-progress-v1.schema.json").read_text(encoding="utf-8")
    )
    assert resolver_schema["$schema"].endswith("2020-12/schema")
    assert resolver_schema["properties"]["schemaVersion"]["const"] == 2
    assert resolver_schema["unevaluatedProperties"] is True
    assert progress_schema["properties"]["schemaVersion"]["const"] == 1
    assert progress_schema["properties"]["indeterminate"]["type"] == "boolean"
    assert progress_schema["unevaluatedProperties"] is True

    tag = "steamos-3.8.14-nvidia-575.64.05-k6.16.12-valve24.4-x86"
    archive = f"nvidia-open-{tag}-x86_64.tar.gz"
    releases = [{
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [{"name": name} for name in (
            archive,
            archive + ".sha256",
            f"nvidia-open-{tag}-x86_64.provenance.json",
        )],
    }]
    compatible = resolve_target(
        "3.8.14", "6.16.12-valve24.4-x86", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(compatible)
    assert compatible["status"] == "compatible"
    unavailable = resolve_target(
        "3.8.15", "different-kernel", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(unavailable)
    assert unavailable["status"] == "no_compatible_artifact"
    assert unavailable["nextAction"]["kind"] == "build_exact_target"
    assert unavailable["nextAction"]["kernelPolicy"] == "exact"
    incomplete = copy.deepcopy(releases)
    incomplete[0]["assets"] = []
    incomplete_result = resolve_target(
        "3.8.14", "6.16.12-valve24.4-x86", "x86_64", incomplete,
        "CorniiDog/OPEMOS",
    )
    assert incomplete_result["reason"] == "release_assets_missing"
    assert "nextAction" not in incomplete_result
    invalid = resolve_target(
        "3.8", "kernel", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(invalid)
    assert invalid["status"] == "invalid_target"

    with tempfile.TemporaryDirectory(prefix="opemos-consumer-contract-") as temporary:
        repository = Path(temporary) / "repository"
        repository.mkdir()
        command("git", "init", "-q", cwd=repository)
        command("git", "config", "user.name", "OPEMOS tests", cwd=repository)
        command("git", "config", "user.email", "tests@example.invalid", cwd=repository)
        first = repository / "bin/tool"
        second = repository / "policy/data.json"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        second.write_text("{}\n", encoding="utf-8")
        first.chmod(0o755)
        command("git", "add", ".", cwd=repository)
        command("git", "commit", "-q", "-m", "fixture", cwd=repository)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip()
        inventory = (
            ("bin/tool", "entrypoint", "0755"),
            ("policy/data.json", "policy", "0644"),
        )
        document = build_manifest(repository, commit, inventory)
        assert document == build_manifest(repository, commit, inventory)
        assert [record["path"] for record in document["files"]] == [
            "bin/tool", "policy/data.json"
        ]
        assert document["bundleId"] == hashlib.sha256(canonical({
            key: document[key] for key in (
                "schemaVersion", "kind", "repository", "supportCommit", "files"
            )
        })).hexdigest()
        validate_manifest(document, commit, inventory)

        # A mutable checkout cannot influence a manifest generated from the
        # committed blobs.
        first.write_text("changed after commit\n", encoding="utf-8")
        assert build_manifest(repository, commit, inventory) == document

        wrong_hash = copy.deepcopy(document)
        wrong_hash["bundleId"] = "0" * 64
        expect_failure(validate_manifest, wrong_hash, commit, inventory)
        duplicate = copy.deepcopy(document)
        duplicate["files"].append(copy.deepcopy(duplicate["files"][0]))
        expect_failure(validate_manifest, duplicate, commit, inventory)
        unsorted = copy.deepcopy(document)
        unsorted["files"].reverse()
        expect_failure(validate_manifest, unsorted, commit, inventory)
        expect_failure(validate_inventory, (("../escape", "policy", "0644"),))
        expect_failure(validate_inventory, (
            ("same", "policy", "0644"), ("same", "policy", "0644")
        ))
        expect_failure(build_manifest, repository, commit, (
            ("bin/tool", "entrypoint", "0644"),
        ))

        output = Path(temporary) / "manifest.json"
        payload = canonical(document) + b"\n"
        write_create_only(output, payload)
        assert output.read_bytes() == payload
        assert os.stat(output).st_mode & 0o777 == 0o644
        expect_failure(write_create_only, output, payload)


if __name__ == "__main__":
    main()
