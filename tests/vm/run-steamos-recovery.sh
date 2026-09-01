#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANIFEST="$PROJECT_ROOT/trust/steamos-recovery-images.json"
CACHE_DIR="$SCRIPT_DIR/.cache"
RUNTIME_DIR="$SCRIPT_DIR/.runtime/steamos-recovery"
FEDORA_NAME=Fedora-Cloud-Base-Generic-42-1.1.x86_64.qcow2
FEDORA_URL="https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/42/Cloud/x86_64/images/$FEDORA_NAME"
FEDORA_SHA256=e401a4db2e5e04d1967b6729774faa96da629bcf3ba90b67d8d9cce9906bec0f
FEDORA_IMAGE="$CACHE_DIR/$FEDORA_NAME"
archive=""
fixture=0
no_controller_download=0

usage()
{
    printf 'Usage: %s --fixture [--no-controller-download]\n' "${0##*/}"
    printf '       %s --archive REVIEWED.img.bz2 [--no-controller-download]\n' "${0##*/}"
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fixture) fixture=1 ;;
        --archive) shift; archive="${1:-}" ;;
        --no-controller-download) no_controller_download=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
if [[ "$fixture" == 1 ]]; then
    [[ -z "$archive" ]] || { usage >&2; exit 2; }
    args=()
    [[ "$no_controller_download" == 0 ]] || args+=(--no-image-download)
    exec "$SCRIPT_DIR/run.sh" "${args[@]}"
fi
[[ -n "$archive" && "$archive" == /* ]] || { usage >&2; exit 2; }

for command_name in curl qemu-img qemu-system-x86_64 sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        printf 'missing required command: %s\n' "$command_name" >&2
        exit 2
    }
done
rm -rf "$RUNTIME_DIR"
mkdir -p "$CACHE_DIR" "$RUNTIME_DIR/seed"
[[ ! -L "$CACHE_DIR" && ! -L "$RUNTIME_DIR" && ! -L "$archive" ]]

verification="$RUNTIME_DIR/recovery-verification.json"
python3 "$PROJECT_ROOT/lib/validate_steamos_recovery_input.py" \
    --manifest "$MANIFEST" --archive "$archive" --output "$verification"
raw_size="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["rawSizeBytes"])' "$verification")"
recovery_raw="$RUNTIME_DIR/recovery.raw"
python3 "$PROJECT_ROOT/lib/decompress_bzip2_image.py" \
    --input "$archive" --output "$recovery_raw" --expected-bytes "$raw_size"
trap 'rm -f "$recovery_raw"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ ! -L "$FEDORA_IMAGE" ]]
if [[ -f "$FEDORA_IMAGE" ]] &&
   ! printf '%s  %s\n' "$FEDORA_SHA256" "$FEDORA_IMAGE" | sha256sum -c - >/dev/null 2>&1; then
    [[ "$no_controller_download" == 0 ]] || {
        printf 'cached Fedora controller failed its pinned SHA-256 check\n' >&2
        exit 1
    }
    rm -f "$FEDORA_IMAGE"
fi
if [[ ! -f "$FEDORA_IMAGE" ]]; then
    [[ "$no_controller_download" == 0 ]] || {
        printf 'verified cached Fedora controller is unavailable\n' >&2
        exit 1
    }
    partial="$FEDORA_IMAGE.partial"
    rm -f "$partial"
    trap 'rm -f "$partial"' EXIT INT TERM
    curl -fL --retry 3 "$FEDORA_URL" -o "$partial"
    printf '%s  %s\n' "$FEDORA_SHA256" "$partial" | sha256sum -c -
    mv "$partial" "$FEDORA_IMAGE"
    trap - EXIT INT TERM
fi
printf '%s  %s\n' "$FEDORA_SHA256" "$FEDORA_IMAGE" | sha256sum -c -

seed="$RUNTIME_DIR/seed"
tar --exclude=.git --exclude=tests/vm/.cache --exclude=tests/vm/.runtime \
    -C "$PROJECT_ROOT" -czf "$seed/repo.tgz" .
cp "$SCRIPT_DIR/meta-data" "$seed/meta-data"
cat > "$seed/user-data" <<'EOF'
#cloud-config
package_update: false
packages: [bash, btrfs-progs, git, util-linux]
runcmd:
  - [bash, -lc, "mkdir -p /mnt/seed /opt/open-gpu && mount -o ro /dev/sr0 /mnt/seed && tar -xzf /mnt/seed/repo.tgz -C /opt/open-gpu"]
  - [bash, -lc, "/opt/open-gpu/tests/vm/inspect-steamos-recovery.sh /dev/vdb > /dev/ttyS0 2>&1"]
final_message: OPEN_GPU_STEAMOS_RECOVERY_COMPLETE
power_state: {mode: poweroff, timeout: 30, condition: true}
EOF
seed_iso="$RUNTIME_DIR/seed.iso"
if command -v hdiutil >/dev/null 2>&1; then
    hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata -o "$seed_iso" "$seed"
elif command -v xorriso >/dev/null 2>&1; then
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock -output "$seed_iso" "$seed"
else
    printf 'hdiutil or xorriso is required for NoCloud seed media\n' >&2
    exit 2
fi

overlay="$RUNTIME_DIR/controller.qcow2"
serial="$RUNTIME_DIR/serial.log"
qemu-img create -q -f qcow2 -F qcow2 -b "$FEDORA_IMAGE" "$overlay" 20G
qemu_pid=""
stop_qemu()
{
    [[ -n "$qemu_pid" ]] || return 0
    kill -TERM "$qemu_pid" 2>/dev/null || true
    sleep 2
    kill -KILL "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
cleanup_controller()
{
    stop_qemu
    rm -f "$recovery_raw"
}
trap cleanup_controller EXIT
trap 'stop_qemu; exit 130' INT
trap 'stop_qemu; exit 143' TERM
qemu-system-x86_64 -machine q35,accel=tcg -cpu max -smp 4 -m 4096 \
    -display none -monitor none -serial "file:$serial" -no-reboot \
    -nic user,model=virtio-net-pci \
    -drive "if=virtio,file=$overlay,format=qcow2" \
    -drive "if=virtio,file=$recovery_raw,format=raw,readonly=on" \
    -drive "if=ide,media=cdrom,readonly=on,file=$seed_iso,format=raw" &
qemu_pid=$!
deadline=$(( $(date +%s) + 2700 ))
while kill -0 "$qemu_pid" 2>/dev/null; do
    (( $(date +%s) < deadline )) || { stop_qemu; exit 124; }
    sleep 5
done
set +e; wait "$qemu_pid"; qemu_status=$?; set -e
qemu_pid=""
trap - EXIT INT TERM
rm -f "$recovery_raw"
result="$(grep -E '^\{"schemaVersion":1,' "$serial" | tail -n1 | tr -d '\r' || true)"
expected='{"schemaVersion":1,"status":"passed","media":"valve-reviewed","layout":"passed","presets":"passed","hooks":"passed","recoveryAB":"passed","productionInstaller":"preflight-only","idempotency":"passed"}'
[[ "$qemu_status" == 0 && "$result" == "$expected" ]] &&
    grep -q OPEN_GPU_STEAMOS_RECOVERY_COMPLETE "$serial" || {
    printf 'SteamOS recovery controller failed; serial log: %s\n' "$serial" >&2
    exit 1
}
printf '%s\n' "$result"
