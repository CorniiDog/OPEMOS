#!/usr/bin/env python3
"""Validate and deterministically select self-describing driver releases."""

import hashlib
import json
import re


MAX_DOCUMENT_BYTES = 256 * 1024
MAX_CANDIDATES = 256
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TOKEN = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
LIFECYCLES = {"active", "deprecated", "revoked"}


class MetadataError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _exact_object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise MetadataError(f"{label} is malformed")


def validate(document):
    if not isinstance(document, dict) or len(canonical(document)) > MAX_DOCUMENT_BYTES:
        raise MetadataError("metadata is malformed or exceeds its size limit")
    required = {"schemaVersion", "contract", "artifactId", "target", "capabilities",
                "compatibility", "lifecycle", "archive", "provenance",
                "authentication", "extensions"}
    _exact_object(document, required, "metadata")
    if document["schemaVersion"] != 1 or document["contract"] != "opemos-driver-release-v1":
        raise MetadataError("contract version is unsupported")
    if not SHA256.fullmatch(document.get("artifactId", "")):
        raise MetadataError("artifact identity is malformed")
    target = document["target"]
    _exact_object(target, {"steamosVersion", "kernelAbi", "architecture"}, "target")
    if (not all(isinstance(target[x], str) and target[x] for x in target)
            or target["architecture"] != "x86_64"):
        raise MetadataError("target is unsupported")
    capabilities = document["capabilities"]
    _exact_object(capabilities, {"required", "optional"}, "capabilities")
    for field in ("required", "optional"):
        values = capabilities[field]
        if (not isinstance(values, list) or values != sorted(set(values))
                or not all(isinstance(x, str) and TOKEN.fullmatch(x) for x in values)):
            raise MetadataError("capabilities are malformed")
    if set(capabilities["required"]) & set(capabilities["optional"]):
        raise MetadataError("capabilities overlap")
    compatibility = document["compatibility"]
    _exact_object(compatibility, {"nvidiaModuleVersion", "userspace"}, "compatibility")
    userspace = compatibility["userspace"]
    _exact_object(userspace, {"version", "policy"}, "userspace compatibility")
    if (not isinstance(compatibility["nvidiaModuleVersion"], str)
            or not compatibility["nvidiaModuleVersion"]
            or not isinstance(userspace["version"], str) or not userspace["version"]
            or userspace["policy"] != "exact"):
        raise MetadataError("compatibility is malformed")
    lifecycle = document["lifecycle"]
    _exact_object(lifecycle, {"status", "revocation"}, "lifecycle")
    if lifecycle["status"] not in LIFECYCLES:
        raise MetadataError("lifecycle is malformed")
    revocation = lifecycle["revocation"]
    if lifecycle["status"] == "revoked":
        _exact_object(revocation, {"reason", "reference"}, "revocation")
        if not all(isinstance(revocation[x], str) and revocation[x] for x in revocation):
            raise MetadataError("revocation is malformed")
    elif revocation is not None:
        raise MetadataError("non-revoked artifact has revocation data")
    archive = document["archive"]
    _exact_object(archive, {"name", "bytes", "sha256"}, "archive")
    if (not isinstance(archive["name"], str) or not archive["name"].endswith(".tar.gz")
            or not isinstance(archive["bytes"], int) or not 1 <= archive["bytes"] <= 8 * 1024**3
            or not SHA256.fullmatch(archive.get("sha256", ""))):
        raise MetadataError("archive is malformed")
    provenance = document["provenance"]
    _exact_object(provenance, {"repository", "commit", "reference", "sha256"}, "provenance")
    if (not isinstance(provenance["repository"], str) or "/" not in provenance["repository"]
            or not COMMIT.fullmatch(provenance.get("commit", ""))
            or not isinstance(provenance["reference"], str) or not provenance["reference"]
            or not SHA256.fullmatch(provenance.get("sha256", ""))):
        raise MetadataError("provenance is malformed")
    authentication = document["authentication"]
    if not isinstance(authentication, list) or not 1 <= len(authentication) <= 8:
        raise MetadataError("authentication references are malformed")
    seen = set()
    for reference in authentication:
        _exact_object(reference, {"kind", "reference", "sha256"}, "authentication reference")
        identity = (reference.get("kind"), reference.get("reference"))
        if (reference.get("kind") not in {"signature", "attestation"}
                or not isinstance(reference.get("reference"), str) or not reference["reference"]
                or not SHA256.fullmatch(reference.get("sha256", "")) or identity in seen):
            raise MetadataError("authentication references are malformed")
        seen.add(identity)
    if not isinstance(document["extensions"], dict):
        raise MetadataError("extensions are malformed")
    expected_id = hashlib.sha256(canonical({k: document[k] for k in required - {"artifactId", "extensions"}})).hexdigest()
    if document["artifactId"] != expected_id:
        raise MetadataError("artifact identity does not match metadata")
    return document


def _rejection_identity(candidate):
    try:
        return hashlib.sha256(canonical(candidate)).hexdigest()
    except (TypeError, ValueError):
        return None


def select(candidates, target, supported_required):
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise MetadataError("candidate inventory is malformed")
    if (not isinstance(target, dict)
            or set(target) != {"steamosVersion", "kernelAbi", "architecture"}
            or not all(isinstance(value, str) and value for value in target.values())):
        raise MetadataError("requested target is malformed")
    if (not isinstance(supported_required, list)
            or supported_required != sorted(set(supported_required))
            or not all(isinstance(value, str) and TOKEN.fullmatch(value)
                       for value in supported_required)):
        raise MetadataError("supported capabilities are malformed")
    supported = set(supported_required)
    compatible = {}
    identity_conflicts = set()
    rejected = []
    for candidate in candidates:
        try:
            item = validate(candidate)
        except (MetadataError, TypeError, ValueError):
            rejected.append({"candidateSha256": _rejection_identity(candidate),
                             "reason": "metadata_invalid"})
            continue
        reason = None
        if item["lifecycle"]["status"] == "revoked":
            reason = "revoked"
        elif item["target"] != target:
            reason = "target_incompatible"
        elif (item["compatibility"]["nvidiaModuleVersion"]
              != item["compatibility"]["userspace"]["version"]):
            reason = "userspace_incompatible"
        elif not set(item["capabilities"]["required"]) <= supported:
            reason = "required_capability_unsupported"
        if reason:
            rejected.append({"artifactId": item["artifactId"], "reason": reason})
            continue
        previous = compatible.get(item["artifactId"])
        if previous is not None and canonical(previous) != canonical(item):
            identity_conflicts.add(item["artifactId"])
        else:
            compatible[item["artifactId"]] = item
    rejected = sorted(rejected, key=lambda value: json.dumps(value, sort_keys=True))
    identities = sorted(compatible)
    if identity_conflicts or len(identities) > 1:
        return {"schemaVersion": 1, "status": "ambiguous",
                "artifactIds": sorted(set(identities) | identity_conflicts),
                "rejected": rejected}
    if not compatible:
        return {"schemaVersion": 1, "status": "no_match", "rejected": rejected}
    return {"schemaVersion": 1, "status": "selected",
            "artifact": compatible[identities[0]], "rejected": rejected}
