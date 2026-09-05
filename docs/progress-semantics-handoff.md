---
layout: page
title: Progress semantics consumer handoff
description: Canonical Core adapter from installer progress records to frontend-neutral semantics.
---

Core commit consumers pin supplies `lib/progress_semantics.py` and
`contracts/schemas/progress-semantics-v1.schema.json`. Invoke the adapter with
`--record FILE`; stdout is one canonical compact JSON record. Validate the
source stream with `validate_install_contract.py` to enforce cross-record
monotonicity before adapting records.

Known phases receive a canonical index and integer millionths for current and
overall completion. Indeterminate heartbeats remain indeterminate. A valid
future phase is preserved with `phaseDisposition: future`, its current counters
remain usable, and overall progress fails safely to indeterminate. Unknown
schema versions, malformed JSON, duplicate keys, non-finite values, invalid
counters, and excessive input are rejected.

Frontends choose labels, layout, animation, controls, accessibility, and other
platform presentation. Test fixtures do not authorize production activation.

The canonical installer bundle inventory includes both the adapter executable
and its output schema. Consumers should pin the immutable Core commit plus
bundle-manifest hash and verify the listed path, mode, size, and SHA-256 before
execution.

For terminal results, invoke `--result FILE`. Core first applies the complete
authoritative installer-result validator, then emits schema-1
`opemos-result-semantics` with normalized state, phase, reason, trust, and a
computed cleanup-complete flag. Invalid or future unsupported result schemas
produce no semantic record. The output schema is
`contracts/schemas/result-semantics-v1.schema.json` and is included in the same
immutable bundle.

`contracts/fixtures/progress-semantics-v1.json` is the deterministic consumer
corpus. Consumers must reproduce every expected semantic record; its bundled
generator proves canonical bytes and prevents hand-edited fixture drift.
