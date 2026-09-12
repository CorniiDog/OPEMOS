---
layout: page
title: Product-first review tiers decision
description: Synchronized record for routine, imaging-sensitive, and release pull-request gates.
---

# Product-first review tiers decision — 2026-09-11

The user explicitly authorized both repositories to apply the lightest review
tier that matches a pull request's material behavior. Routine documentation,
isolated UI, developer-tooling, and test-harness-only changes may merge after
their required CI passes without counterpart-primary approval, provided they
cannot reach disks, images, VM or process lifecycle, production trust, releases,
physical hardware, or cross-repository contracts.

Image construction and export, device enumeration and selection, partitioning
and writing, VM and process lifecycle, Core bundle consumption, compatibility
decisions, and cross-repository contracts retain exact counterpart-primary
review. Release, signing/trust, production, physical-media, and hardware work
retains that review plus every existing explicit-user gate. Standalone evidence-
only pull requests are prohibited; implementation pull requests and authenticated
handoffs preserve evidence, with final squash identities deferred to later
material work when necessary.

This is a synchronized mirrored-boundary change. Core lands the canonical bytes
first with staged integrity evidence against the preceding EXE mirror. EXE then
mirrors those exact bytes in its own reviewed pull request. Core finally repins
its integrity check to the immutable EXE squash commit. Core does not edit EXE.

## Preserved preceding identities

- Preceding synchronized SHA-256:
  `8c882b9a25e3d53fc200d82fff0807a8746dc826410271563d37342542c01df0`
- Preceding synchronized Git blob: `2f8424a1df29fce2859126f7c42fd1885db8a425`
- Preceding EXE mirror commit:
  `507e23cf848cde3c74390f7e6c41ba09f9084a15`

## New Core canonical identities

- SHA-256: `c44a987b4931f413ee72cc6d94ff3797f746bbdba4bcf51c7d7aed9406ffd9f2`
- Git blob: `9b379788b1deadbb2088887eb10be325008254ac`

## Completed synchronization

Core PR https://github.com/CorniiDog/OPEMOS/pull/35 preserved source commit
`9fdcaf22d8be4d199807904f018f0f7c1b8a6bd9` and squash-merged as
`e36e9052b982893b5fc89f6df0fa1c8671b7cad1` after exact EXE approval and
passing required checks. EXE PR https://github.com/CorniiDog/OPEMOS.EXE/pull/86
mirrored the exact authority bytes and squash-merged as
`000fecbe271f9bd52d1ef6f0e62a1e440e0154df` after exact Core approval and ten applicable passing checks; deploy skipped. The EXE squash
has parent `ead6703f85d7b3cd0bfc22e60f3c99e49a537c23`. Core's final repin replaces
only the staged counterpart commit and synchronization flag; the preceding
identities and review history remain preserved above.
