#!/usr/bin/env python3
"""Maintainer-only, non-mutating Arch snapshot userspace closure audit."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
KEYRING_PROVENANCE = ROOT / "trust/arch-full-keyring-provenance.json"
SNAPSHOT_MANIFEST = ROOT / "trust/arch-snapshot-2025-08-01.json"
sys.path.insert(0, str(ROOT / "lib"))
from validate_install_inputs import (  # noqa: E402
    dependency_name, package_metadata, pacman_desc_fields, record_satisfies,
    require_safe_destination,
)
from bsdtar_safety import ArchiveSafetyError, extract_confined, extract_single_member
from atomic_output import atomic_create_bytes

MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024
MAX_REPOSITORY_MEMBERS = 200_000
MAX_REPOSITORY_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_REPOSITORY_RECORDS = 100_000
MAX_REPOSITORY_RECORD_BYTES = 1024 * 1024
MAX_REPOSITORY_RELATIONS = 256
MAX_KEYRING_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_INSTALLED_BYTES = 16 * 1024 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
REPOS = ("core", "extra", "multilib")


def args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--snapshot-url", required=True)
    parser.add_argument("--full-keyring", required=True, type=Path)
    parser.add_argument("--keyring-source", required=True, type=Path)
    parser.add_argument("--keyring-source-signature", required=True, type=Path)
    parser.add_argument("--nvidia-utils", required=True, type=Path)
    parser.add_argument("--nvidia-utils-signature", required=True, type=Path)
    parser.add_argument("--lib32-nvidia-utils", required=True, type=Path)
    parser.add_argument("--lib32-nvidia-utils-signature", required=True, type=Path)
    parser.add_argument("--steamos", required=True)
    parser.add_argument("--nvidia", required=True)
    parser.add_argument("--architecture", default="x86_64", choices=("x86_64",))
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular(path, description, maximum):
    try:
        if (path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size <= maximum):
            raise OSError
    except OSError as error:
        raise SystemExit(f"{description} is unsafe, absent, or excessive") from error


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "steamos-nvidia-closure-audit/1"})
    if destination.exists() or destination.is_symlink():
        raise ValueError("audit download destination already exists")
    total = 0
    descriptor = None
    staged = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.geturl() != url:
                raise ValueError("audit download redirected unexpectedly")
            descriptor, staged_name = tempfile.mkstemp(
                prefix=f".{destination.name}.part-", dir=destination.parent
            )
            staged = Path(staged_name)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise ValueError("download exceeds audit size limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        os.link(staged, destination)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if staged is not None:
            staged.unlink(missing_ok=True)


def repository_file_url(base, filename):
    return base.rstrip("/") + "/" + urllib.parse.quote(filename, safe="@._+-")


def verify(path, signature, keyring):
    completed = subprocess.run(
        ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(signature), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise ValueError(f"cryptographic signature verification failed: {path.name}")
    fingerprints = [
        line.split()[2].upper() for line in completed.stdout.splitlines()
        if len(line.split()) >= 3 and line.split()[1] == "VALIDSIG"
    ]
    if len(fingerprints) != 1 or not re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", fingerprints[0]):
        raise ValueError(f"signature has no unique full fingerprint: {path.name}")
    return fingerprints[0]


def percent_fields(text):
    lines = text.splitlines()
    fields = {}
    index = 0
    while index < len(lines):
        marker = lines[index]
        if re.fullmatch(r"%[A-Z0-9_]+%", marker):
            field_name = marker.strip("%")
            if field_name in fields:
                raise ValueError(f"duplicate repository metadata field: {field_name}")
            values = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"%[A-Z0-9_]+%", lines[index]):
                if lines[index]:
                    values.append(lines[index])
                index += 1
            fields[field_name] = values
        else:
            index += 1
    return fields


def repository_records(database, repository):
    with tempfile.TemporaryDirectory(prefix="arch-repo-db-") as temporary:
        extract_confined(
            database, Path(temporary), max_members=MAX_REPOSITORY_MEMBERS,
            max_expanded_bytes=MAX_REPOSITORY_EXPANDED, allow_empty=True,
        )
        records = []
        identities = set()
        descriptions = sorted(Path(temporary).glob("*/desc"))
        if len(descriptions) > MAX_REPOSITORY_RECORDS:
            raise ValueError(f"authenticated {repository} repository has too many records")
        for desc in descriptions:
            if (desc.is_symlink() or not desc.is_file()
                    or desc.stat().st_size > MAX_REPOSITORY_RECORD_BYTES):
                raise ValueError(f"unsafe authenticated {repository} repository record")
            fields = percent_fields(desc.read_text(encoding="utf-8"))
            required = {key: fields.get(key, []) for key in ("NAME", "VERSION", "FILENAME", "SHA256SUM")}
            if any(len(values) != 1 for values in required.values()):
                raise ValueError(f"malformed authenticated {repository} repository record")
            filename = required["FILENAME"][0]
            name = required["NAME"][0]
            version = required["VERSION"][0]
            package_hash = required["SHA256SUM"][0].lower()
            dependencies = fields.get("DEPENDS", [])
            provides = fields.get("PROVIDES", [])
            if (not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", name)
                    or not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", version)
                    or not re.fullmatch(r"[0-9a-f]{64}", package_hash)
                    or len(dependencies) > MAX_REPOSITORY_RELATIONS
                    or len(provides) > MAX_REPOSITORY_RELATIONS
                    or any(not re.fullmatch(r"[A-Za-z0-9@._+<>=:-]{1,256}", value)
                           for value in [*dependencies, *provides])
                    or Path(filename).name != filename or filename in (".", "..")
                    or not re.fullmatch(r"[A-Za-z0-9@._+:-]+", filename)):
                raise ValueError(f"unsafe authenticated {repository} repository record")
            identity = (name, version, filename)
            if identity in identities:
                raise ValueError(f"duplicate authenticated {repository} repository record")
            identities.add(identity)
            records.append({
                "name": name, "version": version,
                "filename": filename, "sha256": package_hash,
                "depends": dependencies, "provides": provides,
                "repository": repository,
            })
        return records


def installed_records(root):
    root = root.resolve(strict=True)
    if root == Path("/"):
        raise ValueError("refusing appliance root")
    require_safe_destination(root, "usr/lib/holo/pacmandb/local")
    database = root / "usr/lib/holo/pacmandb"
    local = database / "local"
    resolved_database = database.resolve(strict=True)
    resolved_database.relative_to(root)
    if database.is_symlink() or local.is_symlink() or not local.is_dir():
        raise ValueError("Holo database is not a confined directory")
    records = {}
    for entry in local.iterdir():
        if entry.name == "ALPM_DB_VERSION" and entry.is_file() and not entry.is_symlink():
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("unexpected Holo local database entry")
        desc = entry / "desc"
        desc.resolve(strict=True).relative_to(resolved_database)
        if desc.is_symlink() or not desc.is_file():
            raise ValueError("Holo package record is not confined")
        record = pacman_desc_fields(desc)
        record["source"] = "installed"
        if record["name"] in records:
            raise ValueError(f"duplicate installed identity: {record['name']}")
        records[record["name"]] = record
    return records


def reviewed_policy():
    policy = json.loads((ROOT / "trust/nvidia-userspace-package-signers.json").read_text())
    return {
        (package, signer["fingerprint"])
        for signer in policy["signers"] if signer["status"] == "active"
        for package in signer["packages"]
    }


def package_record(package, signature, keyring, expected_sha=None):
    if expected_sha and sha256(package) != expected_sha:
        raise ValueError(f"repository hash mismatch: {package.name}")
    signer = verify(package, signature, keyring)
    metadata = package_metadata(package)  # Only after full-keyring verification.
    size = metadata.get("size", "")
    name = metadata.get("pkgname", "")
    version = metadata.get("pkgver", "")
    architecture = metadata.get("arch", "")
    dependencies = metadata.get("depends", [])
    provides = metadata.get("provides", [])
    if (not size.isdigit() or int(size) > MAX_PACKAGE_INSTALLED_BYTES
            or not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", name)
            or not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", version)
            or not re.fullmatch(r"[A-Za-z0-9@._+:-]{1,256}", architecture)
            or len(dependencies) > MAX_REPOSITORY_RELATIONS
            or len(provides) > MAX_REPOSITORY_RELATIONS
            or any(not re.fullmatch(r"[A-Za-z0-9@._+<>=:-]{1,256}", value)
                   for value in [*dependencies, *provides])):
        raise ValueError(f"package has unsafe or incomplete metadata: {package.name}")
    return {
        "name": name, "filename": package.name,
        "signatureFilename": signature.name, "version": version,
        "architecture": architecture, "packageSha256": sha256(package),
        "signatureSha256": sha256(signature), "signerFingerprint": signer,
        "installedSize": int(size), "dependencies": dependencies,
        "provides": provides,
    }


def main():
    options = args()
    for path, description, maximum in (
        (options.full_keyring, "full keyring", MAX_KEYRING_BYTES),
        (options.keyring_source, "keyring source package", MAX_DOWNLOAD),
        (options.keyring_source_signature, "keyring source signature", MAX_SIGNATURE_BYTES),
        (options.nvidia_utils, "nvidia-utils package", MAX_DOWNLOAD),
        (options.nvidia_utils_signature, "nvidia-utils signature", MAX_SIGNATURE_BYTES),
        (options.lib32_nvidia_utils, "lib32-nvidia-utils package", MAX_DOWNLOAD),
        (options.lib32_nvidia_utils_signature, "lib32-nvidia-utils signature", MAX_SIGNATURE_BYTES),
    ):
        require_regular(path, description, maximum)
    if not re.fullmatch(r"[0-9]{4}/[0-9]{2}/[0-9]{2}", options.snapshot):
        raise SystemExit("snapshot must be YYYY/MM/DD")
    expected_url = f"https://archive.archlinux.org/repos/{options.snapshot}/"
    if (os.environ.get("PROJECT_TEST_MODE") != "1"
            and options.snapshot_url.rstrip("/") + "/" != expected_url):
        raise SystemExit("snapshot URL must be the matching Arch Linux Archive date")
    manifest_url = expected_url
    if os.environ.get("PROJECT_TEST_MODE") == "1":
        manifest_url = options.snapshot_url.rstrip("/") + "/"
    snapshot_manifest_path = SNAPSHOT_MANIFEST
    if os.environ.get("PROJECT_TEST_MODE") == "1" and os.environ.get("PROJECT_TEST_SNAPSHOT_MANIFEST"):
        snapshot_manifest_path = Path(os.environ["PROJECT_TEST_SNAPSHOT_MANIFEST"])
    require_regular(snapshot_manifest_path, "snapshot provenance manifest", MAX_MANIFEST_BYTES)
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    if (snapshot_manifest.get("schemaVersion") != 1
            or snapshot_manifest.get("identity") != options.snapshot
            or snapshot_manifest.get("url") != manifest_url
            or set(snapshot_manifest.get("databases", {})) != set(REPOS)):
        raise SystemExit("snapshot is absent from support-owned provenance")
    provenance_path = KEYRING_PROVENANCE
    if os.environ.get("PROJECT_TEST_MODE") == "1" and os.environ.get("PROJECT_TEST_KEYRING_PROVENANCE"):
        provenance_path = Path(os.environ["PROJECT_TEST_KEYRING_PROVENANCE"])
    require_regular(provenance_path, "keyring provenance manifest", MAX_MANIFEST_BYTES)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if (provenance.get("schemaVersion") != 1
            or provenance.get("snapshot") != options.snapshot
            or options.keyring_source.name != provenance["source"]["package"]
            or options.keyring_source_signature.name != provenance["source"]["signature"]
            or sha256(options.keyring_source) != provenance["source"]["sha256"]
            or sha256(options.keyring_source_signature) != provenance["source"]["signatureSha256"]
            or sha256(options.full_keyring) != provenance["keyring"]["sha256"]):
        raise SystemExit("full keyring does not match support-owned provenance")
    try:
        keyring_payload = extract_single_member(
            options.keyring_source, provenance["keyring"]["path"],
            maximum=MAX_KEYRING_BYTES,
        )
    except ArchiveSafetyError as error:
        raise SystemExit(f"authenticated keyring source archive is unsafe: {error}")
    if hashlib.sha256(keyring_payload).hexdigest() != provenance["keyring"]["sha256"]:
        raise SystemExit("source package does not produce the pinned full keyring")
    if options.stage.is_symlink():
        raise SystemExit("stage directory must not be a symlink")
    options.stage.mkdir(parents=True, exist_ok=True)
    if not options.stage.is_dir() or options.stage.is_symlink():
        raise SystemExit("stage directory is unsafe")
    if any(options.stage.iterdir()):
        raise SystemExit("stage directory must be empty")
    verification_keyring = options.stage / "authenticated-full-arch-keyring.gpg"
    subprocess.run(
        ["gpg", "--batch", "--yes", "--dearmor", "--output",
         str(verification_keyring), str(options.full_keyring)],
        check=True,
    )
    base = options.snapshot_url.rstrip("/") + "/"
    repo_records = []
    repository_locks = []
    for repository in REPOS:
        repo_base = urllib.parse.urljoin(base, f"{repository}/os/x86_64/")
        database = options.stage / f"{repository}.db"
        download(urllib.parse.urljoin(repo_base, f"{repository}.db"), database)
        database_sha = sha256(database)
        if database_sha != snapshot_manifest["databases"][repository]:
            raise ValueError(f"repository database provenance mismatch: {repository}")
        repository_locks.append({
            "repository": repository, "databaseSha256": database_sha,
            "provenanceManifest": snapshot_manifest_path.name,
        })
        repo_records.extend(repository_records(database, repository))
    packages = []
    for package, signature, expected_name in (
        (options.nvidia_utils, options.nvidia_utils_signature, "nvidia-utils"),
        (options.lib32_nvidia_utils, options.lib32_nvidia_utils_signature, "lib32-nvidia-utils"),
    ):
        seed = package_record(package, signature, verification_keyring)
        if (seed["name"] != expected_name or seed["architecture"] != "x86_64"
                or seed["version"].rsplit("-", 1)[0] != options.nvidia):
            raise ValueError(f"seed package does not match requested identity: {expected_name}")
        packages.append(seed)
    installed = installed_records(options.root)
    providers = {}
    for record in repo_records:
        providers.setdefault(record["name"], []).append(record)
        for provided in record["provides"]:
            providers.setdefault(dependency_name(provided), []).append(record)
    pending = list(packages)
    seen = set()
    while pending:
        record = pending.pop()
        if record["name"] in seen:
            continue
        seen.add(record["name"])
        for dependency in record["dependencies"]:
            if any(record_satisfies(candidate, dependency) for candidate in installed.values()):
                continue
            if any(record_satisfies(candidate, dependency) for candidate in packages):
                continue
            matches = [candidate for candidate in providers.get(dependency_name(dependency), [])
                       if record_satisfies(candidate, dependency)]
            if not matches:
                raise ValueError(f"snapshot cannot satisfy dependency: {dependency}")
            exact = [candidate for candidate in matches if candidate["name"] == dependency_name(dependency)]
            selected = sorted(exact or matches, key=lambda item: (item["repository"], item["name"]))[0]
            repo_base = urllib.parse.urljoin(base, f"{selected['repository']}/os/x86_64/")
            package = options.stage / selected["filename"]
            signature = options.stage / f"{selected['filename']}.sig"
            download(repository_file_url(repo_base, selected["filename"]), package)
            download(repository_file_url(repo_base, f"{selected['filename']}.sig"), signature)
            locked = package_record(package, signature, verification_keyring, selected["sha256"])
            packages.append(locked)
            pending.append(locked)
    reviewed = reviewed_policy()
    missing_review = [
        {"packageName": package["name"], "signerFingerprint": package["signerFingerprint"]}
        for package in packages
        if (package["name"], package["signerFingerprint"]) not in reviewed
    ]
    document = {
        "schemaVersion": 1, "status": "candidate",
        "target": {"steamosVersion": options.steamos, "nvidiaVersion": options.nvidia,
                   "architecture": options.architecture},
        "snapshot": {"identity": options.snapshot, "url": base,
                     "repositories": repository_locks},
        "keyring": {"filename": options.full_keyring.name,
                    "sha256": provenance["keyring"]["sha256"],
                    "verificationKeyringSha256": sha256(verification_keyring),
                    "provenance": {"manifest": provenance_path.name,
                                   "manifestSha256": sha256(provenance_path),
                                   "source": provenance["source"]["package"],
                                   "sourceSha256": provenance["source"]["sha256"],
                                   "reviewedAt": provenance["reviewedAt"]}},
        "packages": sorted(packages, key=lambda item: item["name"]),
        "missingReview": sorted(missing_review, key=lambda item: item["packageName"]),
    }
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    try:
        atomic_create_bytes(options.output, payload)
    except FileExistsError:
        raise SystemExit("candidate lock output already exists")


if __name__ == "__main__":
    try:
        main()
    except (ArchiveSafetyError, KeyError, OSError, TypeError, UnicodeError,
            ValueError, json.JSONDecodeError, subprocess.SubprocessError,
            urllib.error.URLError) as error:
        raise SystemExit(f"userspace closure audit failed: {error}") from None
