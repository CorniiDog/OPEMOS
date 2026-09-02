#!/usr/bin/env bash
set -euo pipefail

[[ "${1:-}" == status && "${2:-}" == --json && $# -eq 2 ]] || exit 2
cat <<'EOF'
{"actions":[],"fallback":{"active":false,"automaticProfile":"console","nouveauAutomatic":false,"profile":null,"profiles":["console","igpu-desktop","nouveau-experimental"]},"moduleVerification":{"records":[{"exactKernel":true,"exactUserspace":true,"name":"nvidia","present":true,"vermagic":"6.16.12-valve24.4-1-neptune-616-gfixture","version":"575.64.05"},{"exactKernel":true,"exactUserspace":true,"name":"nvidia_drm","present":true,"vermagic":"6.16.12-valve24.4-1-neptune-616-gfixture","version":"575.64.05"},{"exactKernel":true,"exactUserspace":true,"name":"nvidia_modeset","present":true,"vermagic":"6.16.12-valve24.4-1-neptune-616-gfixture","version":"575.64.05"},{"exactKernel":true,"exactUserspace":true,"name":"nvidia_peermem","present":true,"vermagic":"6.16.12-valve24.4-1-neptune-616-gfixture","version":"575.64.05"},{"exactKernel":true,"exactUserspace":true,"name":"nvidia_uvm","present":true,"vermagic":"6.16.12-valve24.4-1-neptune-616-gfixture","version":"575.64.05"}],"status":"verified"},"reason":"exact_nvidia_ready","schemaVersion":1,"status":"healthy","target":{"kernelVersion":"6.16.12-valve24.4-1-neptune-616-gfixture","nvidiaVersion":"575.64.05"}}
EOF

