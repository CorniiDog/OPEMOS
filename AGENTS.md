# Repository agent instructions

`BOUNDARIES.md` is the read-only ownership authority. OPEMOS.EXE mirrors the
same bytes, currently pinned by this repository to counterpart commit
`c6733c7c80a104f57b44411d2d4223c2d624818d`. Do not modify the authority, its
integrity test, or the counterpart reference during ordinary implementation,
cleanup, documentation, release, or repinning work. A change requires an
explicit user request plus synchronized source commit, counterpart commit, Git
blob, and SHA-256 references. Do not infer permission from a task that merely
touches both repositories.

Repository summaries may link to the authority but must not contradict it.
Never edit OPEMOS.EXE from this repository's task unless the user separately
and explicitly authorizes that cross-repository mutation.
