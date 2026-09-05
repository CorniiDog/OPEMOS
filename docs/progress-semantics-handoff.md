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
