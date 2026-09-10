#!/usr/bin/env python3
"""Create or validate a canonical compiled-driver GitHub Release bundle manifest."""

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path


MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
HEX = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}")


def fail(message):
    raise SystemExit(message)


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path, maximum):
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and 0 < path.stat().st_size <= maximum
        )
    except OSError:
        return False


def strict_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        fail("Bundle manifest is not strict JSON.")


def product_manifest(archive):
    try:
        with tarfile.open(archive, "r:gz") as product:
            names = product.getnames()
            expected = [
                "DRIVER-MANIFEST.json",
                "payload/nvidia-driver.tar.zst",
                "metadata/BUILD-INFO.txt",
                "metadata/PROVENANCE.json",
                "metadata/VALIDATION-RECEIPT.json",
            ]
            if names != expected:
                fail("Compiled-driver product member order is not canonical.")
            member = product.getmember("DRIVER-MANIFEST.json")
            if not member.isfile() or member.size <= 0 or member.size > MAX_MANIFEST_BYTES:
                fail("Compiled-driver product manifest is invalid.")
            stream = product.extractfile(member)
            if stream is None:
                fail("Compiled-driver product manifest is unreadable.")
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
            try:
                document = strict_json(payload.decode("utf-8"))
            except UnicodeError:
                fail("Compiled-driver product manifest is not UTF-8.")
            if not isinstance(document, dict) or canonical(document) != payload:
                fail("Compiled-driver product manifest is not canonical JSON.")
            metadata = document.get("metadata")
            if not isinstance(metadata, list):
                fail("Compiled-driver product inventory is not canonical.")
            records = [document.get("payload")] + metadata
            if (
                not isinstance(records[0], dict)
                or [record.get("path") for record in records if isinstance(record, dict)]
                != expected[1:]
            ):
                fail("Compiled-driver product inventory is not canonical.")
            for name, record in zip(expected[1:], records):
                asset = product.getmember(name)
                if (
                    not asset.isfile()
                    or not isinstance(record, dict)
                    or asset.size != record.get("bytes")
                    or not 1 <= asset.size <= MAX_ASSET_BYTES
                ):
                    fail("Compiled-driver product member size is invalid.")
                content = product.extractfile(asset)
                if content is None:
                    fail("Compiled-driver product member is unreadable.")
                digest = hashlib.sha256()
                for chunk in iter(lambda: content.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != record.get("sha256"):
                    fail("Compiled-driver product member hash is invalid.")
    except (OSError, tarfile.TarError, KeyError):
        fail("Compiled-driver product is unreadable.")
    return document


def validate_document(document):
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion", "kind", "contract", "release", "core", "source",
        "target", "compatibility", "assets",
    }:
        fail("Bundle manifest fields are not canonical.")
    if (
        document["schemaVersion"] != 1
        or document["kind"] != "opemos-driver-binary-bundle"
        or document["contract"] != {
            "driverProductManifestSchemaVersion": 1,
            "releaseBundleManifestSchemaVersion": 1,
        }
    ):
        fail("Bundle manifest schema identity is unsupported.")
    release = document["release"]
    if (
        not isinstance(release, dict)
        or set(release) != {"repository", "tag"}
        or REPOSITORY.fullmatch(release.get("repository", "")) is None
        or not isinstance(release.get("tag"), str)
        or not 1 <= len(release["tag"]) <= 255
    ):
        fail("Bundle release identity is invalid.")
    core = document["core"]
    source = document["source"]
    for identity, label in ((core, "Core"), (source, "source")):
        if (
            not isinstance(identity, dict)
            or set(identity) != {"repository", "commit"}
            or REPOSITORY.fullmatch(identity.get("repository", "")) is None
            or COMMIT.fullmatch(identity.get("commit", "")) is None
        ):
            fail(f"Bundle {label} identity is invalid.")
    target = document["target"]
    if (
        not isinstance(target, dict)
        or set(target) != {
            "steamosVersion", "kernelVersion", "nvidiaVersion", "architecture"
        }
        or target.get("architecture") != "x86_64"
        or any(not isinstance(target.get(field), str) or not target[field]
               for field in ("steamosVersion", "kernelVersion", "nvidiaVersion"))
    ):
        fail("Bundle target identity is invalid.")
    if document["compatibility"] != {
        "architecture": "exact", "fallback": False, "kernel": "exact"
    }:
        fail("Bundle compatibility policy is invalid.")
    assets = document["assets"]
    if not isinstance(assets, list) or len(assets) != 2:
        fail("Bundle asset inventory is invalid.")
    if [asset.get("role") for asset in assets if isinstance(asset, dict)] != [
        "driver-product", "sha256-sidecar"
    ]:
        fail("Bundle asset order is invalid.")
    names = []
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or set(asset) != {"role", "name", "bytes", "sha256"}
            or ASSET.fullmatch(asset.get("name", "")) is None
            or not isinstance(asset.get("bytes"), int)
            or isinstance(asset["bytes"], bool)
            or not 1 <= asset["bytes"] <= MAX_ASSET_BYTES
            or HEX.fullmatch(asset.get("sha256", "")) is None
        ):
            fail("Bundle asset record is invalid.")
        names.append(asset["name"])
    if len(set(names)) != 2 or names[1] != names[0] + ".sha256":
        fail("Bundle asset names are inconsistent.")
    return document


def bind_product(document, product):
    if (
        product.get("schemaVersion") != 1
        or product.get("kind") != "opemos-compiled-driver"
        or product.get("creator", {}).get("repository") != document["core"]["repository"]
        or product.get("creator", {}).get("commit") != document["core"]["commit"]
        or product.get("source") != document["source"]
        or product.get("target") != document["target"]
        or product.get("compatibility") != document["compatibility"]
        or product.get("releaseTag") != document["release"]["tag"]
    ):
        fail("Bundle manifest does not match its compiled-driver product.")


def validate_assets(document, asset_dir):
    for record in document["assets"]:
        path = asset_dir / record["name"]
        if not regular(path, MAX_ASSET_BYTES):
            fail("Bundle asset is missing, linked, empty, or excessive.")
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            fail("Bundle asset size or hash does not match.")
    archive = asset_dir / document["assets"][0]["name"]
    sidecar = asset_dir / document["assets"][1]["name"]
    try:
        fields = sidecar.read_text(encoding="utf-8").split()
    except (OSError, UnicodeError):
        fail("Bundle checksum sidecar is unreadable.")
    if fields != [document["assets"][0]["sha256"], archive.name]:
        fail("Bundle checksum sidecar is not canonical.")
    bind_product(document, product_manifest(archive))


def create(args):
    if not regular(args.archive, MAX_ASSET_BYTES) or not regular(
        args.sidecar, MAX_MANIFEST_BYTES
    ):
        fail("Bundle inputs must be bounded regular files.")
    product = product_manifest(args.archive)
    creator = product.get("creator", {})
    source = product.get("source")
    target = product.get("target")
    compatibility = product.get("compatibility")
    tag = product.get("releaseTag")
    if (
        creator.get("component") != "OPEMOS Core"
        or creator.get("cleanupOwner") != "core"
        or creator.get("repository") != args.release_repository
        or not isinstance(source, dict)
        or not isinstance(target, dict)
        or compatibility != {"architecture": "exact", "fallback": False, "kernel": "exact"}
        or tag != args.release_tag
    ):
        fail("Compiled-driver product cannot be bound to this release.")
    document = {
        "schemaVersion": 1,
        "kind": "opemos-driver-binary-bundle",
        "contract": {
            "driverProductManifestSchemaVersion": 1,
            "releaseBundleManifestSchemaVersion": 1,
        },
        "release": {"repository": args.release_repository, "tag": args.release_tag},
        "core": {
            "repository": creator["repository"],
            "commit": creator["commit"],
        },
        "source": source,
        "target": target,
        "compatibility": compatibility,
        "assets": [
            {
                "role": "driver-product",
                "name": args.archive.name,
                "bytes": args.archive.stat().st_size,
                "sha256": sha256(args.archive),
            },
            {
                "role": "sha256-sidecar",
                "name": args.sidecar.name,
                "bytes": args.sidecar.stat().st_size,
                "sha256": sha256(args.sidecar),
            },
        ],
    }
    validate_document(document)
    bind_product(document, product)
    try:
        fields = args.sidecar.read_text(encoding="utf-8").split()
    except (OSError, UnicodeError):
        fail("Bundle checksum sidecar is unreadable.")
    if fields != [document["assets"][0]["sha256"], args.archive.name]:
        fail("Bundle checksum sidecar is not canonical.")
    payload = canonical(document)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() or args.output.is_symlink():
        fail("Bundle manifest output already exists.")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".driver-bundle-manifest.", dir=args.output.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, args.output)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    print(json.dumps({
        "schemaVersion": 1,
        "status": "created",
        "manifest": str(args.output),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }, sort_keys=True, separators=(",", ":")))


def validate(args):
    if not regular(args.manifest, MAX_MANIFEST_BYTES):
        fail("Bundle manifest is missing, linked, empty, or excessive.")
    payload = args.manifest.read_bytes()
    if sha256(args.manifest) != args.expected_manifest_sha256:
        fail("Bundle manifest does not match the pinned SHA-256.")
    try:
        document = validate_document(strict_json(payload.decode("utf-8")))
    except UnicodeError:
        fail("Bundle manifest is not UTF-8.")
    if canonical(document) != payload:
        fail("Bundle manifest is not canonical JSON.")
    if document["core"]["commit"] != args.expected_core_commit:
        fail("Bundle manifest does not match the pinned Core commit.")
    validate_assets(document, args.asset_dir)
    print(json.dumps({
        "schemaVersion": 1,
        "status": "validated",
        "coreCommit": document["core"]["commit"],
        "release": document["release"],
        "assets": document["assets"],
    }, sort_keys=True, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    producer = commands.add_parser("create")
    producer.add_argument("--archive", required=True, type=Path)
    producer.add_argument("--sidecar", required=True, type=Path)
    producer.add_argument("--output", required=True, type=Path)
    producer.add_argument("--release-repository", default="CorniiDog/OPEMOS")
    producer.add_argument("--release-tag", required=True)
    producer.set_defaults(handler=create)
    checker = commands.add_parser("validate")
    checker.add_argument("--manifest", required=True, type=Path)
    checker.add_argument("--asset-dir", required=True, type=Path)
    checker.add_argument("--expected-manifest-sha256", required=True)
    checker.add_argument("--expected-core-commit", required=True)
    checker.set_defaults(handler=validate)
    args = parser.parse_args()
    if args.command == "validate":
        if HEX.fullmatch(args.expected_manifest_sha256) is None:
            fail("Pinned manifest SHA-256 is invalid.")
        if COMMIT.fullmatch(args.expected_core_commit) is None:
            fail("Pinned Core commit is invalid.")
    args.handler(args)


if __name__ == "__main__":
    main()
