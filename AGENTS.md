# Repository agent instructions

`BOUNDARIES.md` is the read-only ownership authority. OPEMOS.EXE mirrors the
same bytes. The previous mirror is pinned to counterpart commit
`c6733c7c80a104f57b44411d2d4223c2d624818d`; the explicitly authorized
2026-09-04 cleanup-ownership revision awaits an EXE mirror commit recorded in
`docs/boundary-decision-2026-09-04.md`. Do not modify the authority, its
integrity test, or the counterpart reference during ordinary implementation,
cleanup, documentation, release, or repinning work. A change requires an
explicit user request plus synchronized source commit, counterpart commit, Git
blob, and SHA-256 references. Do not infer permission from a task that merely
touches both repositories.

Repository summaries may link to the authority but must not contradict it.
Never edit OPEMOS.EXE from this repository's task unless the user separately
and explicitly authorizes that cross-repository mutation.

## Scheduled work and resource limits

Follow the user-authorized coordination policy at
`/home/connor/Documents/ChatGPT/Handoff troubleshooting/opemos-scheduler/POLICY.md`.
One primary plus at most one useful helper per repository; no nested helpers.
All heavy work must use the shared `opemos-scheduler/heavy.sh` wrapper.
This execution policy does not authorize any change to BOUNDARIES.md or its pins.

Boundary exception, conflict, or ambiguity: stop the affected work and ask the
user before proceeding. Do not resolve ownership questions autonomously or
work around them through another agent. Report scheduler state `approval`.
This applies equally to the primary and its helper.

Resolver may decide only small, reversible boundary-adjacent placement questions
under the shared POLICY.md. Major ownership, deletion authority, trust,
production, destructive, hardware, or governance questions still require the
user. Preserve completed TODO entries in place with commit/test evidence and
preserve Git and handoff history; do not erase or rewrite completed history.
