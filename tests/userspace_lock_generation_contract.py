#!/usr/bin/env python3
"""Compatibility tests for inactive reviewed userspace-lock generations."""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from userspace_lock_generation_contract import (  # noqa: E402
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_GENERATION_BYTES,
    MAX_LINEAGE_GENERATIONS,
    MAX_TARGETS,
    GenerationContractError,
    canonical,
    load_discovery,
    load_manifest,
    validate_activation,
    validate_discovery,
    validate_manifest,
    validate_pair,
)


GENERATOR = ROOT / "lib/generate_userspace_lock_generation_fixtures.py"


def generate():
    completed = subprocess.run(
        [sys.executable, str(GENERATOR)], cwd="/", check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.stderr == b""
    assert 1 <= len(completed.stdout) <= 512 * 1024
    return completed.stdout


def excessive_targets(base, count):
    document = copy.deepcopy(base)
    template = document["targets"][0]
    records = []
    for index in range(count):
        record = copy.deepcopy(template)
        record["target"]["kernelVersion"] = f"kernel-{index:04d}"
        record["lock"]["filename"] = f"lock-{index:04d}.json"
        record["lock"]["sha256"] = f"{index + 1:064x}"
        records.append(record)
    document["targets"] = records
    return document


def excessive_files(base, count):
    document = copy.deepcopy(base)
    document["files"] = [{
        "role": "package",
        "filename": f"package-{index:04d}.pkg.tar.zst",
        "size": 1,
        "sha256": f"{index + 1:064x}",
    } for index in range(count)]
    return document


def payload_for(fixture, kind, base_cases):
    direct = kind
    raw = "raw" + kind.capitalize()
    recipe_key = kind + "Recipe"
    if direct in fixture:
        return canonical(fixture[direct])
    if raw in fixture:
        return fixture[raw].encode()
    recipe = fixture[recipe_key]
    base = copy.deepcopy(base_cases[recipe["baseCase"]][kind])
    if recipe["kind"] == "top-level-padding":
        base["padding"] = "x" * recipe["paddingBytes"]
        return canonical(base)
    if recipe["kind"] == "excessive-targets":
        return canonical(excessive_targets(base, recipe["count"]))
    if recipe["kind"] == "excessive-files":
        return canonical(excessive_files(base, recipe["count"]))
    raise AssertionError(f"unknown fixture recipe: {recipe}")


def accepted(function, *arguments):
    try:
        function(*arguments)
    except GenerationContractError:
        return False
    return True


def main():
    first = generate()
    second = generate()
    assert first == second and first.endswith(b"\n")
    matrix = json.loads(first)
    assert set(matrix) == {
        "schemaVersion", "kind", "status", "authority", "activationState",
        "bootstrapCheckpoint", "expectedTarget", "consumerHandoff", "limits",
        "durableStateFixtures", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-userspace-lock-generation-compatibility-fixtures"
    )
    assert matrix["status"] == "inactive-design-contract"
    assert matrix["consumerHandoff"] == {
        "generationIdSource": "generation.manifestSha256",
        "manifestSha256Source": "generation.manifestSha256",
        "sequenceSource": "sequence",
        "durableIdentityFields": ["sequence", "manifestSha256"],
        "highWaterInvariant": "maximum-activated-sequence-never-decreases",
        "rollbackInvariant": "active-may-return-to-lkg-high-water-unchanged",
    }
    transitions = matrix["durableStateFixtures"]
    assert [item["name"] for item in transitions] == [
        "activate-newer", "rollback-active",
    ]
    activated, rolled_back = transitions
    assert activated["after"]["highWaterSequence"] > (
        activated["before"]["highWaterSequence"]
    )
    assert rolled_back["after"]["active"] == (
        rolled_back["before"]["lastKnownGood"]
    )
    assert rolled_back["after"]["highWaterSequence"] == (
        rolled_back["before"]["highWaterSequence"]
    )
    assert matrix["limits"] == {
        "discoveryMaxBytes": DISCOVERY_MAX_BYTES,
        "manifestMaxBytes": MANIFEST_MAX_BYTES,
        "maxTargets": MAX_TARGETS,
        "maxFiles": MAX_FILES,
        "maxFileBytes": MAX_FILE_BYTES,
        "maxGenerationBytes": MAX_GENERATION_BYTES,
        "maxLineageGenerations": MAX_LINEAGE_GENERATIONS,
    }
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 80
    base_cases = {
        case["name"]: {
            key: copy.deepcopy(case[key]) for key in ("discovery", "manifest")
            if key in case
        }
        for case in cases
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-lock-generation-") as temporary:
        root = Path(temporary)
        discovery_path = root / "discovery.json"
        manifest_path = root / "manifest.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) <= {
                "name", "expected", "discovery", "manifest", "rawDiscovery",
                "rawManifest", "discoveryRecipe", "manifestRecipe",
                "expectedTarget", "activationState", "lineage",
                "bootstrapCheckpoint",
            }
            name = fixture["name"]
            names.append(name)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {
                "discoveryAccepted", "manifestAccepted", "pairAccepted",
                "activationAccepted",
            }
            assert all(type(value) is bool for value in expected.values())
            discovery_path.write_bytes(payload_for(
                fixture, "discovery", base_cases
            ))
            manifest_path.write_bytes(payload_for(fixture, "manifest", base_cases))
            try:
                discovery = load_discovery(discovery_path)
            except GenerationContractError:
                discovery = None
            try:
                manifest = load_manifest(manifest_path)
            except GenerationContractError:
                manifest = None
            discovery_ok = discovery is not None
            manifest_ok = manifest is not None
            pair_ok = (
                discovery_ok and manifest_ok
                and accepted(validate_pair, discovery, manifest)
            )
            target = fixture.get("expectedTarget", matrix["expectedTarget"])
            activation = fixture.get("activationState", matrix["activationState"])
            checkpoint = fixture.get(
                "bootstrapCheckpoint", matrix["bootstrapCheckpoint"]
            )
            activation_ok = (
                pair_ok and accepted(
                    validate_activation, discovery, manifest, matrix["authority"],
                    target, activation["highWaterSequence"],
                    activation["activeManifestSha256"], [
                        (item["discovery"], item["manifest"])
                        for item in fixture.get("lineage", [])
                    ], checkpoint, activation.get("activeSequence"),
                )
            )
            assert discovery_ok is expected["discoveryAccepted"], name
            assert manifest_ok is expected["manifestAccepted"], name
            assert pair_ok is expected["pairAccepted"], name
            assert activation_ok is expected["activationAccepted"], name

        discovery_path.write_bytes(canonical(cases[0]["discovery"]))
        for linked in (root / "linked.json", root / "hardlinked.json"):
            if linked.name == "linked.json":
                linked.symlink_to(discovery_path)
            else:
                os.link(discovery_path, linked)
            assert not accepted(load_discovery, linked)

    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-next-generation", "valid-forward-skip",
        "valid-fresh-current-bootstrap",
        "valid-first-generation-record", "fresh-historical-replay",
        "invalid-fresh-bootstrap-checkpoint",
        "maximum-sequence",
        "valid-missed-generation-catchup", "missing-catchup-generation",
        "valid-after-rollback-catchup", "replay-after-rollback",
        "tampered-catchup-generation", "forked-catchup-generation",
        "excessive-catchup-lineage",
        "unknown-discovery-schema", "unknown-manifest-schema",
        "unknown-policy-id", "unknown-policy-schema", "unknown-compatibility",
        "unknown-discovery-field", "unknown-authority-field",
        "unknown-manifest-field", "discovery-manifest-authority-mismatch",
        "structural-alternate-authority",
        "valid-multiple-targets", "unsorted-targets", "duplicate-targets",
        "duplicate-lock-filenames", "casefold-duplicate-lock-filenames",
        "empty-targets", "unsafe-lock-filename",
        "windows-reserved-lock-filename", "colon-in-manifest-filename",
        "trailing-dot-keyring-filename", "oversized-kernel-identity",
        "excessive-target-count",
        "expected-target-missing", "discovery-manifest-target-mismatch",
        "duplicate-files", "unsorted-files", "unknown-file-role",
        "unsafe-file-name", "unknown-file-field", "empty-files",
        "missing-target-lock-file", "target-lock-file-hash-mismatch",
        "unexpected-lock-file", "duplicate-filename-across-roles",
        "casefold-duplicate-file-names", "payload-collides-with-manifest",
        "oversized-file-record", "excessive-file-count",
        "excessive-generation-total", "sequence-mismatch",
        "zero-discovery-sequence", "missing-predecessor",
        "first-generation-with-predecessor", "broken-immediate-predecessor",
        "replayed-sequence", "downgraded-sequence", "malformed-manifest-hash",
        "malformed-signature-hash", "zero-signature-size",
        "release-tag-sequence-mismatch", "manifest-filename-mismatch",
        "signature-filename-mismatch", "invalid-published-at",
        "published-at-mismatch", "duplicate-discovery-key",
        "duplicate-manifest-key", "non-finite-discovery",
        "noncanonical-discovery-json", "oversized-discovery-document",
        "oversized-manifest-document",
    }

    for invalid in (None, [], "value", 1, True):
        assert not accepted(validate_discovery, invalid)
        assert not accepted(validate_manifest, invalid)

    discovery_schema = json.loads((
        ROOT / "contracts/schemas/userspace-lock-discovery-v1.schema.json"
    ).read_text(encoding="utf-8"))
    manifest_schema = json.loads((
        ROOT / "contracts/schemas/userspace-lock-generation-manifest-v1.schema.json"
    ).read_text(encoding="utf-8"))
    assert discovery_schema["additionalProperties"] is False
    assert manifest_schema["additionalProperties"] is False
    assert discovery_schema["properties"]["targets"]["maxItems"] == MAX_TARGETS
    assert manifest_schema["properties"]["files"]["maxItems"] == MAX_FILES
    filename_pattern = discovery_schema["$defs"]["plainFilename"]["pattern"]
    assert re.fullmatch(filename_pattern, "portable-file.json")
    for unsafe in (
            "CON", "con.json", "Nul.txt", "COM1.sig", "Lpt9", "bad.",
            "bad:name.json"):
        assert re.fullmatch(filename_pattern, unsafe) is None
    assert set(manifest_schema["$defs"]["file"]["properties"]["role"]["enum"]) == {
        "userspace-lock", "package", "package-signature", "keyring",
        "signer-policy", "target-policy", "gaming-profile", "provenance",
    }


if __name__ == "__main__":
    main()
