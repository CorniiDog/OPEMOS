#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:?repository root is required}"
cd "$REPOSITORY_ROOT"
TARGET_DIR=/home/fedora/opemos-desktop-target

sudo -u fedora env HOME=/home/fedora CARGO_TARGET_DIR="$TARGET_DIR" \
    cargo test --locked --release --manifest-path desktop/Cargo.toml
sudo -u fedora env HOME=/home/fedora CARGO_TARGET_DIR="$TARGET_DIR" \
    cargo build --locked --release --manifest-path desktop/Cargo.toml
sudo -u fedora env HOME=/home/fedora LIBGL_ALWAYS_SOFTWARE=1 \
    xvfb-run -a "$TARGET_DIR/release/opemos-recovery-status" \
        --smoke-test --recoveryctl tests/fixtures/recoveryctl-healthy.sh

printf '%s\n' '{"schemaVersion":1,"status":"passed","desktopGui":"passed"}'
