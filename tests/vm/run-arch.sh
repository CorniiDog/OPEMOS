#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CACHE_DIR="$SCRIPT_DIR/.cache/arch"
RUNTIME_DIR="$SCRIPT_DIR/.runtime/arch"
RUNTIME_LOCK="$SCRIPT_DIR/.runtime/arch.lock"
VERSION=20260815.573966
IMAGE_NAME="Arch-Linux-x86_64-cloudimg-$VERSION.qcow2"
BASE_URL="https://geo.mirror.pkgbuild.com/images/v$VERSION"
IMAGE_SHA256=5d8be8d28cfd290f051b0f67df0a6874596ad23de3f3f18b90c91aeb758eb878
SIGNER_FINGERPRINT=656E4C5AC1CC3B86E539D97E343635A6859A9174
BASE_IMAGE="$CACHE_DIR/$IMAGE_NAME"
CHECKSUM="$CACHE_DIR/$IMAGE_NAME.SHA256"
SIGNATURE="$CHECKSUM.sig"
OVERLAY="$RUNTIME_DIR/guest.qcow2"
SEED_DIR="$RUNTIME_DIR/seed"
SEED_ISO="$RUNTIME_DIR/seed.iso"
SERIAL_LOG="$RUNTIME_DIR/serial.log"
NO_DOWNLOAD=0
OFFLINE_CACHE_ONLY=0

usage() { printf 'Usage: %s [--no-download] [--offline-cache-only]\n' "${0##*/}"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-download) NO_DOWNLOAD=1 ;;
        --offline-cache-only) OFFLINE_CACHE_ONLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

for command_name in curl gpg gpgv qemu-img qemu-system-x86_64 sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        printf 'missing required command: %s\n' "$command_name" >&2
        exit 2
    }
done

mkdir -p "$SCRIPT_DIR/.runtime"
mkdir "$RUNTIME_LOCK" 2>/dev/null || {
    printf 'Arch VM runtime is already owned by another invocation.\n' >&2
    exit 1
}
release_runtime_lock() { rmdir "$RUNTIME_LOCK" 2>/dev/null || true; }
trap release_runtime_lock EXIT
trap 'release_runtime_lock; exit 130' INT
trap 'release_runtime_lock; exit 143' TERM

rm -rf "$RUNTIME_DIR"
mkdir -p "$CACHE_DIR" "$SEED_DIR"
[[ ! -L "$CACHE_DIR" ]] || {
    printf 'Arch VM cache directory must not be a symbolic link.\n' >&2
    exit 1
}
if [[ "$NO_DOWNLOAD" == 0 ]]; then
    cleanup_partial_downloads()
    {
        rm -f "$BASE_IMAGE.partial" "$CHECKSUM.partial" "$SIGNATURE.partial"
    }
    trap cleanup_partial_downloads EXIT INT TERM
    cleanup_partial_downloads
    for name in "$IMAGE_NAME" "$IMAGE_NAME.SHA256" "$IMAGE_NAME.SHA256.sig"; do
        [[ -f "$CACHE_DIR/$name" ]] ||
            curl -fL --retry 3 "$BASE_URL/$name" -o "$CACHE_DIR/$name.partial"
        if [[ -f "$CACHE_DIR/$name.partial" ]]; then
            mv "$CACHE_DIR/$name.partial" "$CACHE_DIR/$name"
        fi
    done
    trap - EXIT INT TERM
fi
for required in "$BASE_IMAGE" "$CHECKSUM" "$SIGNATURE"; do
    [[ -f "$required" && ! -L "$required" ]] || {
        printf 'verified Arch input is unavailable: %s\n' "$required" >&2
        exit 1
    }
done

mkdir -m 0700 "$RUNTIME_DIR/inspect-gnupg"
key_fingerprints="$(gpg --batch --homedir "$RUNTIME_DIR/inspect-gnupg" --show-keys --with-colons \
    "$SCRIPT_DIR/arch-boxes-signing-key.asc" |
    awk -F: '$1 == "fpr" {print $10}')"
grep -qx "$SIGNER_FINGERPRINT" <<<"$key_fingerprints"
gpg --batch --yes --homedir "$RUNTIME_DIR/inspect-gnupg" --dearmor \
    --output "$RUNTIME_DIR/arch-boxes-keyring.gpg" \
    "$SCRIPT_DIR/arch-boxes-signing-key.asc"
gpgv --keyring "$RUNTIME_DIR/arch-boxes-keyring.gpg" "$SIGNATURE" "$CHECKSUM"
[[ "$(<"$CHECKSUM")" == "$IMAGE_SHA256  $IMAGE_NAME" ]]
(cd "$CACHE_DIR" && sha256sum -c "$IMAGE_NAME.SHA256")

tar --exclude=.git --exclude=tests/vm/.cache --exclude=tests/vm/.runtime \
    -C "$PROJECT_ROOT" -czf "$SEED_DIR/repo.tgz" .
cat > "$SEED_DIR/meta-data" <<EOF
instance-id: open-gpu-arch-validation-$VERSION
local-hostname: open-gpu-arch-validation
EOF
if [[ "$OFFLINE_CACHE_ONLY" == 1 ]]; then
cat > "$SEED_DIR/user-data" <<'EOF'
#cloud-config
runcmd:
  - [bash, -lc, "mkdir -p /mnt/seed /opt/open-gpu && mount -o ro /dev/sr0 /mnt/seed && tar -xzf /mnt/seed/repo.tgz -C /opt/open-gpu"]
  - [bash, -lc, "/opt/open-gpu/tests/vm/offline-cache-guest.sh /opt/open-gpu > /dev/ttyS0 2>&1"]
final_message: OPEN_GPU_ARCH_VM_COMPLETE
power_state:
  mode: poweroff
  timeout: 30
  condition: true
EOF
else
cat > "$SEED_DIR/user-data" <<'EOF'
#cloud-config
runcmd:
  - [bash, -lc, "mkdir -p /mnt/seed /opt/open-gpu && mount -o ro /dev/sr0 /mnt/seed && tar -xzf /mnt/seed/repo.tgz -C /opt/open-gpu"]
  - [bash, -lc, "/opt/open-gpu/tests/vm/arch-guest-checks.sh > /dev/ttyS0 2>&1"]
final_message: OPEN_GPU_ARCH_VM_COMPLETE
power_state:
  mode: poweroff
  timeout: 30
  condition: true
EOF
fi
if command -v hdiutil >/dev/null 2>&1; then
    hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata -o "$SEED_ISO" "$SEED_DIR"
elif command -v xorriso >/dev/null 2>&1; then
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock -output "$SEED_ISO" "$SEED_DIR"
else
    printf 'hdiutil or xorriso is required for NoCloud seed media.\n' >&2
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
    release_runtime_lock
}
trap stop_qemu EXIT
trap 'stop_qemu; exit 130' INT
trap 'stop_qemu; exit 143' TERM
qemu-system-x86_64 -machine q35,accel=tcg -cpu max -smp 4 -m 4096 \
    -display none -monitor none -serial "file:$SERIAL_LOG" -no-reboot \
    -nic user,model=virtio-net-pci \
    -drive "if=virtio,file=$OVERLAY,format=qcow2" \
    -drive "if=ide,media=cdrom,readonly=on,file=$SEED_ISO,format=raw" &
qemu_pid=$!
while kill -0 "$qemu_pid" 2>/dev/null; do
    if (( $(date +%s) >= deadline )); then
        kill -TERM "$qemu_pid" 2>/dev/null || true
        sleep 2
        kill -KILL "$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
        exit 124
    fi
    sleep 5
done
set +e; wait "$qemu_pid"; qemu_status=$?; set -e
qemu_pid=""
release_runtime_lock
trap - EXIT INT TERM
result="$(sed -n '/{"schemaVersion":1,/ { s/^[^{]*//; p; }' "$SERIAL_LOG" | tail -n1 | tr -d '\r' || true)"
if [[ "$OFFLINE_CACHE_ONLY" == 1 ]]; then
    expected_result='{"schemaVersion":1,"status":"passed","offlineAuthenticatedCache":"passed","offlineBundleSelection":"passed"}'
else
    expected_result='{"schemaVersion":1,"status":"passed","pacman":"passed","mkinitcpio":"passed","cancellation":"passed","idempotency":"passed","initramfsContract":"passed","offlineAuthenticatedCache":"passed"}'
fi
[[ "$qemu_status" == 0 && "$result" == "$expected_result" ]] &&
    grep -q 'OPEN_GPU_ARCH_VM_COMPLETE' "$SERIAL_LOG" || {
    printf 'Arch VM failed; serial log: %s\n' "$SERIAL_LOG" >&2
    exit 1
}
printf '%s\n' "$result"
