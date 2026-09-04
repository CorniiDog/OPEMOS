#!/usr/bin/env python3
"""Authorize one explicit source intent using Core resolver/build policy."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from gaming_payload_profiles import ProfileError
from resolve_target import (
    MAX_RELEASES_BYTES,
    exact_target_build_action,
    read_bounded_regular,
    resolve_target,
    strict_json,
)


MAX_INTENT_BYTES = 64 * 1024
MODES = {
    "automatic", "exact-published-artifact", "exact-target-local-build",
    "reviewed-project-source", "upstream-development",
}
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,2}")
STEAMOS = re.compile(r"[0-9]+(?:\.[0-9]+){2}")
KERNEL = re.compile(r"[A-Za-z0-9._+~-]{1,255}")
COMMIT = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"[A-Za-z0-9._+~-]{1,1024}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class SourceIntentError(ValueError):
    pass


def fail(message):
    raise SourceIntentError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_source(source, repository=None, reference=None):
    if (not isinstance(source, dict) or set(source) != {"repository", "ref", "commit"}
            or not isinstance(source.get("repository"), str)
            or not isinstance(source.get("ref"), str)
            or len(source["repository"]) > 255
            or not matches(REPOSITORY, source["repository"])
            or not 1 <= len(source["ref"]) <= 1024
            or not matches(COMMIT, source.get("commit"))
            or repository is not None and source["repository"] != repository
            or reference is not None and source["ref"] != reference):
        fail("source identity is invalid")
    return source


def validate_intent(intent):
    if (not isinstance(intent, dict) or set(intent) != {
            "schemaVersion", "kind", "mode", "target", "selection"}
            or type(intent.get("schemaVersion")) is not int
            or intent["schemaVersion"] != 1
            or intent.get("kind") != "opemos-source-intent"
            or not isinstance(intent.get("mode"), str)
            or intent["mode"] not in MODES):
        fail("source intent identity is invalid")
    target = intent["target"]
    if (not isinstance(target, dict) or set(target) != {
            "steamosVersion", "kernelVersion", "architecture"}
            or not matches(STEAMOS, target.get("steamosVersion"))
            or not matches(KERNEL, target.get("kernelVersion"))
            or target.get("architecture") != "x86_64"):
        fail("source intent target is invalid")
    mode = intent["mode"]
    selection = intent["selection"]
    if mode == "automatic":
        if selection is not None:
            fail("automatic source intent cannot contain a selection")
    elif mode == "exact-published-artifact":
        if (not isinstance(selection, dict) or set(selection) != {"releaseTag"}
                or not matches(TAG, selection.get("releaseTag"))):
            fail("published source selection is invalid")
    elif mode == "exact-target-local-build":
        if (not isinstance(selection, dict) or set(selection) != {"nvidiaVersion"}
                or not matches(VERSION, selection.get("nvidiaVersion"))):
            fail("exact-target build selection is invalid")
    elif mode == "reviewed-project-source":
        if (not isinstance(selection, dict) or set(selection) != {"nvidiaVersion", "source"}
                or not matches(VERSION, selection.get("nvidiaVersion"))):
            fail("reviewed project selection is invalid")
        validate_source(selection["source"])
    else:
        if (not isinstance(selection, dict) or set(selection) != {
                "nvidiaVersion", "source", "developmentAcknowledged"}
                or not matches(VERSION, selection.get("nvidiaVersion"))
                or type(selection.get("developmentAcknowledged")) is not bool):
            fail("upstream development selection is invalid")
        validate_source(selection["source"])
    return intent


def decision(intent, releases, repository="CorniiDog/OPEMOS"):
    try:
        validate_intent(intent)
    except SourceIntentError:
        return result("rejected", "source_intent_invalid", intent)
    target = intent["target"]
    mode = intent["mode"]
    resolver_releases = releases
    if mode == "exact-published-artifact":
        full_result = resolve_target(
            target["steamosVersion"], target["kernelVersion"],
            target["architecture"], releases, repository,
        )
        if full_result["status"] == "resolver_error":
            return result("rejected", "resolver_failed", intent)
        requested_tag = intent["selection"]["releaseTag"]
        resolver_releases = [
            item for item in releases
            if isinstance(item, dict) and item.get("tag_name") == requested_tag
        ]
    resolved = resolve_target(
        target["steamosVersion"], target["kernelVersion"],
        target["architecture"], resolver_releases, repository,
    )
    if mode in {"automatic", "exact-published-artifact"}:
        if resolved["status"] in {"resolver_error", "invalid_target", "unsupported_target"}:
            return result("rejected", "resolver_failed", intent)
        if resolved["status"] == "compatible":
            if (mode == "exact-published-artifact"
                    and resolved["publication"]["tag"]
                    != intent["selection"]["releaseTag"]):
                return result("rejected", "requested_publication_unavailable", intent)
            return result("authorized", "published_artifact_authorized", intent, {
                "schemaVersion": 1,
                "kind": "use_published_artifact",
                "resolverResultSha256": hashlib.sha256(canonical(resolved)).hexdigest(),
                "resolverResult": resolved,
            })
        if (mode == "automatic" and resolved.get("nextAction") is not None):
            return result("authorized", "exact_target_build_authorized", intent, resolved["nextAction"])
        reason = "automatic_no_authorized_action" if mode == "automatic" else "requested_publication_unavailable"
        return result("rejected", reason, intent)

    selection = intent["selection"]
    if mode in {"exact-target-local-build", "reviewed-project-source"}:
        try:
            action = exact_target_build_action(
                target["steamosVersion"], target["kernelVersion"], target["architecture"]
            )
        except ProfileError:
            return result("rejected", "reviewed_build_plan_unavailable", intent)
        if (action is None or action["buildPlan"]["target"]["nvidiaVersion"] != selection["nvidiaVersion"]):
            return result("rejected", "reviewed_build_plan_unavailable", intent)
        if mode == "reviewed-project-source" and action["buildPlan"]["source"] != selection["source"]:
            return result("rejected", "reviewed_project_source_mismatch", intent)
        return result("authorized", "reviewed_project_source_authorized" if mode == "reviewed-project-source" else "exact_target_build_authorized", intent, action)

    source = selection["source"]
    if selection["developmentAcknowledged"] is not True:
        return result("rejected", "explicit_development_acknowledgement_required", intent)
    try:
        validate_source(
            source, "NVIDIA/open-gpu-kernel-modules",
            f"refs/tags/{selection['nvidiaVersion']}",
        )
    except SourceIntentError:
        return result("rejected", "unsupported_development_source", intent)
    return result("authorized", "upstream_development_authorized", intent, {
        "schemaVersion": 1,
        "kind": "build_upstream_development",
        "entrypoint": "bootstrap/build_for_target.sh",
        "executionArchitecture": "x86_64",
        "kernelPolicy": "exact",
        "trust": "development-unverified",
        "publicationPermitted": False,
        "target": {**target, "nvidiaVersion": selection["nvidiaVersion"]},
        "source": source,
    })


def result(status, reason, intent, action=None):
    target = intent.get("target") if isinstance(intent, dict) else None
    if reason == "source_intent_invalid":
        target = None
    document = {
        "schemaVersion": 1,
        "kind": "opemos-source-authorization",
        "status": status,
        "reason": reason,
        "intentSha256": hashlib.sha256(canonical(intent)).hexdigest(),
        "target": target,
    }
    if action is not None:
        document["action"] = action
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--releases", required=True, type=Path)
    parser.add_argument("--repository", default="CorniiDog/OPEMOS")
    args = parser.parse_args()
    try:
        intent_payload = read_bounded_regular(args.intent, MAX_INTENT_BYTES)
        intent = strict_json(intent_payload)
        if canonical(intent) != intent_payload:
            fail("source intent is not canonical JSON")
        releases = strict_json(read_bounded_regular(args.releases, MAX_RELEASES_BYTES))
        if not isinstance(releases, list):
            fail("release metadata must be an array")
        document = decision(intent, releases, args.repository)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"source_intent_contract.py: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
