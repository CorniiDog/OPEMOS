#!/usr/bin/env python3
"""Emit deterministic inactive userspace-lock generation compatibility fixtures."""

import copy
import hashlib
import json
import sys

from userspace_lock_generation_contract import (
    DISCOVERY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_GENERATION_BYTES,
    MAX_LINEAGE_GENERATIONS,
    MAX_SEQUENCE,
    MAX_TARGETS,
    canonical,
)


MAX_OUTPUT_BYTES = 512 * 1024
ACTIVE_MANIFEST = "1" * 64
OTHER_MANIFEST = "2" * 64
SIGNATURE_HASH = "3" * 64
POLICY_HASH = "4" * 64
KEYRING_HASH = "5" * 64
LOCK_HASH = "6" * 64
PACKAGE_HASH = "7" * 64
PACKAGE_SIGNATURE_HASH = "8" * 64


def exact_target(steamos="3.8.14"):
    return {
        "steamosVersion": steamos,
        "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
        "nvidiaVersion": "575.64.05",
        "architecture": "x86_64",
    }


def lock_record(steamos="3.8.14"):
    suffix = steamos.replace(".", "-")
    return {
        "target": exact_target(steamos),
        "lock": {
            "filename": f"steamos-{suffix}-nvidia-575.64.05.json",
            "schemaVersion": 1,
            "sha256": LOCK_HASH if steamos == "3.8.14" else "9" * 64,
            "size": 8192,
        },
    }


def authority():
    return {
        "policyId": "opemos-userspace-lock-generations",
        "policySchemaVersion": 1,
        "policySha256": POLICY_HASH,
        "keyringFilename": "opemos-userspace-lock-generations.gpg",
        "keyringSha256": KEYRING_HASH,
        "signingKeyFingerprint": "A" * 40,
    }


def documents(sequence=7, predecessor=ACTIVE_MANIFEST, targets=None):
    targets = copy.deepcopy(targets or [lock_record()])
    files = [
        {
            "role": "package",
            "filename": "nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst",
            "size": 343_000_000,
            "sha256": PACKAGE_HASH,
        },
        {
            "role": "package-signature",
            "filename": "nvidia-utils-575.64.05-2-x86_64.pkg.tar.zst.sig",
            "size": 566,
            "sha256": PACKAGE_SIGNATURE_HASH,
        },
    ]
    for record in targets:
        files.append({
            "role": "userspace-lock",
            "filename": record["lock"]["filename"],
            "size": record["lock"]["size"],
            "sha256": record["lock"]["sha256"],
        })
    files.sort(key=lambda item: (item["role"], item["filename"]))
    manifest = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-generation",
        "channel": "reviewed",
        "sequence": sequence,
        "publishedAt": "2026-09-03T12:00:00Z",
        "authority": authority(),
        "previousManifestSha256": predecessor,
        "targetLocks": targets,
        "files": files,
    }
    tag = f"opemos-userspace-lock-generation-v1-s{sequence}"
    manifest_payload = canonical(manifest)
    discovery = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-discovery",
        "channel": "reviewed",
        "sequence": sequence,
        "publishedAt": manifest["publishedAt"],
        "authority": copy.deepcopy(manifest["authority"]),
        "compatibility": {
            "discoverySchemaVersion": 1,
            "generationManifestSchemaVersion": 1,
            "userspaceLockSchemaVersion": 1,
            "minimumInstallerResultSchemaVersion": 1,
        },
        "generation": {
            "releaseTag": tag,
            "manifestFilename": f"{tag}.manifest.json",
            "manifestSha256": hashlib.sha256(manifest_payload).hexdigest(),
            "manifestSize": len(manifest_payload),
            "signatureFilename": f"{tag}.manifest.json.sig",
            "signatureSha256": SIGNATURE_HASH,
            "signatureSize": 566,
            "previousManifestSha256": predecessor,
        },
        "targets": copy.deepcopy(targets),
    }
    return discovery, manifest


def refresh(discovery, manifest):
    payload = canonical(manifest)
    discovery["sequence"] = manifest["sequence"]
    discovery["publishedAt"] = manifest["publishedAt"]
    discovery["authority"] = copy.deepcopy(manifest["authority"])
    discovery["targets"] = copy.deepcopy(manifest["targetLocks"])
    discovery["generation"]["manifestSha256"] = hashlib.sha256(payload).hexdigest()
    discovery["generation"]["manifestSize"] = len(payload)
    discovery["generation"]["previousManifestSha256"] = manifest[
        "previousManifestSha256"
    ]


def case(name, discovery, manifest, discovery_ok=True, manifest_ok=True,
         pair_ok=None, activation_ok=None, expected_target=None,
         activation_state=None, bootstrap_checkpoint=None):
    if pair_ok is None:
        pair_ok = discovery_ok and manifest_ok
    if activation_ok is None:
        activation_ok = pair_ok
    value = {
        "name": name,
        "expected": {
            "discoveryAccepted": discovery_ok,
            "manifestAccepted": manifest_ok,
            "pairAccepted": pair_ok,
            "activationAccepted": activation_ok,
        },
        "discovery": discovery,
        "manifest": manifest,
    }
    if expected_target is not None:
        value["expectedTarget"] = expected_target
    if activation_state is not None:
        value["activationState"] = activation_state
    if bootstrap_checkpoint is not None:
        value["bootstrapCheckpoint"] = bootstrap_checkpoint
    return value


def changed(base, callback):
    value = copy.deepcopy(base)
    callback(value)
    return value


def main():
    base_discovery, base_manifest = documents()
    cases = [case("valid-next-generation", base_discovery, base_manifest)]

    skipped_discovery, skipped_manifest = documents(
        sequence=9, predecessor=ACTIVE_MANIFEST
    )
    cases.append(case("valid-forward-skip", skipped_discovery, skipped_manifest))
    cases.append(case(
        "valid-fresh-current-bootstrap", base_discovery, base_manifest,
        activation_state={"highWaterSequence": 0, "activeManifestSha256": None},
    ))
    first_discovery, first_manifest = documents(sequence=1, predecessor=None)
    cases.append(case(
        "valid-first-generation-record", first_discovery, first_manifest,
        activation_state={"highWaterSequence": 0, "activeManifestSha256": None},
        bootstrap_checkpoint={
            "sequence": 1,
            "manifestSha256": first_discovery["generation"]["manifestSha256"],
        },
    ))
    historical_case = case(
        "fresh-historical-replay", first_discovery, first_manifest,
        activation_ok=False,
        activation_state={"highWaterSequence": 0, "activeManifestSha256": None},
    )
    cases.append(historical_case)
    cases.append(case(
        "invalid-fresh-bootstrap-checkpoint", base_discovery, base_manifest,
        activation_ok=False,
        activation_state={"highWaterSequence": 0, "activeManifestSha256": None},
        bootstrap_checkpoint={
            "sequence": 0,
            "manifestSha256": base_discovery["generation"]["manifestSha256"],
        },
    ))
    maximum_discovery, maximum_manifest = documents(
        sequence=MAX_SEQUENCE, predecessor=ACTIVE_MANIFEST
    )
    cases.append(case(
        "maximum-sequence", maximum_discovery, maximum_manifest
    ))

    catchup_7_discovery, catchup_7_manifest = documents(
        sequence=7, predecessor=ACTIVE_MANIFEST
    )
    catchup_7_hash = hashlib.sha256(canonical(catchup_7_manifest)).hexdigest()
    catchup_8_discovery, catchup_8_manifest = documents(
        sequence=8, predecessor=catchup_7_hash
    )
    catchup_8_hash = hashlib.sha256(canonical(catchup_8_manifest)).hexdigest()
    catchup_discovery, catchup_manifest = documents(
        sequence=9, predecessor=catchup_8_hash
    )
    catchup_lineage = [
        {"discovery": catchup_7_discovery, "manifest": catchup_7_manifest},
        {"discovery": catchup_8_discovery, "manifest": catchup_8_manifest},
    ]
    catchup_case = case(
        "valid-missed-generation-catchup", catchup_discovery, catchup_manifest
    )
    catchup_case["lineage"] = catchup_lineage
    cases.append(catchup_case)

    rollback_catchup_case = case(
        "valid-after-rollback-catchup", catchup_8_discovery,
        catchup_8_manifest,
        activation_state={
            "highWaterSequence": 7,
            "activeSequence": 6,
            "activeManifestSha256": ACTIVE_MANIFEST,
        },
    )
    rollback_catchup_case["lineage"] = [copy.deepcopy(catchup_lineage[0])]
    cases.append(rollback_catchup_case)

    cases.append(case(
        "replay-after-rollback", catchup_7_discovery, catchup_7_manifest,
        activation_ok=False,
        activation_state={
            "highWaterSequence": 7,
            "activeSequence": 6,
            "activeManifestSha256": ACTIVE_MANIFEST,
        },
    ))

    missing_case = case(
        "missing-catchup-generation", catchup_discovery, catchup_manifest,
        activation_ok=False,
    )
    missing_case["lineage"] = [copy.deepcopy(catchup_lineage[1])]
    cases.append(missing_case)

    tampered_lineage = copy.deepcopy(catchup_lineage)
    tampered_lineage[0]["manifest"]["publishedAt"] = "2026-09-03T12:00:01Z"
    tampered_case = case(
        "tampered-catchup-generation", catchup_discovery, catchup_manifest,
        activation_ok=False,
    )
    tampered_case["lineage"] = tampered_lineage
    cases.append(tampered_case)

    forked_lineage = copy.deepcopy(catchup_lineage)
    forked_lineage[1]["manifest"]["previousManifestSha256"] = OTHER_MANIFEST
    refresh(
        forked_lineage[1]["discovery"], forked_lineage[1]["manifest"]
    )
    forked_case = case(
        "forked-catchup-generation", catchup_discovery, catchup_manifest,
        activation_ok=False,
    )
    forked_case["lineage"] = forked_lineage
    cases.append(forked_case)

    excessive_lineage = []
    predecessor = ACTIVE_MANIFEST
    for sequence in range(7, 7 + MAX_LINEAGE_GENERATIONS + 1):
        item_discovery, item_manifest = documents(
            sequence=sequence, predecessor=predecessor
        )
        excessive_lineage.append({
            "discovery": item_discovery, "manifest": item_manifest,
        })
        predecessor = hashlib.sha256(canonical(item_manifest)).hexdigest()
    excessive_discovery, excessive_manifest = documents(
        sequence=7 + MAX_LINEAGE_GENERATIONS + 1, predecessor=predecessor
    )
    excessive_case = case(
        "excessive-catchup-lineage", excessive_discovery, excessive_manifest,
        activation_ok=False,
    )
    excessive_case["lineage"] = excessive_lineage
    cases.append(excessive_case)

    cases.extend([
        case("unknown-discovery-schema", changed(
            base_discovery, lambda value: value.update({"schemaVersion": 2})
        ), base_manifest, False, True, False, False),
        case("unknown-manifest-schema", base_discovery, changed(
            base_manifest, lambda value: value.update({"schemaVersion": 2})
        ), True, False, False, False),
        case("unknown-policy-id", changed(
            base_discovery, lambda value: value["authority"].update(
                {"policyId": "unknown-policy"}
            )
        ), base_manifest, False, True, False, False),
        case("unknown-policy-schema", changed(
            base_discovery, lambda value: value["authority"].update(
                {"policySchemaVersion": 2}
            )
        ), base_manifest, False, True, False, False),
        case("unknown-compatibility", changed(
            base_discovery, lambda value: value["compatibility"].update(
                {"userspaceLockSchemaVersion": 2}
            )
        ), base_manifest, False, True, False, False),
        case("unknown-discovery-field", changed(
            base_discovery, lambda value: value.update({"message": "ignored?"})
        ), base_manifest, False, True, False, False),
        case("unknown-authority-field", changed(
            base_discovery, lambda value: value["authority"].update({"keyId": "x"})
        ), base_manifest, False, True, False, False),
        case("unknown-manifest-field", base_discovery, changed(
            base_manifest, lambda value: value.update({"message": "ignored?"})
        ), True, False, False, False),
        case("discovery-manifest-authority-mismatch", changed(
            base_discovery, lambda value: value["authority"].update(
                {"policySha256": "a" * 64}
            )
        ), base_manifest, True, True, False, False),
    ])

    alternate_discovery, alternate_manifest = documents()
    alternate_manifest["authority"]["policySha256"] = "a" * 64
    refresh(alternate_discovery, alternate_manifest)
    cases.append(case(
        "structural-alternate-authority", alternate_discovery,
        alternate_manifest, activation_ok=False,
    ))

    two_targets = [lock_record("3.8.14"), lock_record("3.8.15")]
    ordered_discovery, ordered_manifest = documents(targets=two_targets)
    cases.append(case("valid-multiple-targets", ordered_discovery, ordered_manifest))
    reversed_discovery, reversed_manifest = documents(targets=two_targets)
    reversed_manifest["targetLocks"].reverse()
    refresh(reversed_discovery, reversed_manifest)
    cases.append(case(
        "unsorted-targets", reversed_discovery, reversed_manifest,
        False, False, False, False,
    ))
    duplicate_targets = [lock_record(), copy.deepcopy(lock_record())]
    duplicate_discovery, duplicate_manifest = documents(targets=duplicate_targets)
    cases.append(case(
        "duplicate-targets", duplicate_discovery, duplicate_manifest,
        False, False, False, False,
    ))
    duplicate_locks = [lock_record(), lock_record("3.8.15")]
    duplicate_locks[1]["lock"] = copy.deepcopy(duplicate_locks[0]["lock"])
    duplicate_lock_discovery, duplicate_lock_manifest = documents(
        targets=duplicate_locks
    )
    cases.append(case(
        "duplicate-lock-filenames", duplicate_lock_discovery,
        duplicate_lock_manifest, False, False, False, False,
    ))
    casefold_locks = [lock_record(), lock_record("3.8.15")]
    casefold_locks[0]["lock"]["filename"] = "LOCK.json"
    casefold_locks[1]["lock"]["filename"] = "lock.json"
    casefold_lock_discovery, casefold_lock_manifest = documents(
        targets=casefold_locks
    )
    cases.append(case(
        "casefold-duplicate-lock-filenames", casefold_lock_discovery,
        casefold_lock_manifest, False, False, False, False,
    ))
    cases.append(case("empty-targets", changed(
        base_discovery, lambda value: value.update({"targets": []})
    ), base_manifest, False, True, False, False))
    cases.append(case("unsafe-lock-filename", changed(
        base_discovery, lambda value: value["targets"][0]["lock"].update(
            {"filename": "../lock.json"}
        )
    ), base_manifest, False, True, False, False))
    cases.append(case("windows-reserved-lock-filename", changed(
        base_discovery, lambda value: value["targets"][0]["lock"].update(
            {"filename": "CON.json"}
        )
    ), base_manifest, False, True, False, False))
    cases.append(case("colon-in-manifest-filename", changed(
        base_discovery, lambda value: value["generation"].update(
            {"manifestFilename": "manifest:7.json"}
        )
    ), base_manifest, False, True, False, False))
    cases.append(case("trailing-dot-keyring-filename", changed(
        base_discovery, lambda value: value["authority"].update(
            {"keyringFilename": "keyring."}
        )
    ), base_manifest, False, True, False, False))
    cases.append(case("oversized-kernel-identity", changed(
        base_discovery, lambda value: value["targets"][0]["target"].update(
            {"kernelVersion": "k" * 256}
        )
    ), base_manifest, False, True, False, False))
    cases.append({
        "name": "excessive-target-count",
        "expected": {"discoveryAccepted": False, "manifestAccepted": True,
                     "pairAccepted": False, "activationAccepted": False},
        "discoveryRecipe": {
            "kind": "excessive-targets", "baseCase": "valid-next-generation",
            "count": MAX_TARGETS + 1,
        },
        "manifest": base_manifest,
    })
    cases.append(case(
        "expected-target-missing", base_discovery, base_manifest,
        activation_ok=False, expected_target=exact_target("3.8.15"),
    ))
    target_mismatch = changed(base_discovery, lambda value: value["targets"][0][
        "target"].update({"steamosVersion": "3.8.15"}))
    cases.append(case(
        "discovery-manifest-target-mismatch", target_mismatch, base_manifest,
        True, True, False, False,
    ))

    duplicate_file_manifest = changed(base_manifest, lambda value: value[
        "files"].append(copy.deepcopy(value["files"][0])))
    cases.append(case(
        "duplicate-files", base_discovery, duplicate_file_manifest,
        True, False, False, False,
    ))
    unsorted_file_manifest = changed(
        base_manifest, lambda value: value["files"].reverse()
    )
    cases.append(case(
        "unsorted-files", base_discovery, unsorted_file_manifest,
        True, False, False, False,
    ))
    cases.append(case("unknown-file-role", base_discovery, changed(
        base_manifest, lambda value: value["files"][0].update(
            {"role": "executable"}
        )
    ), True, False, False, False))
    cases.append(case("unsafe-file-name", base_discovery, changed(
        base_manifest, lambda value: value["files"][0].update(
            {"filename": "../package"}
        )
    ), True, False, False, False))
    cases.append(case("unknown-file-field", base_discovery, changed(
        base_manifest, lambda value: value["files"][0].update({"mode": "0644"})
    ), True, False, False, False))
    cases.append(case("empty-files", base_discovery, changed(
        base_manifest, lambda value: value.update({"files": []})
    ), True, False, False, False))
    cases.append(case("missing-target-lock-file", base_discovery, changed(
        base_manifest, lambda value: value["files"].pop()
    ), True, False, False, False))
    cases.append(case("target-lock-file-hash-mismatch", base_discovery, changed(
        base_manifest, lambda value: value["files"][-1].update({"sha256": "b" * 64})
    ), True, False, False, False))
    unexpected_lock_manifest = copy.deepcopy(base_manifest)
    unexpected_lock_manifest["files"].append({
        "role": "userspace-lock", "filename": "unused-lock.json",
        "size": 10, "sha256": "c" * 64,
    })
    cases.append(case(
        "unexpected-lock-file", base_discovery, unexpected_lock_manifest,
        True, False, False, False,
    ))
    duplicate_name_manifest = copy.deepcopy(base_manifest)
    duplicate_name_manifest["files"].append({
        "role": "provenance",
        "filename": duplicate_name_manifest["files"][-1]["filename"],
        "size": 10,
        "sha256": "d" * 64,
    })
    duplicate_name_manifest["files"].sort(
        key=lambda item: (item["role"], item["filename"])
    )
    cases.append(case(
        "duplicate-filename-across-roles", base_discovery,
        duplicate_name_manifest, True, False, False, False,
    ))
    casefold_name_manifest = copy.deepcopy(base_manifest)
    casefold_name_manifest["files"].extend([
        {"role": "provenance", "filename": "RECORD.json", "size": 10,
         "sha256": "d" * 64},
        {"role": "provenance", "filename": "record.json", "size": 10,
         "sha256": "e" * 64},
    ])
    casefold_name_manifest["files"].sort(
        key=lambda item: (item["role"], item["filename"])
    )
    cases.append(case(
        "casefold-duplicate-file-names", base_discovery,
        casefold_name_manifest, True, False, False, False,
    ))
    colliding_manifest = copy.deepcopy(base_manifest)
    colliding_manifest["files"][0]["filename"] = (
        base_discovery["generation"]["manifestFilename"]
    )
    colliding_discovery = copy.deepcopy(base_discovery)
    refresh(colliding_discovery, colliding_manifest)
    cases.append(case(
        "payload-collides-with-manifest", colliding_discovery,
        colliding_manifest, True, True, False, False,
    ))
    cases.append(case("oversized-file-record", base_discovery, changed(
        base_manifest, lambda value: value["files"][0].update(
            {"size": MAX_FILE_BYTES + 1}
        )
    ), True, False, False, False))
    cases.append({
        "name": "excessive-file-count",
        "expected": {"discoveryAccepted": True, "manifestAccepted": False,
                     "pairAccepted": False, "activationAccepted": False},
        "discovery": base_discovery,
        "manifestRecipe": {
            "kind": "excessive-files", "baseCase": "valid-next-generation",
            "count": MAX_FILES + 1,
        },
    })
    excessive_total_manifest = copy.deepcopy(base_manifest)
    excessive_total_manifest["files"] = [{
        "role": "package", "filename": f"package-{index}.pkg.tar.zst",
        "size": MAX_FILE_BYTES, "sha256": f"{index + 10:064x}",
    } for index in range(5)]
    cases.append(case(
        "excessive-generation-total", base_discovery, excessive_total_manifest,
        True, False, False, False,
    ))

    cases.extend([
        case("sequence-mismatch", base_discovery, changed(
            base_manifest, lambda value: value.update({"sequence": 8})
        ), True, True, False, False),
        case("zero-discovery-sequence", changed(
            base_discovery, lambda value: value.update({"sequence": 0})
        ), base_manifest, False, True, False, False),
        case("missing-predecessor", changed(
            base_discovery, lambda value: value["generation"].update(
                {"previousManifestSha256": None}
            )
        ), base_manifest, False, True, False, False),
    ])
    first_with_predecessor_discovery, first_with_predecessor_manifest = documents(
        sequence=1, predecessor=ACTIVE_MANIFEST
    )
    cases.append(case(
        "first-generation-with-predecessor", first_with_predecessor_discovery,
        first_with_predecessor_manifest, False, False, False, False,
    ))
    broken_discovery, broken_manifest = documents(predecessor=OTHER_MANIFEST)
    cases.append(case(
        "broken-immediate-predecessor", broken_discovery, broken_manifest,
        activation_ok=False,
    ))
    replay_discovery, replay_manifest = documents(
        sequence=6, predecessor=ACTIVE_MANIFEST
    )
    cases.append(case(
        "replayed-sequence", replay_discovery, replay_manifest,
        activation_ok=False,
    ))
    downgrade_discovery, downgrade_manifest = documents(
        sequence=5, predecessor=OTHER_MANIFEST
    )
    cases.append(case(
        "downgraded-sequence", downgrade_discovery, downgrade_manifest,
        activation_ok=False,
    ))

    cases.extend([
        case("malformed-manifest-hash", changed(
            base_discovery, lambda value: value["generation"].update(
                {"manifestSha256": "short"}
            )
        ), base_manifest, False, True, False, False),
        case("malformed-signature-hash", changed(
            base_discovery, lambda value: value["generation"].update(
                {"signatureSha256": "short"}
            )
        ), base_manifest, False, True, False, False),
        case("zero-signature-size", changed(
            base_discovery, lambda value: value["generation"].update(
                {"signatureSize": 0}
            )
        ), base_manifest, False, True, False, False),
        case("release-tag-sequence-mismatch", changed(
            base_discovery, lambda value: value["generation"].update(
                {"releaseTag": "opemos-userspace-lock-generation-v1-s8"}
            )
        ), base_manifest, False, True, False, False),
        case("manifest-filename-mismatch", changed(
            base_discovery, lambda value: value["generation"].update(
                {"manifestFilename": "other.manifest.json"}
            )
        ), base_manifest, False, True, False, False),
        case("signature-filename-mismatch", changed(
            base_discovery, lambda value: value["generation"].update(
                {"signatureFilename": "other.sig"}
            )
        ), base_manifest, False, True, False, False),
        case("invalid-published-at", changed(
            base_discovery, lambda value: value.update(
                {"publishedAt": "2026-02-30T12:00:00Z"}
            )
        ), base_manifest, False, True, False, False),
        case("published-at-mismatch", changed(
            base_discovery, lambda value: value.update(
                {"publishedAt": "2026-09-03T12:00:01Z"}
            )
        ), base_manifest, True, True, False, False),
    ])

    cases.extend([
        {
            "name": "duplicate-discovery-key",
            "expected": {"discoveryAccepted": False, "manifestAccepted": True,
                         "pairAccepted": False, "activationAccepted": False},
            "rawDiscovery": '{"schemaVersion":1,"schemaVersion":1}\n',
            "manifest": base_manifest,
        },
        {
            "name": "duplicate-manifest-key",
            "expected": {"discoveryAccepted": True, "manifestAccepted": False,
                         "pairAccepted": False, "activationAccepted": False},
            "discovery": base_discovery,
            "rawManifest": '{"schemaVersion":1,"schemaVersion":1}\n',
        },
        {
            "name": "non-finite-discovery",
            "expected": {"discoveryAccepted": False, "manifestAccepted": True,
                         "pairAccepted": False, "activationAccepted": False},
            "rawDiscovery": '{"schemaVersion":NaN}\n',
            "manifest": base_manifest,
        },
        {
            "name": "noncanonical-discovery-json",
            "expected": {"discoveryAccepted": False, "manifestAccepted": True,
                         "pairAccepted": False, "activationAccepted": False},
            "rawDiscovery": json.dumps(base_discovery, indent=2) + "\n",
            "manifest": base_manifest,
        },
        {
            "name": "oversized-discovery-document",
            "expected": {"discoveryAccepted": False, "manifestAccepted": True,
                         "pairAccepted": False, "activationAccepted": False},
            "discoveryRecipe": {
                "kind": "top-level-padding", "baseCase": "valid-next-generation",
                "paddingBytes": DISCOVERY_MAX_BYTES,
            },
            "manifest": base_manifest,
        },
        {
            "name": "oversized-manifest-document",
            "expected": {"discoveryAccepted": True, "manifestAccepted": False,
                         "pairAccepted": False, "activationAccepted": False},
            "discovery": base_discovery,
            "manifestRecipe": {
                "kind": "top-level-padding", "baseCase": "valid-next-generation",
                "paddingBytes": MANIFEST_MAX_BYTES,
            },
        },
    ])

    matrix = {
        "schemaVersion": 1,
        "kind": "opemos-userspace-lock-generation-compatibility-fixtures",
        "status": "inactive-design-contract",
        "authority": authority(),
        "activationState": {
            "highWaterSequence": 6,
            "activeSequence": 6,
            "activeManifestSha256": ACTIVE_MANIFEST,
        },
        "bootstrapCheckpoint": {
            "sequence": base_discovery["sequence"],
            "manifestSha256": base_discovery["generation"]["manifestSha256"],
        },
        "expectedTarget": exact_target(),
        "consumerHandoff": {
            "generationIdSource": "generation.manifestSha256",
            "manifestSha256Source": "generation.manifestSha256",
            "sequenceSource": "sequence",
            "durableIdentityFields": ["sequence", "manifestSha256"],
            "highWaterInvariant": "maximum-activated-sequence-never-decreases",
            "rollbackInvariant": "active-may-return-to-lkg-high-water-unchanged",
        },
        "durableStateFixtures": [
            {
                "name": "activate-newer",
                "before": {
                    "active": {"sequence": 6, "manifestSha256": ACTIVE_MANIFEST},
                    "lastKnownGood": {
                        "sequence": 6, "manifestSha256": ACTIVE_MANIFEST,
                    },
                    "highWaterSequence": 6,
                },
                "candidate": {
                    "sequence": base_discovery["sequence"],
                    "manifestSha256": base_discovery["generation"][
                        "manifestSha256"
                    ],
                },
                "after": {
                    "active": {
                        "sequence": base_discovery["sequence"],
                        "manifestSha256": base_discovery["generation"][
                            "manifestSha256"
                        ],
                    },
                    "lastKnownGood": {
                        "sequence": 6, "manifestSha256": ACTIVE_MANIFEST,
                    },
                    "highWaterSequence": base_discovery["sequence"],
                },
            },
            {
                "name": "rollback-active",
                "before": {
                    "active": {
                        "sequence": base_discovery["sequence"],
                        "manifestSha256": base_discovery["generation"][
                            "manifestSha256"
                        ],
                    },
                    "lastKnownGood": {
                        "sequence": 6, "manifestSha256": ACTIVE_MANIFEST,
                    },
                    "highWaterSequence": base_discovery["sequence"],
                },
                "after": {
                    "active": {"sequence": 6, "manifestSha256": ACTIVE_MANIFEST},
                    "lastKnownGood": {
                        "sequence": 6, "manifestSha256": ACTIVE_MANIFEST,
                    },
                    "highWaterSequence": base_discovery["sequence"],
                },
            },
        ],
        "limits": {
            "discoveryMaxBytes": DISCOVERY_MAX_BYTES,
            "manifestMaxBytes": MANIFEST_MAX_BYTES,
            "maxTargets": MAX_TARGETS,
            "maxFiles": MAX_FILES,
            "maxFileBytes": MAX_FILE_BYTES,
            "maxGenerationBytes": MAX_GENERATION_BYTES,
            "maxLineageGenerations": MAX_LINEAGE_GENERATIONS,
        },
        "cases": cases,
    }
    payload = canonical(matrix)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("generated userspace-lock generation fixtures are excessive")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
