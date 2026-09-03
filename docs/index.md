---
layout: default
title: OPEMOS
description: Documentation for exact-kernel NVIDIA open-module builds and SteamOS image installation.
---

<section class="opemos-hero" aria-labelledby="opemos-hero-title">
  <div class="opemos-wordmark"><span aria-hidden="true"></span>OPEMOS</div>
  <p class="opemos-kicker">Open Packaging for Exact-kernel Modules on SteamOS</p>
  <h1 id="opemos-hero-title">Exact NVIDIA modules.<br>Built for the kernel you actually have.</h1>
  <p class="opemos-summary">
    Build, authenticate, publish, and install NVIDIA open kernel modules for an
    exact SteamOS release, Neptune kernel, architecture, and userspace version.
  </p>
  <div class="opemos-actions">
    <a class="opemos-button opemos-button-primary" href="{{ '/getting-started.html' | relative_url }}">Steam Deck guide</a>
    <a class="opemos-button opemos-button-secondary" href="{{ '/developer-guide.html' | relative_url }}">Developer guide</a>
    <a class="opemos-button opemos-button-secondary" href="https://corniidog.github.io/OPEMOS.EXE/">Open OPEMOS.EXE docs</a>
  </div>
</section>

OPEMOS is an unofficial community-built toolkit. It also defines the
machine-readable boundary used by OPEMOS.EXE.

> This is active development software. Exact-kernel validation has passed for
> published development artifacts, but fresh-stock installation and NVIDIA
> hardware boot remain separate certification gates.

SteamOS and Steam Deck are Valve trademarks. NVIDIA and related marks are
NVIDIA trademarks. This project is not affiliated with, endorsed by, or
supported by Valve or NVIDIA.

## Contents

1. [Steam Deck terminal tutorial](getting-started.md)
2. [Developer tutorials](developer-guide.md)
3. [OPEMOS.EXE integration](image-builder.md)
4. [SteamOS desktop companion](desktop-companion.md)
5. [No-input boot interstitial](interstitial.md)
6. [Command and JSON contracts](contracts.md)
7. [Trust, safety, and recovery](security.md)
8. [Complete technical reference](technical-reference.md)
9. [Project roadmap](https://github.com/CorniiDog/OPEMOS/blob/main/TODO.md)

## Choose your path

| Goal | Start here |
| --- | --- |
| Inspect or install from a Steam Deck terminal | [Getting started](getting-started.md) |
| Build an image with the graphical macOS application | [OPEMOS.EXE documentation](https://corniidog.github.io/OPEMOS.EXE/) |
| View installed recovery health in SteamOS Desktop Mode | [Desktop companion](desktop-companion.md) |
| Understand the temporary pre-session recovery display | [No-input boot interstitial](interstitial.md) |
| Build an exact missing kernel artifact | [Developer guide: offline target build](developer-guide.md#build-for-an-offline-target) |
| Integrate OPEMOS with OPEMOS.EXE | [OPEMOS.EXE integration](image-builder.md) |
| Consume resolver, progress, or result JSON | [Contracts](contracts.md) |
| Publish or revise an artifact | [Developer guide: releases](developer-guide.md#publish-a-release) |
| Understand what is authenticated and what is not | [Security model](security.md) |

## Component boundaries

| Component | Owns |
| --- | --- |
| OPEMOS | Target resolution, headers, builds, validation, userspace locks, installation, rollback, provenance, and publishing |
| `open-gpu-kernel-modules-steamos` | Versioned `nvidia/<version>` source branches and SteamOS-specific source patches |
| [OPEMOS.EXE](https://github.com/CorniiDog/OPEMOS.EXE) | Recovery-image inspection, A/B selection, appliance lifecycle, disposable overlays, progress UI, and final-image validation |
| NVIDIA upstream | OS-independent driver source and Linux kernel interface baseline |

## Core rule

A compiled module is not interchangeable merely because two kernels share a
base version. The artifact must match the complete target kernel release and
validated vermagic. Missing exact artifacts are built against the matching
prepared Valve header tree; they are never approximated with a nearby kernel.
