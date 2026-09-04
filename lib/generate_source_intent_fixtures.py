#!/usr/bin/env python3
"""Emit deterministic source-intent authorization compatibility fixtures."""

import copy
import sys

from source_intent_contract import canonical


TARGET = {
    "steamosVersion": "3.8.14",
    "kernelVersion": "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
    "architecture": "x86_64",
}
PROJECT_SOURCE = {
    "repository": "CorniiDog/open-gpu-kernel-modules-steamos",
    "ref": "refs/heads/nvidia/575.64.05",
    "commit": "40bd1b5d6d39ae4e4180b7a665df144b08854d14",
}
UPSTREAM_SOURCE = {
    "repository": "NVIDIA/open-gpu-kernel-modules",
    "ref": "refs/tags/575.64.05",
    "commit": "1" * 40,
}
TAG = "steamos-3.8.14-nvidia-575.64.05-k6.16.12-valve24.4-1-neptune-616-gfe145653a794"


def intent(mode, selection):
    return {"schemaVersion": 1, "kind": "opemos-source-intent", "mode": mode, "target": copy.deepcopy(TARGET), "selection": selection}


def release():
    base = f"nvidia-open-{TAG}-x86_64.tar.gz"
    return [{
        "tag_name": TAG, "draft": False, "prerelease": False,
        "published_at": "2026-09-03T12:00:00Z",
        "assets": [{"name": name} for name in (base, base + ".sha256", base.removesuffix(".tar.gz") + ".provenance.json")],
    }]


def case(name, value, releases, status, reason, action=None):
    expected = {"status": status, "reason": reason}
    if action is not None:
        expected["actionKind"] = action
    return {"name": name, "intent": value, "releases": releases, "expected": expected}


def matrix():
    cases = [
        case("automatic-published", intent("automatic", None), release(), "authorized", "published_artifact_authorized", "use_published_artifact"),
        case("automatic-reviewed-build", intent("automatic", None), [], "authorized", "exact_target_build_authorized", "build_exact_target"),
        case("automatic-unreviewed-target", {**intent("automatic", None), "target": {**TARGET, "kernelVersion": "unknown-kernel"}}, [], "rejected", "automatic_no_authorized_action"),
        case("exact-published-match", intent("exact-published-artifact", {"releaseTag": TAG}), release(), "authorized", "published_artifact_authorized", "use_published_artifact"),
        case("exact-published-mismatch", intent("exact-published-artifact", {"releaseTag": "steamos-3.8.14-other"}), release(), "rejected", "requested_publication_unavailable"),
        case("automatic-malformed-publications", intent("automatic", None), ["invalid"], "rejected", "resolver_failed"),
        case("exact-reviewed-build", intent("exact-target-local-build", {"nvidiaVersion": "575.64.05"}), release(), "authorized", "exact_target_build_authorized", "build_exact_target"),
        case("exact-unreviewed-version", intent("exact-target-local-build", {"nvidiaVersion": "580.119.02"}), [], "rejected", "reviewed_build_plan_unavailable"),
        case("reviewed-project-source", intent("reviewed-project-source", {"nvidiaVersion": "575.64.05", "source": copy.deepcopy(PROJECT_SOURCE)}), [], "authorized", "reviewed_project_source_authorized", "build_exact_target"),
        case("unreviewed-project-source", intent("reviewed-project-source", {"nvidiaVersion": "575.64.05", "source": {**PROJECT_SOURCE, "commit": "2" * 40}}), [], "rejected", "reviewed_project_source_mismatch"),
        case("malformed-project-source", intent("reviewed-project-source", {"nvidiaVersion": "575.64.05", "source": {**PROJECT_SOURCE, "repository": "invalid repository"}}), [], "rejected", "source_intent_invalid"),
        case("explicit-upstream-development", intent("upstream-development", {"nvidiaVersion": "575.64.05", "source": copy.deepcopy(UPSTREAM_SOURCE), "developmentAcknowledged": True}), [], "authorized", "upstream_development_authorized", "build_upstream_development"),
        case("upstream-not-acknowledged", intent("upstream-development", {"nvidiaVersion": "575.64.05", "source": copy.deepcopy(UPSTREAM_SOURCE), "developmentAcknowledged": False}), [], "rejected", "explicit_development_acknowledgement_required"),
        case("upstream-source-substitution", intent("upstream-development", {"nvidiaVersion": "575.64.05", "source": {**UPSTREAM_SOURCE, "repository": "example/other"}, "developmentAcknowledged": True}), [], "rejected", "unsupported_development_source"),
        case("malformed-automatic-selection", intent("automatic", {}), [], "rejected", "source_intent_invalid"),
        case("floating-schema-version", {**intent("automatic", None), "schemaVersion": 1.0}, [], "rejected", "source_intent_invalid"),
        case("fractional-selection-version", intent("exact-target-local-build", {"nvidiaVersion": 575.64}), [], "rejected", "source_intent_invalid"),
        case("non-scalar-mode", intent(["automatic"], None), [], "rejected", "source_intent_invalid"),
        case("unknown-mode", intent("nearest", None), [], "rejected", "source_intent_invalid"),
        case("unsupported-architecture", {**intent("automatic", None), "target": {**TARGET, "architecture": "aarch64"}}, [], "rejected", "source_intent_invalid"),
        case("duplicate-publication-identity", intent("exact-published-artifact", {"releaseTag": TAG}), release() * 2, "rejected", "resolver_failed"),
    ]
    return {"schemaVersion": 1, "kind": "opemos-source-intent-compatibility-fixtures", "maxCases": 32, "cases": cases}


def main():
    payload = canonical(matrix())
    if len(payload) > 512 * 1024:
        raise SystemExit("source-intent fixture matrix is excessive")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
