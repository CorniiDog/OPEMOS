# Artifact cleanup ownership decision — 2026-09-04

The user explicitly authorized a narrow amendment to `BOUNDARIES.md`. Creator
ownership governs cleanup: EXE cleans EXE-created artifacts; Core cleans
Core-created artifacts only when it can safely identify them. For a concerning
Core-created artifact conflict, Core may expose a bounded, provenance-preserving
flag for EXE to consume. Exact identity and provenance must be revalidated;
missing, stale, malformed, mismatched, conflicting, or ambiguous evidence fails
safely. The flag supplies no blanket deletion authority.

## Canonical identities

- Core boundary commit introducing the decision: `3a6f0652f4118936820871f8201f7c5e1250acbf`
- SHA-256: `136d3572effa90c1b84bcf51002d7f9641c367132de20d54dd7173f68f13c6a8`
- Git blob: `68fd9553bb8fee79cee803a38f980a94b2d80e57`

## Synchronization history

The preceding identical-mirror counterpart pin was
`c6733c7c80a104f57b44411d2d4223c2d624818d`. After the Core change, the default cross-repository integrity test
correctly failed against that old commit while the strict `--local-only` check
passed. This preserved the mirror gate during the staged handoff.

OPEMOS.EXE then mirrored the exact bytes at commit
`064d1d54c7ef2eda3d56e80c67e9f8e78a554725`. Core verified that commit read-only: its `BOUNDARIES.md` has the
canonical SHA-256 and Git blob above. Core repinned its mandatory default
cross-repository integrity check to that commit. Core did not edit EXE.

## Validation

Both commands are required focused checks after synchronization:

```bash
python3 tests/boundary_policy.py
python3 tests/boundary_policy.py --local-only
```

The default command verifies the pinned EXE commit bytes; `--local-only` verifies
the canonical Core bytes and semantic clauses independently of sibling checkout
availability.
