#!/usr/bin/env python3
"""Validate bounded installed-device generation lifecycle records."""

import json
import re


MAX_DOCUMENT_BYTES = 64 * 1024
MAX_SEQUENCE = 2**64 - 1
SHA256 = re.compile(r"[0-9a-f]{64}")
REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
IDENTITY_FIELDS = {"sequence", "manifestSha256"}
STATE_FIELDS = {
    "schemaVersion", "channel", "active", "lastKnownGood",
    "highWaterSequence", "healthPending",
}
RESULT_FIELDS = {
    "schemaVersion", "channel", "status", "reason", "message", "state",
    "generationCreated", "prunedGenerations", "cancellationAfterCommit",
}
HEALTH_FIELDS = {"schemaVersion", "kind", "status", "generation", "checks"}
CHANNEL = "reviewed-userspace-lock-generations"


class DeviceGenerationContractError(ValueError):
    """A bounded device-generation record violates schema-1 semantics."""


def canonical(value):
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n").encode()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DeviceGenerationContractError("document contains a duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise DeviceGenerationContractError("document contains a non-finite number")


def strict_json(payload, label="device generation document"):
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_DOCUMENT_BYTES:
        raise DeviceGenerationContractError(f"{label} is empty or excessive")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DeviceGenerationContractError(f"{label} is malformed") from error
    if canonical(value) != payload:
        raise DeviceGenerationContractError(f"{label} is not canonical JSON")
    return value


def validate_identity(identity, nullable=False):
    if identity is None and nullable:
        return None
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS
            or type(identity.get("sequence")) is not int
            or not 1 <= identity["sequence"] <= MAX_SEQUENCE
            or not isinstance(identity.get("manifestSha256"), str)
            or SHA256.fullmatch(identity["manifestSha256"]) is None):
        raise DeviceGenerationContractError("generation identity is invalid")
    return identity


def validate_state(state):
    if (not isinstance(state, dict) or set(state) != STATE_FIELDS
            or state.get("schemaVersion") != 1
            or state.get("channel") != CHANNEL
            or type(state.get("highWaterSequence")) is not int
            or not 0 <= state["highWaterSequence"] <= MAX_SEQUENCE
            or type(state.get("healthPending")) is not bool):
        raise DeviceGenerationContractError("device generation state is invalid")
    active = validate_identity(state.get("active"), nullable=True)
    last_good = validate_identity(state.get("lastKnownGood"), nullable=True)
    if ((active is None) != (state["highWaterSequence"] == 0)
            or active is not None and active["sequence"] > state["highWaterSequence"]
            or last_good is not None
            and last_good["sequence"] > state["highWaterSequence"]
            or state["healthPending"] and active == last_good
            or not state["healthPending"] and active != last_good):
        raise DeviceGenerationContractError("device generation state is inconsistent")
    return state


def validate_result(document):
    if (not isinstance(document, dict) or not set(document) <= RESULT_FIELDS
            or not {"schemaVersion", "channel", "status", "reason"} <= set(document)
            or document.get("schemaVersion") != 1
            or document.get("channel") != CHANNEL
            or document.get("status") not in {"ok", "failed", "cancelled"}
            or not isinstance(document.get("reason"), str)
            or REASON.fullmatch(document["reason"]) is None
            or "message" in document
            and (not isinstance(document["message"], str)
                 or not 1 <= len(document["message"]) <= 512)
            or "generationCreated" in document
            and type(document["generationCreated"]) is not bool
            or "prunedGenerations" in document
            and (type(document["prunedGenerations"]) is not int
                 or not 0 <= document["prunedGenerations"] <= 32)
            or "cancellationAfterCommit" in document
            and type(document["cancellationAfterCommit"]) is not bool):
        raise DeviceGenerationContractError("device generation result is invalid")
    if "state" in document:
        validate_state(document["state"])
    return document


def validate_health(document, active=None):
    if (not isinstance(document, dict) or set(document) != HEALTH_FIELDS
            or document.get("schemaVersion") != 1
            or document.get("kind") != "opemos-device-generation-health"
            or document.get("status") != "healthy"
            or document.get("checks") != [
                "generation-integrity", "recovery-ready",
            ]):
        raise DeviceGenerationContractError("generation health evidence is invalid")
    generation = validate_identity(document.get("generation"))
    if active is not None:
        validate_identity(active)
        if generation != active:
            raise DeviceGenerationContractError(
                "health evidence does not bind the active generation"
            )
    return document
