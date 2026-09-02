---
layout: home
title: Overview
description: Documentation for exact-kernel NVIDIA open-module builds and SteamOS image installation.
---

**OPEMOS** is the short project name for this unofficial community-built NVIDIA
open-module toolkit. It builds, validates, publishes, and installs modules for
an **exact** SteamOS release, Neptune kernel, architecture, and NVIDIA userspace
version. It also defines the machine-readable boundary used by SteamOS NVIDIA
Image Builder.

> This is active development software. Exact-kernel validation has passed for
> published development artifacts, but fresh-stock installation and NVIDIA
> hardware boot remain separate certification gates.

SteamOS and Steam Deck are Valve trademarks. NVIDIA and related marks are
NVIDIA trademarks. This project is not affiliated with, endorsed by, or
supported by Valve or NVIDIA.

## Contents

1. [Steam Deck terminal tutorial](getting-started.md)
2. [Developer tutorials](developer-guide.md)
3. [Image-builder integration](image-builder.md)
4. [Command and JSON contracts](contracts.md)
5. [Trust, safety, and recovery](security.md)
6. [Complete technical reference](technical-reference.md)
7. [Project roadmap](https://github.com/CorniiDog/open-gpu-kernel-modules-steamos-support/blob/main/TODO.md)

## Choose your path

| Goal | Start here |
| --- | --- |
| Inspect or install from a Steam Deck terminal | [Getting started](getting-started.md) |
| Build an exact missing kernel artifact | [Developer guide: offline target build](developer-guide.md#build-for-an-offline-target) |
| Integrate the support backend into an image builder | [Image-builder integration](image-builder.md) |
| Consume resolver, progress, or result JSON | [Contracts](contracts.md) |
| Publish or revise an artifact | [Developer guide: releases](developer-guide.md#publish-a-release) |
| Understand what is authenticated and what is not | [Security model](security.md) |

## Component boundaries

| Component | Owns |
| --- | --- |
| This support repository | Target resolution, headers, builds, validation, userspace locks, installation, rollback, provenance, and publishing |
| `open-gpu-kernel-modules-steamos` | Versioned `nvidia/<version>` source branches and SteamOS-specific source patches |
| SteamOS NVIDIA Image Builder | Recovery-image inspection, A/B selection, appliance lifecycle, disposable overlays, progress UI, and final-image validation |
| NVIDIA upstream | OS-independent driver source and Linux kernel interface baseline |

## Core rule

A compiled module is not interchangeable merely because two kernels share a
base version. The artifact must match the complete target kernel release and
validated vermagic. Missing exact artifacts are built against the matching
prepared Valve header tree; they are never approximated with a nearby kernel.
