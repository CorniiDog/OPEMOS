#!/usr/bin/env python3
"""Validate a compiled-driver product and materialize its installer inputs."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import driver_binary_bundle


MAX_METADATA_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
EXPECTED_MODULES = {
    "nvidia.ko", "nvidia-drm.ko", "nvidia-modeset.ko", "nvidia-peermem.ko",
    "nvidia-uvm.ko",
}
GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def fail(message):
    raise SystemExit(message)


def digest_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def bounded_member(archive, name, maximum):
    try:
        member = archive.getmember(name)
    except KeyError:
        fail(f"Compiled-driver product lacks {name}.")
    if not member.isfile() or not 0 < member.size <= maximum:
        fail(f"Compiled-driver product has an invalid {name}.")
    stream = archive.extractfile(member)
    if stream is None:
        fail(f"Compiled-driver product has an unreadable {name}.")
    payload = stream.read(maximum + 1)
    if len(payload) != member.size:
        fail(f"Compiled-driver product has a truncated {name}.")
    return payload


def strict_document(payload, label):
    try:
        document = driver_binary_bundle.strict_json(payload.decode("utf-8"))
    except UnicodeError:
        fail(f"{label} is not UTF-8.")
    if not isinstance(document, dict):
        fail(f"{label} is malformed.")
    return document


def normalized_member(name):
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        fail("Installer payload contains an unsafe member path.")
    return str(path)


def build_information(payload):
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError:
        fail("Compiled-driver build information is not UTF-8.")
    result = {}
    for line in lines:
        if "=" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split("=", 1)
        if key in result:
            fail("Compiled-driver build information contains a duplicate field.")
        result[key] = value
    return result


def validate_product_contract(
        product, bundle, expected_target, provenance, provenance_bytes, receipt):
    expected_fields = {
        "schemaVersion", "kind", "creator", "target", "compatibility",
        "capabilities", "payload", "metadata", "install", "source", "releaseTag",
    }
    if set(product) != expected_fields:
        fail("Compiled-driver product fields are not canonical.")
    creator = product.get("creator")
    if creator != {
        "component": "OPEMOS Core",
        "repository": bundle["core"]["repository"],
        "commit": bundle["core"]["commit"],
        "cleanupOwner": "core",
    }:
        fail("Compiled-driver product creator does not match the pinned Core identity.")
    if product.get("schemaVersion") != 1 or product.get("kind") != "opemos-compiled-driver":
        fail("Compiled-driver product schema identity is unsupported.")
    if product.get("target") != expected_target or bundle.get("target") != expected_target:
        fail("Compiled-driver product does not match the exact requested target.")
    if product.get("source") != bundle.get("source"):
        fail("Compiled-driver product source does not match its release bundle.")
    if product.get("releaseTag") != bundle.get("release", {}).get("tag"):
        fail("Compiled-driver product release identity does not match its bundle.")
    if product.get("compatibility") != {
        "architecture": "exact", "fallback": False, "kernel": "exact"
    } or product.get("capabilities") != [
        "open-kernel-modules", "offline-install", "initramfs-integration"
    ]:
        fail("Compiled-driver product compatibility or capabilities are unsupported.")
    kernel = expected_target["kernelVersion"]
    if product.get("install") != {
        "moduleDestination": f"/usr/lib/modules/{kernel}/updates/nvidia",
        "runDepmod": True,
        "initramfs": {"required": True, "kernelVersion": kernel},
    }:
        fail("Compiled-driver product install contract does not match the exact target.")

    metadata = product.get("metadata")
    expected_metadata = [
        ("build-info", "metadata/BUILD-INFO.txt"),
        ("provenance", "metadata/PROVENANCE.json"),
        ("validation-receipt", "metadata/VALIDATION-RECEIPT.json"),
    ]
    if (not isinstance(metadata, list) or len(metadata) != len(expected_metadata)
            or [(record.get("role"), record.get("path"))
                for record in metadata if isinstance(record, dict)] != expected_metadata):
        fail("Compiled-driver product metadata inventory is not canonical.")

    payload = product.get("payload")
    if (not isinstance(payload, dict)
            or set(payload) != {"path", "sourceName", "bytes", "sha256"}
            or payload.get("path") != "payload/nvidia-driver.tar.zst"
            or not isinstance(payload.get("sourceName"), str)
            or Path(payload["sourceName"]).name != payload["sourceName"]
            or driver_binary_bundle.ASSET.fullmatch(payload["sourceName"]) is None
            or payload["sourceName"] !=
            f"nvidia-open-{product.get('releaseTag')}-x86_64.tar.gz"):
        fail("Compiled-driver payload representation is unsupported.")

    if provenance.get("schemaVersion") != 1:
        fail("Compiled-driver provenance schema is unsupported.")
    if provenance.get("target") != expected_target:
        fail("Compiled-driver provenance does not match the exact requested target.")
    support = provenance.get("support")
    source = provenance.get("source")
    artifact = provenance.get("artifact")
    repack = provenance.get("repack")
    if (not isinstance(support, dict)
            or support.get("repository") != creator["repository"]
            or support.get("commit") != creator["commit"]
            or str(support.get("dirty")).lower() not in ("0", "false")):
        fail("Compiled-driver provenance does not match the pinned Core identity.")
    if (not isinstance(source, dict)
            or {key: source.get(key) for key in ("repository", "commit")} != product["source"]
            or str(source.get("dirty")).lower() not in ("0", "false")):
        fail("Compiled-driver provenance source identity is invalid.")
    if (not isinstance(artifact, dict)
            or artifact.get("archive") != payload["sourceName"]
            or artifact.get("releaseTag") != product["releaseTag"]
            or artifact.get("representation") != "ko.zst"
            or not isinstance(artifact.get("revision"), int)
            or isinstance(artifact["revision"], bool)
            or not 1 <= artifact["revision"] <= 999):
        fail("Compiled-driver provenance representation is invalid.")
    representation_suffix = f"-modules-zstd-r{artifact['revision']}"
    if not product["releaseTag"].endswith(representation_suffix):
        fail("Compiled-driver provenance release representation is invalid.")
    base_tag = product["releaseTag"].removesuffix(representation_suffix)
    if (provenance.get("trust") not in ("locally-built-verified", "certified-published")
            or not isinstance(repack, dict)
            or repack.get("schemaVersion") != 1
            or repack.get("sourceReleaseTag") != base_tag
            or repack.get("payloadIdentity") != "byte-identical"
            or repack.get("encoding") != "zstd-19-t1"
            or repack.get("encoder") != {"name": "zstd", "version": "1.5.7"}
            or re.fullmatch(r"[0-9a-f]{64}", repack.get("sourceArchiveSha256", "")) is None
            or re.fullmatch(r"[0-9a-f]{64}", repack.get("sourceProvenanceSha256", "")) is None):
        fail("Compiled-driver provenance repack identity is invalid.")
    if receipt != {
        "schemaVersion": 1,
        "status": "validated",
        "target": expected_target,
        "payloadSha256": payload["sha256"],
        "provenanceSha256": digest_bytes(provenance_bytes),
        "validator": {"repository": creator["repository"], "commit": creator["commit"]},
    }:
        fail("Compiled-driver validation receipt is not bound to the product.")
    return payload


def validate_build_information(payload, product, provenance):
    information = build_information(payload)
    expected = {
        "schema_version": "1",
        "steamos_version": product["target"]["steamosVersion"],
        "kernel_version": product["target"]["kernelVersion"],
        "nvidia_version": product["target"]["nvidiaVersion"],
        "build_architecture": product["target"]["architecture"],
        "trust_classification": provenance["trust"],
        "release_tag": product["releaseTag"],
        "release_asset": product["payload"]["sourceName"],
        "support_repository": product["creator"]["repository"],
        "support_commit": product["creator"]["commit"],
        "source_repository": product["source"]["repository"],
        "source_commit": product["source"]["commit"],
    }
    if any(information.get(key) != value for key, value in expected.items()):
        fail("Compiled-driver build information does not match product provenance.")


def validate_payload(payload_path, payload_record, provenance_bytes, build_info_bytes):
    if payload_path.stat().st_size != payload_record["bytes"]:
        fail("Materialized installer payload size changed.")
    digest = driver_binary_bundle.sha256(payload_path)
    if digest != payload_record["sha256"]:
        fail("Materialized installer payload hash changed.")
    with payload_path.open("rb") as stream:
        if stream.read(2) != GZIP_MAGIC:
            fail("Compiled-driver payload is not the declared gzip installer archive.")
    try:
        with tarfile.open(payload_path, "r:gz") as archive:
            members = {}
            for member in archive:
                name = normalized_member(member.name)
                canonical = {name, name + "/"} if member.isdir() else {name}
                if member.name not in canonical or name in members:
                    fail("Installer payload member layout is not canonical.")
                members[name] = member
            expected_files = {"BUILD-INFO.txt", "PROVENANCE.json"} | {
                f"modules/{name}.zst" for name in EXPECTED_MODULES
            }
            if set(members) != expected_files | {"modules"}:
                fail("Installer payload does not contain the canonical compressed module set.")
            if not members["modules"].isdir() or any(
                    not members[name].isfile() for name in expected_files):
                fail("Installer payload contains a non-regular canonical member.")
            if (any(not 0 < members[name].size <= (
                    MAX_METADATA_BYTES if name in ("BUILD-INFO.txt", "PROVENANCE.json")
                    else MAX_PAYLOAD_BYTES) for name in expected_files)
                    or sum(member.size for member in members.values()) > MAX_TOTAL_MEMBER_BYTES):
                fail("Installer payload member size is invalid.")
            embedded_provenance = archive.extractfile(members["PROVENANCE.json"])
            embedded_build_info = archive.extractfile(members["BUILD-INFO.txt"])
            if (embedded_provenance is None or embedded_provenance.read() != provenance_bytes
                    or embedded_build_info is None or embedded_build_info.read() != build_info_bytes):
                fail("Installer payload metadata does not match the product metadata.")
            records = provenance_bytes_to_modules(provenance_bytes)
            for name in EXPECTED_MODULES:
                stream = archive.extractfile(members[f"modules/{name}.zst"])
                if stream is None or stream.read(4) != ZSTD_MAGIC:
                    fail("Installer payload contains a non-zstd module representation.")
                representation_hash = hashlib.sha256(ZSTD_MAGIC)
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    representation_hash.update(chunk)
                if representation_hash.hexdigest() != records[name]["sha256"]:
                    fail("Installer payload module representation does not match provenance.")
    except (OSError, tarfile.TarError, KeyError):
        fail("Compiled-driver payload is not a readable gzip installer archive.")
    return digest


def provenance_bytes_to_modules(payload, expected_target=None):
    document = strict_document(payload, "Compiled-driver provenance")
    records = document.get("modules")
    if not isinstance(records, list) or len(records) != len(EXPECTED_MODULES):
        fail("Compiled-driver provenance module inventory is invalid.")
    result = {}
    for record in records:
        if not isinstance(record, dict):
            fail("Compiled-driver provenance module inventory is invalid.")
        name = record.get("name")
        if (name not in EXPECTED_MODULES or name in result
                or record.get("representation") != "ko.zst"
                or record.get("representationFilename") != name + ".zst"
                or re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")) is None
                or re.fullmatch(r"[0-9a-f]{64}", record.get("payloadSha256", "")) is None):
            fail("Compiled-driver provenance module representation is invalid.")
        if expected_target is not None and (
                record.get("version") != expected_target["nvidiaVersion"]
                or record.get("architecture") != expected_target["architecture"]
                or not isinstance(record.get("vermagic"), str)
                or record["vermagic"].split(maxsplit=1)[0] != expected_target["kernelVersion"]):
            fail("Compiled-driver provenance module target is invalid.")
        result[name] = record
    return result


def stage_member(product_path, member_name, destination, maximum):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    staged = Path(temporary)
    try:
        with tarfile.open(product_path, "r:gz") as archive:
            member = archive.getmember(member_name)
            if not member.isfile() or not 0 < member.size <= maximum:
                fail("Compiled-driver payload size is invalid.")
            source = archive.extractfile(member)
            if source is None:
                fail("Compiled-driver payload is unreadable.")
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
        os.chmod(staged, 0o644)
        return staged
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise


def publish(staged, destination):
    try:
        os.link(staged, destination)
    except FileExistsError:
        fail("Installer output already exists.")
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset-dir", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-core-commit", required=True)
    parser.add_argument("--expected-steamos", required=True)
    parser.add_argument("--expected-kernel", required=True)
    parser.add_argument("--expected-nvidia", required=True)
    parser.add_argument("--expected-architecture", required=True, choices=("x86_64",))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if driver_binary_bundle.HEX.fullmatch(args.expected_manifest_sha256) is None:
        fail("Pinned manifest SHA-256 is invalid.")
    if driver_binary_bundle.COMMIT.fullmatch(args.expected_core_commit) is None:
        fail("Pinned Core commit is invalid.")
    if not driver_binary_bundle.regular(args.manifest, driver_binary_bundle.MAX_MANIFEST_BYTES):
        fail("Bundle manifest is missing, linked, empty, or excessive.")
    manifest_bytes = args.manifest.read_bytes()
    if digest_bytes(manifest_bytes) != args.expected_manifest_sha256:
        fail("Bundle manifest does not match the pinned SHA-256.")
    bundle = driver_binary_bundle.validate_document(
        strict_document(manifest_bytes, "Bundle manifest")
    )
    if driver_binary_bundle.canonical(bundle) != manifest_bytes:
        fail("Bundle manifest is not canonical JSON.")
    if bundle["core"]["commit"] != args.expected_core_commit:
        fail("Bundle manifest does not match the pinned Core commit.")
    driver_binary_bundle.validate_assets(bundle, args.asset_dir)

    expected_target = {
        "steamosVersion": args.expected_steamos,
        "kernelVersion": args.expected_kernel,
        "nvidiaVersion": args.expected_nvidia,
        "architecture": args.expected_architecture,
    }
    product_path = args.asset_dir / bundle["assets"][0]["name"]
    try:
        with tarfile.open(product_path, "r:gz") as archive:
            product_bytes = bounded_member(archive, "DRIVER-MANIFEST.json", MAX_METADATA_BYTES)
            provenance_bytes = bounded_member(archive, "metadata/PROVENANCE.json", MAX_METADATA_BYTES)
            build_info_bytes = bounded_member(archive, "metadata/BUILD-INFO.txt", MAX_METADATA_BYTES)
            receipt_bytes = bounded_member(archive, "metadata/VALIDATION-RECEIPT.json", MAX_METADATA_BYTES)
    except (OSError, tarfile.TarError):
        fail("Compiled-driver product is unreadable.")
    product = strict_document(product_bytes, "Compiled-driver product manifest")
    provenance = strict_document(provenance_bytes, "Compiled-driver provenance")
    receipt = strict_document(receipt_bytes, "Compiled-driver validation receipt")
    payload_record = validate_product_contract(
        product, bundle, expected_target, provenance, provenance_bytes, receipt
    )
    provenance_bytes_to_modules(provenance_bytes, expected_target)
    validate_build_information(build_info_bytes, product, provenance)

    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = payload_record["sourceName"].removesuffix(".tar.gz")
    destinations = {
        "archive": args.output_dir / payload_record["sourceName"],
        "checksum": args.output_dir / (payload_record["sourceName"] + ".sha256"),
        "provenance": args.output_dir / (stem + ".provenance.json"),
        "buildInfo": args.output_dir / (stem + ".build-info.txt"),
    }
    if any(path.exists() or path.is_symlink() for path in destinations.values()):
        fail("Installer output already exists.")

    staged_archive = stage_member(
        product_path, "payload/nvidia-driver.tar.zst", destinations["archive"], MAX_PAYLOAD_BYTES
    )
    try:
        payload_sha256 = validate_payload(
            staged_archive, payload_record, provenance_bytes, build_info_bytes
        )
        outputs = {"archive": staged_archive}
        try:
            outputs["checksum"] = write_staged(
                destinations["checksum"],
                f"{payload_sha256}  {destinations['archive'].name}\n".encode(),
            )
            outputs["provenance"] = write_staged(
                destinations["provenance"], provenance_bytes
            )
            outputs["buildInfo"] = write_staged(
                destinations["buildInfo"], build_info_bytes
            )
        except BaseException:
            for path in outputs.values():
                path.unlink(missing_ok=True)
            raise
        published = []
        try:
            for role in ("archive", "checksum", "provenance", "buildInfo"):
                publish(outputs[role], destinations[role])
                published.append(destinations[role])
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            for path in outputs.values():
                path.unlink(missing_ok=True)
            raise
    except BaseException:
        staged_archive.unlink(missing_ok=True)
        raise

    result = {
        "schemaVersion": 1,
        "status": "materialized",
        "target": expected_target,
        "core": bundle["core"],
        "source": bundle["source"],
        "release": bundle["release"],
        "representation": {
            "productMember": "payload/nvidia-driver.tar.zst",
            "installerContainer": "tar+gzip",
            "modules": "ko.zst",
            "conversion": "none-byte-identical",
        },
        "outputs": {
            role: {"name": path.name, "bytes": path.stat().st_size,
                   "sha256": driver_binary_bundle.sha256(path)}
            for role, path in destinations.items()
        },
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def write_staged(destination, payload):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o644)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
