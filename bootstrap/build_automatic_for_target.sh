#!/usr/bin/env bash
# Execute only the existing reviewed Automatic build action, in private Podman
# storage. Compiler dependencies belong to the disposable container, not SteamOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SUPPORT_ROOT/lib/common.sh"

[[ $# == 5 || $# == 6 ]] || die "Usage: build_automatic_for_target.sh STEAMOS KERNEL NVIDIA RELEASES OUTPUT [FAILURE_RESULT]"
STEAMOS="$1" KERNEL="$2" NVIDIA="$3" RELEASES="$4" OUTPUT="$5"
FAILURE_RESULT="${6:-}"
[[ -z "$FAILURE_RESULT" || ! -e "$FAILURE_RESULT" && ! -L "$FAILURE_RESULT" ]] || die "Failure output already exists."
[[ "$SUPPORT_REPO" == CorniiDog/OPEMOS ]] || die "Automatic builds require the canonical Core repository."
[[ "$NVIDIA_BUILD_IMAGE" == registry.fedoraproject.org/fedora:42 ]] ||
    die "Automatic builds require the existing Fedora build image."
for command in python3 git; do need_cmd "$command"; done
if ! command -v podman >/dev/null 2>&1; then
    exec "$SCRIPT_DIR/setup_build_env.sh" --temporary-podman "$@"
fi
[[ "$(uname -m)" == x86_64 ]] || die "Automatic builds require x86_64."
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || die "Build output already exists."
WORK="$(mktemp -d "${TMPDIR:-/tmp}/opemos-automatic-build.XXXXXX")"
PODMAN=(podman --root "$WORK/storage" --runroot "$WORK/run" --storage-driver vfs)
ACTIVE=""
cleanup()
{
    local rc=$?
    local preserve=0
    if [[ -n "$ACTIVE" ]]; then
        kill -TERM -- "-$ACTIVE" 2>/dev/null || true
        for attempt in {1..10}; do
            kill -0 -- "-$ACTIVE" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL -- "-$ACTIVE" 2>/dev/null || true
        wait "$ACTIVE" 2>/dev/null || true
    fi
    if [[ -f "$WORK/container.cid" && ! -L "$WORK/container.cid" ]]; then
        local cid
        cid="$(cat "$WORK/container.cid")"
        if [[ "$cid" =~ ^[0-9a-f]{64}$ ]]; then
            local exists_rc=0
            "${PODMAN[@]}" container exists "$cid" || exists_rc=$?
            case "$exists_rc" in
                0) "${PODMAN[@]}" rm --force "$cid" >/dev/null 2>&1 || preserve=1 ;;
                1) ;; # --rm already retired this exact owned container
                *) preserve=1 ;;
            esac
        else
            preserve=1
        fi
    elif [[ -e "$WORK/container.cid" || -L "$WORK/container.cid" ]]; then
        preserve=1
    fi
    # Preserve the existing bounded build-result contract, not compiler logs or
    # container data. Only the caller's create-only private result is written.
    if [[ "$rc" != 0 && -n "$FAILURE_RESULT" && -f "$WORK/output/build-result.json" ]]; then
        python3 - "$SUPPORT_ROOT/lib" "$WORK/output/build-result.json" \
            "$FAILURE_RESULT" "$STEAMOS" "$KERNEL" "$NVIDIA" <<'PY' || rc=1
import os, re, sys
sys.path.insert(0, sys.argv[1])
from resolve_target import read_bounded_regular, strict_json
from source_intent_contract import canonical
document = strict_json(read_bounded_regular(sys.argv[2], 65536))
if not (set(document) == {'schemaVersion','status','reason','message','trust','target'}):
    raise SystemExit("Automatic build identity validation failed.")
if not (type(document['schemaVersion']) is int and document['schemaVersion'] == 1):
    raise SystemExit("Automatic build identity validation failed.")
if not (document['status'] in ('failed', 'cancelled')):
    raise SystemExit("Automatic build identity validation failed.")
if not (document['target'] == dict(zip(('steamosVersion','kernelVersion','nvidiaVersion','architecture'), (*sys.argv[4:7], 'x86_64')))):
    raise SystemExit("Automatic build identity validation failed.")
if not (document['trust'] == 'development-unverified'):
    raise SystemExit("Automatic build identity validation failed.")
if not (isinstance(document['reason'], str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', document['reason'])):
    raise SystemExit("Automatic build identity validation failed.")
if not (isinstance(document['message'], str) and 0 < len(document['message']) <= 2048):
    raise SystemExit("Automatic build identity validation failed.")
if not (all(c in '\n\t' or ord(c) >= 32 for c in document['message'])):
    raise SystemExit("Automatic build identity validation failed.")
descriptor = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'wb') as output:
    output.write(canonical(document))
    output.flush()
    os.fsync(output.fileno())
PY
    fi
    if [[ "$preserve" == 1 ]]; then
        warn "Container cleanup was not proven; preserving owned workspace: $WORK"
        return 1
    fi
    # Rootless image layers can contain mapped subordinate owners. Enter only
    # Podman's user namespace to retire this freshly created private store.
    if [[ -d "$WORK/storage" ]]; then
        if ! "${PODMAN[@]}" unshare rm -rf -- "$WORK/storage"; then
            warn "Private image storage cleanup failed; preserving workspace: $WORK"
            return 1
        fi
    fi
    rm -rf -- "$WORK"
    return "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
run_cancellable()
{
    local rc
    python3 "$SUPPORT_ROOT/lib/run_in_process_group.py" "$@" &
    ACTIVE=$!
    if wait "$ACTIVE"; then rc=0; else rc=$?; fi
    ACTIVE=""
    return "$rc"
}
python3 "$SUPPORT_ROOT/lib/automatic_install_selection.py" \
    --steamos "$STEAMOS" --kernel "$KERNEL" --releases "$RELEASES" --build-as-fallback > "$WORK/action.json"
IFS=$'\t' read -r SOURCE_REPOSITORY SOURCE_COMMIT SOURCE_REF < <(
    python3 - "$WORK/action.json" "$NVIDIA" <<'PY'
import json, sys
document = json.load(open(sys.argv[1]))
action = document['action']
if not (document['status'] == 'authorized' and action['kind'] == 'build_exact_target'):
    raise SystemExit("Automatic build identity validation failed.")
plan = action['buildPlan']
if not (plan['target']['nvidiaVersion'] == sys.argv[2]):
    raise SystemExit("Automatic build identity validation failed.")
source = plan['source']
print('\t'.join(source[field] for field in ('repository', 'commit', 'ref')))
PY
)
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ && -n "$SOURCE_REPOSITORY" && -n "$SOURCE_REF" ]] ||
    die "No reviewed Automatic build matches installed userspace."
run_cancellable git clone --quiet --no-checkout --depth 1 \
    --branch "${SOURCE_REF#refs/heads/}" "https://github.com/$SOURCE_REPOSITORY.git" "$WORK/source"
run_cancellable git -C "$WORK/source" fetch --quiet --depth 1 origin "$SOURCE_COMMIT"
git -C "$WORK/source" checkout --quiet --detach "$SOURCE_COMMIT"
[[ "$(git -C "$WORK/source" rev-parse HEAD)" == "$SOURCE_COMMIT" ]] || die "Source pin mismatch."
run_cancellable "${PODMAN[@]}" pull "$NVIDIA_BUILD_IMAGE"
IMAGE_DIGEST="$("${PODMAN[@]}" image inspect --format '{{index .RepoDigests 0}}' "$NVIDIA_BUILD_IMAGE")"
[[ "$IMAGE_DIGEST" =~ ^registry.fedoraproject.org/fedora@sha256:[0-9a-f]{64}$ ]] || die "Could not seal the Fedora image digest."
log "Using sealed Fedora image: $IMAGE_DIGEST"
mkdir "$WORK/output"
run_cancellable "${PODMAN[@]}" run --rm --cidfile "$WORK/container.cid" \
    --cpus 1 --memory 5g --memory-swap 5g --security-opt label=disable \
    -v "$SUPPORT_ROOT:/support:ro" -v "$WORK/source:/source:rw" \
    -v "$WORK/output:/output:rw" -e HOME=/tmp -e OPEMOS_BUILD_JOBS=1 \
    -e "OPEMOS_BUILD_CONTAINER_IMAGE=$IMAGE_DIGEST" \
    "$IMAGE_DIGEST" bash -euc '
        dnf install -y sudo bc binutils bsdtar curl diffutils elfutils-libelf-devel \
            findutils gcc gcc-c++ git gnupg2 kmod make openssl-devel pahole perl python3 zstd
        python3 /support/bootstrap/prepare_valve_keyring.py --output /tmp/valve.gpg > /tmp/keyring.json
        signer=$(python3 -c '\''import json,sys; d=json.load(open("/tmp/keyring.json")); len(d["signers"])==1 or sys.exit("Expected exactly one authenticated signer"); print(d["signers"][0])'\'')
        /support/bootstrap/build_for_target.sh --steamos "$1" --kernel "$2" --nvidia "$3" \
            --source /source --source-commit "$4" --output /tmp/raw-product \
            --header-keyring /tmp/valve.gpg --header-signer "$signer" \
            --require-compiler-major-match --result-json /output/build-result.json
        shopt -s nullglob
        archives=(/tmp/raw-product/nvidia-open-*.tar.gz)
        [[ ${#archives[@]} == 1 ]] || exit 1
        archive="${archives[0]}"
        support_revision=$(git -C /support rev-parse HEAD)
        python3 /support/lib/repack_module_artifact.py \
            --archive "$archive" --checksum "$archive.sha256" \
            --build-info "${archive%.tar.gz}.build-info.txt" \
            --provenance "${archive%.tar.gz}.provenance.json" \
            --output-dir /tmp/repacked --support-commit "$support_revision"
        packed=(/tmp/repacked/nvidia-open-*.tar.gz)
        [[ ${#packed[@]} == 1 ]] || exit 1
        archive="${packed[0]}"
        provenance="${archive%.tar.gz}.provenance.json"
        python3 /support/lib/build_driver_product.py \
            --archive "$archive" --checksum "$archive.sha256" \
            --build-info "${archive%.tar.gz}.build-info.txt" --provenance "$provenance" \
            --output-dir /tmp/driver-product
        products=(/tmp/driver-product/*.tar.gz)
        [[ ${#products[@]} == 1 ]] || exit 1
        tag=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[\"artifact\"][\"releaseTag\"])" "$provenance")
        python3 /support/lib/driver_binary_bundle.py create \
            --archive "${products[0]}" --sidecar "${products[0]}.sha256" \
            --output /tmp/driver-product/bundle.json --release-tag "$tag"
        manifest_sha=$(sha256sum /tmp/driver-product/bundle.json | cut -d " " -f 1)
        python3 /support/lib/materialize_driver_product.py \
            --manifest /tmp/driver-product/bundle.json --asset-dir /tmp/driver-product \
            --expected-manifest-sha256 "$manifest_sha" --expected-core-commit "$support_revision" \
            --expected-steamos "$1" --expected-kernel "$2" --expected-nvidia "$3" \
            --expected-architecture x86_64 --output-dir /output > /output/materialization.json
        # The raw success receipt names the temporary raw product; export only
        # the validated materialized product, not that stale receipt.
        rm -- /output/build-result.json
    ' -- "$STEAMOS" "$KERNEL" "$NVIDIA" "$SOURCE_COMMIT"
# The caller consumes the four-file product and canonical materialization only after validation.
mv -T --no-clobber "$WORK/output" "$OUTPUT"
[[ ! -d "$WORK/output" ]] || die "Build output appeared concurrently; refusing replacement."
