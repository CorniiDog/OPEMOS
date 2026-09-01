#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CACHE_DIR="$SCRIPT_DIR/.cache"
RUNTIME_DIR="$SCRIPT_DIR/.runtime"
IMAGE_NAME=Fedora-Cloud-Base-Generic-42-1.1.x86_64.qcow2
IMAGE_URL="https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/42/Cloud/x86_64/images/$IMAGE_NAME"
IMAGE_SHA256=e401a4db2e5e04d1967b6729774faa96da629bcf3ba90b67d8d9cce9906bec0f
BASE_IMAGE="$CACHE_DIR/$IMAGE_NAME"
OVERLAY="$RUNTIME_DIR/guest.qcow2"
SEED_DIR="$RUNTIME_DIR/seed"
SEED_ISO="$RUNTIME_DIR/seed.iso"
SERIAL_LOG="$RUNTIME_DIR/serial.log"
NO_IMAGE_DOWNLOAD=0

usage()
{
    printf 'Usage: %s [--no-image-download]\n' "${0##*/}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-image-download) NO_IMAGE_DOWNLOAD=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

for command_name in curl qemu-img qemu-system-x86_64 sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        printf 'missing required command: %s\n' "$command_name" >&2
        exit 2
    }
done

rm -rf "$RUNTIME_DIR"
mkdir -p "$CACHE_DIR" "$SEED_DIR"
[[ ! -L "$CACHE_DIR" ]] || {
    printf 'VM cache directory must not be a symbolic link.\n' >&2
    exit 1
}
[[ ! -L "$BASE_IMAGE" ]] || {
    printf 'cached Fedora image must not be a symbolic link\n' >&2
    exit 1
}
if [[ -f "$BASE_IMAGE" ]] &&
   ! printf '%s  %s\n' "$IMAGE_SHA256" "$BASE_IMAGE" | sha256sum -c - >/dev/null 2>&1; then
    if [[ "$NO_IMAGE_DOWNLOAD" == 1 ]]; then
        printf 'cached Fedora image failed its pinned SHA-256 check\n' >&2
        exit 1
    fi
    rm -f "$BASE_IMAGE"
fi
if [[ ! -f "$BASE_IMAGE" && "$NO_IMAGE_DOWNLOAD" == 1 ]]; then
    printf 'no-image-download mode requires the verified cached image: %s\n' "$BASE_IMAGE" >&2
    exit 1
fi
if [[ ! -f "$BASE_IMAGE" ]]; then
    rm -f "$BASE_IMAGE.partial"
    trap 'rm -f "$BASE_IMAGE.partial"' EXIT INT TERM
    curl -fL --retry 3 "$IMAGE_URL" -o "$BASE_IMAGE.partial"
    printf '%s  %s\n' "$IMAGE_SHA256" "$BASE_IMAGE.partial" | sha256sum -c -
    mv "$BASE_IMAGE.partial" "$BASE_IMAGE"
    trap - EXIT INT TERM
fi
printf '%s  %s\n' "$IMAGE_SHA256" "$BASE_IMAGE" | sha256sum -c -

tar --exclude=.git --exclude=tests/vm/.cache --exclude=tests/vm/.runtime \
    -C "$PROJECT_ROOT" -czf "$SEED_DIR/repo.tgz" .
cp "$SCRIPT_DIR/meta-data" "$SEED_DIR/meta-data"
cat > "$SEED_DIR/user-data" <<'EOF'
#cloud-config
users:
  - default
package_update: false
packages:
  - bash
  - btrfs-progs
  - cpio
  - git
  - util-linux
  - zstd
runcmd:
  - [bash, -lc, "mkdir -p /mnt/seed /opt/open-gpu && mount -o ro /dev/sr0 /mnt/seed && tar -xzf /mnt/seed/repo.tgz -C /opt/open-gpu"]
  - [bash, -lc, "/opt/open-gpu/tests/vm/guest-checks.sh /opt/open-gpu > /dev/ttyS0 2>&1"]
final_message: OPEN_GPU_VM_COMPLETE
power_state:
  mode: poweroff
  timeout: 30
  condition: true
EOF

if command -v hdiutil >/dev/null 2>&1; then
    hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata \
        -o "$SEED_ISO" "$SEED_DIR"
elif command -v xorriso >/dev/null 2>&1; then
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock \
        -output "$SEED_ISO" "$SEED_DIR"
else
    printf 'hdiutil (macOS) or xorriso is required to create NoCloud seed media.\n' >&2
    exit 2
fi

qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMAGE" "$OVERLAY" 20G
deadline=$(( $(date +%s) + 2700 ))
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
trap stop_qemu EXIT
trap 'stop_qemu; exit 130' INT
trap 'stop_qemu; exit 143' TERM
qemu-system-x86_64 \
    -machine q35,accel=tcg -cpu max -smp 4 -m 4096 \
    -display none -monitor none -serial "file:$SERIAL_LOG" \
    -no-reboot -nic user,model=virtio-net-pci \
    -drive "if=virtio,file=$OVERLAY,format=qcow2" \
    -drive "if=ide,media=cdrom,readonly=on,file=$SEED_ISO,format=raw" &
qemu_pid=$!
while kill -0 "$qemu_pid" 2>/dev/null; do
    if (( $(date +%s) >= deadline )); then
        kill -TERM "$qemu_pid" 2>/dev/null || true
        sleep 2
        kill -KILL "$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
        printf 'VM timed out after 45 minutes; serial log: %s\n' "$SERIAL_LOG" >&2
        exit 124
    fi
    sleep 5
done
set +e
wait "$qemu_pid"
qemu_status=$?
set -e
qemu_pid=""
trap - EXIT INT TERM

result="$(grep -E '^\{"schemaVersion":1,' "$SERIAL_LOG" | tail -n1 | tr -d '\r' || true)"
expected_result='{"schemaVersion":1,"status":"passed","transaction":"passed","flock":"passed","mountNamespace":"passed","btrfs":"passed","recoveryAB":"passed","chrootHooks":"passed","mountLifecycle":"passed","consumerContract":"passed","targetExecutionTrust":"passed","initramfsContract":"passed","steamosRecoveryHarness":"passed"}'
[[ "$qemu_status" == 0 ]] || {
    printf 'VM exited with status %s; serial log: %s\n' "$qemu_status" "$SERIAL_LOG" >&2
    exit "$qemu_status"
}
[[ "$result" == "$expected_result" ]] && grep -q 'OPEN_GPU_VM_COMPLETE' "$SERIAL_LOG" || {
    printf 'VM did not report success; serial log: %s\n' "$SERIAL_LOG" >&2
    exit 1
}
printf '%s\n' "$result"
