#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${1:?repository root is required}"
WORK_ROOT="$(mktemp -d /tmp/open-gpu-mount-lifecycle.XXXXXX)"
SOURCE="$WORK_ROOT/source"
TARGET="$WORK_ROOT/target"
ALIAS="$WORK_ROOT/alias"
PHASE=setup

cleanup()
{
    local rc=$?
    for mount_path in "$ALIAS" "$TARGET" "$SOURCE"; do
        mountpoint -q "$mount_path" && umount -R "$mount_path" || true
    done
    if (( rc != 0 )); then
        printf '{"schemaVersion":1,"status":"failed","phase":"%s"}\n' "$PHASE"
    fi
    rm -rf "$WORK_ROOT"
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir "$SOURCE" "$TARGET" "$ALIAS"
mount -t tmpfs -o size=32m,nosuid,nodev tmpfs "$SOURCE"
mkdir "$SOURCE/expected" "$SOURCE/sibling"
mount --bind "$SOURCE/expected" "$TARGET"
PHASE=expected_topology
python3 "$REPOSITORY_ROOT/lib/verify_bind_mount.py" \
    --source "$SOURCE/expected" --target "$TARGET"
expected_device="$(findmnt -rn -M "$TARGET" -o MAJ:MIN)"

# A sibling bind has the same device identity but the wrong FSROOT topology.
umount "$TARGET"
mount --bind "$SOURCE/sibling" "$TARGET"
PHASE=wrong_fsroot
[[ "$(findmnt -rn -M "$TARGET" -o MAJ:MIN)" == "$expected_device" ]]
! python3 "$REPOSITORY_ROOT/lib/verify_bind_mount.py" \
    --source "$SOURCE/expected" --target "$TARGET"
umount "$TARGET"
mount --bind "$SOURCE/expected" "$TARGET"

# util-linux and Python locks conflict on the same directory inode through an alias.
mount --bind "$SOURCE/expected" "$ALIAS"
PHASE=alias_lock
exec 8<"$SOURCE/expected"
flock -n 8
! flock -n "$ALIAS" -c true
! python3 - "$ALIAS" <<'PY'
import fcntl
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(1)
raise SystemExit(0)
PY

# flock remains global across a private mount namespace for the aliased inode.
! unshare --mount bash -c 'mount --make-rprivate /; flock -n "$1" -c true' \
    bash "$ALIAS"
exec 8<&-

PHASE=complete
printf '{"schemaVersion":1,"status":"passed","sameDeviceWrongFsroot":"rejected","utilLinuxPythonFlock":"passed","aliasLock":"passed","namespaceLock":"passed"}\n'
