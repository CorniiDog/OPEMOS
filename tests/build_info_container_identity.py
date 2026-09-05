#!/usr/bin/env python3
"""Enforce immutable container identity in every container-backed BUILD-INFO."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
build = (ROOT / "bootstrap/build.sh").read_text(encoding="utf-8")
compile_script = (ROOT / "bootstrap/compile.sh").read_text(encoding="utf-8")
offline = (ROOT / "bootstrap/build_for_target.sh").read_text(encoding="utf-8")

for name, source in (("build.sh", build), ("compile.sh", compile_script)):
    assert "Could not determine immutable build container digest." in source, (
        f"{name} must fail when immutable container identity is unavailable"
    )
    assert "^sha256:[0-9a-f]{64}$" in source, (
        f"{name} must reject empty, truncated, uppercase, non-hex, and overlong digests"
    )
    assert "container_image=%s" in source, (
        f"{name} must record the canonical immutable container image reference"
    )
    assert "@${CONTAINER_DIGEST}" in source or "@${CONTAINER_IMAGE_REF}" in source, (
        f"{name} must bind the image repository to its digest"
    )

valid_digest = "sha256:" + "0" * 64
invalid_digests = (
    "",
    "sha256:",
    "sha256:" + "0" * 63,
    "sha256:" + "0" * 65,
    "sha256:" + "A" * 64,
    "sha256:" + "g" * 64,
    "sha512:" + "0" * 64,
)
for digest, expected in ((valid_digest, 0), *((value, 1) for value in invalid_digests)):
    result = subprocess.run(
        ["bash", "-c", '[[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]', "digest-check", digest],
        check=False,
    )
    assert result.returncode == expected, f"unexpected digest validation for {digest!r}"

inspect_at = build.index('podman image inspect "$NVIDIA_BUILD_IMAGE"')
run_at = build.index("podman run")
assert inspect_at < run_at, "build image digest must be resolved before container execution"
assert '    "$CONTAINER_IMAGE_REF" \\' in build, "container run must use the immutable image reference"
run_block = build[run_at:build.index("bash -euxo pipefail -c", run_at)]
assert '    "$NVIDIA_BUILD_IMAGE" \\' not in run_block, "container run must not use the mutable tag"
assert "container_digest=%s" not in build, "legacy bare/unknown digest metadata is forbidden"
assert "${CONTAINER_DIGEST:-unknown}" not in build, "unknown container identity is forbidden"
assert "build_mode=offline-target-fedora" in offline, (
    "the native Fedora appliance builder must identify its non-container build mode"
)
assert "container_image=%s" not in offline, (
    "the native Fedora appliance builder must not claim a container identity"
)

print("BUILD-INFO container identity checks passed")
