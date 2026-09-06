---
layout: page
title: Cross-repository merge governance decision
description: Staged synchronization record for owning-lead squash merges with exact counterpart approval.
---

# Cross-repository merge governance decision — 2026-09-06

The user explicitly authorized replacing the absolute no-merge rule in both
mirrored ownership contracts. Only the owning repository primary lead may
squash-merge its repository's bounded pull request, and only after the other
repository's primary lead approves the exact repository, pull request, base
branch and commit, unchanged head commit, material scope, and required-check set.
Every required check must pass. Helpers and Resolver cannot approve or merge.

A new head commit, changed base commit, material scope change, or required-check
change invalidates approval. After verifying the protected-main squash commit,
the owning lead may delete only the exact merged topic branch. This grants no
force-push, history rewrite, other-ref deletion, non-squash merge, release,
trust/signing, production, boundary, or hardware authority.

## Canonical staged identities

- New Core canonical SHA-256:
  `ec64ceb374a56a4217ca47cedd5ae238bab30926230ef0bf2aafd8000c3512c2`
- New Core canonical Git blob: `fb13c9ae5ca0544978bcde433f50958c446cd8cf`
- Preceding synchronized SHA-256:
  `136d3572effa90c1b84bcf51002d7f9641c367132de20d54dd7173f68f13c6a8`
- Preceding synchronized Git blob: `68fd9553bb8fee79cee803a38f980a94b2d80e57`
- Current preceding EXE mirror commit:
  `064d1d54c7ef2eda3d56e80c67e9f8e78a554725`

## Synchronization sequence

This Core pull request is the canonical first governance change. Its integrity
test pins the new local bytes and explicitly verifies that the current EXE pin
still contains the preceding known bytes. After both Core checks pass, the EXE
primary must review and explicitly approve the exact unchanged Core PR identity
before Core may squash-merge it.

EXE then mirrors the exact merged `BOUNDARIES.md` bytes in an EXE-owned pull
request under the same cross-lead approval gate. Once that mirror is merged,
Core opens a separate repin pull request that replaces the preceding counterpart
commit and staged flag with the immutable EXE mirror commit. Until that repin is
merged, the staged mismatch remains explicit and cannot be mistaken for full
synchronization. Core does not edit EXE.

## Required validation

```bash
python3 tests/boundary_policy.py --local-only
python3 tests/boundary_policy.py
```

The local check authenticates the new canonical bytes and semantic clauses. The
default check additionally authenticates the preceding pinned EXE bytes while
synchronization is staged. After the final repin, both checks require identical
mirrored bytes again.
