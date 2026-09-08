---
layout: page
title: Cross-repository merge governance decision
description: Synchronized record for owning-lead squash merges with exact counterpart approval.
---

# Cross-repository merge governance decision — 2026-09-06

The user explicitly authorized replacing the absolute no-merge rule in both
mirrored ownership contracts. Only the owning repository primary lead may
squash-merge its repository's bounded pull request, and only after the other
repository's primary lead approves the exact repository, pull request, base
branch and commit, unchanged head commit, material scope, and required-check set.
Every required check must pass. Helpers and Resolver cannot approve or merge.
When GitHub refuses an approving review solely because both primary leads use
the pull-request author identity, the counterpart primary may record the same
exact approval through the authenticated scheduler/handoff channel, including
the provider refusal. User prompts, nudges, helpers, and Resolver cannot create
or substitute for that counterpart-primary approval.

A new head commit, changed base commit, material scope change, or required-check
change invalidates approval. After verifying the protected-main squash commit,
the owning lead may delete only the exact merged topic branch. This grants no
force-push, history rewrite, other-ref deletion, non-squash merge, release,
trust/signing, production, boundary, or hardware authority.

## Canonical synchronized identities

- New Core canonical SHA-256:
  `8c882b9a25e3d53fc200d82fff0807a8746dc826410271563d37342542c01df0`
- New Core canonical Git blob: `2f8424a1df29fce2859126f7c42fd1885db8a425`
- Preceding synchronized SHA-256:
  `136d3572effa90c1b84bcf51002d7f9641c367132de20d54dd7173f68f13c6a8`
- Preceding synchronized Git blob: `68fd9553bb8fee79cee803a38f980a94b2d80e57`
- Synchronized EXE mirror commit:
  `507e23cf848cde3c74390f7e6c41ba09f9084a15`
- Preceding EXE mirror commit:
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

## Completed synchronization

Core PR https://github.com/CorniiDog/OPEMOS/pull/29 preserved its four source
commits and squash-merged as
`73e8d15c07671f3174f1a948d525e18db1084e5a` after exact EXE-primary approval
and passing checks. EXE PR https://github.com/CorniiDog/OPEMOS.EXE/pull/53
preserved its two source commits and squash-merged as
`507e23cf848cde3c74390f7e6c41ba09f9084a15` after exact Core-primary approval
and passing checks. Both merged `BOUNDARIES.md` files have Git blob
`2f8424a1df29fce2859126f7c42fd1885db8a425` and SHA-256
`8c882b9a25e3d53fc200d82fff0807a8746dc826410271563d37342542c01df0`.
This final Core repin replaces only the staged counterpart identity and flag;
the preceding identities and approval history remain above.
