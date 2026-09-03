#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:?repository root is required}"
cd "$REPOSITORY_ROOT"
TARGET_DIR=/home/fedora/opemos-interstitial-target

sudo -u fedora env HOME=/home/fedora CARGO_TARGET_DIR="$TARGET_DIR" \
    cargo test --locked --release --manifest-path interstitial/Cargo.toml
sudo -u fedora env HOME=/home/fedora CARGO_TARGET_DIR="$TARGET_DIR" \
    cargo build --locked --release --manifest-path interstitial/Cargo.toml
"$TARGET_DIR/release/opemos-interstitial" --smoke-test
binary_sha256="$(sha256sum "$TARGET_DIR/release/opemos-interstitial" | awk '{print $1}')"
python3 lib/validate_interstitial_binary.py \
    --binary "$TARGET_DIR/release/opemos-interstitial" --sha256 "$binary_sha256" >/dev/null

sed "s|@DEST@|$REPOSITORY_ROOT|g" \
    support/recovery/opemos-interstitial.service.in \
    > /tmp/opemos-interstitial.service
sed "s|@DEST@|$REPOSITORY_ROOT|g" \
    support/recovery/opemos-nvidia-guardian.service.in \
    > /tmp/opemos-nvidia-guardian.service
systemd-analyze verify /tmp/opemos-interstitial.service /tmp/opemos-nvidia-guardian.service

install -d -o root -g root -m 0755 /run/opemos/interstitial
python3 lib/interstitial_progress.py reset \
    --state /run/opemos/interstitial/progress.json >/dev/null
python3 lib/interstitial_progress.py succeed \
    --state /run/opemos/interstitial/progress.json >/dev/null

drm_result=unavailable
if compgen -G '/dev/dri/card*' >/dev/null; then
    python3 lib/interstitial_progress.py reset \
        --state /run/opemos/interstitial/progress.json >/dev/null
    python3 lib/interstitial_progress.py set --phase inspecting \
        --state /run/opemos/interstitial/progress.json >/dev/null
    "$TARGET_DIR/release/opemos-interstitial" --timeout 15 &
    interstitial_pid=$!
    sleep 2
    kill -TERM "$interstitial_pid"
    wait "$interstitial_pid"
    pgrep -f "$TARGET_DIR/release/opemos-interstitial" >/dev/null && exit 1
    python3 lib/interstitial_progress.py reset \
        --state /run/opemos/interstitial/progress.json >/dev/null
    python3 lib/interstitial_progress.py succeed \
        --state /run/opemos/interstitial/progress.json >/dev/null
    if timeout 20 "$TARGET_DIR/release/opemos-interstitial" --timeout 15; then
        drm_result=passed
    else
        status=$?
        [[ "$status" == 1 ]] || exit "$status"
        # A headless QEMU scanout may expose a card with no connected connector.
        # The fail-open path must be bounded and must not leave the process alive.
        pgrep -f "$TARGET_DIR/release/opemos-interstitial" >/dev/null && exit 1
        drm_result=fail-open
    fi
fi

printf '{"schemaVersion":1,"status":"passed","interstitialBuild":"passed","softwareFrame":"passed","drm":"%s"}\n' "$drm_result"
