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
from pathlib import Path

import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from validate_install_inputs import (  # noqa: E402
    dependency_name, package_metadata, pacman_desc_fields, record_satisfies,
)

MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024
REPOS = ("core", "extra", "multilib")


def args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--snapshot-url", required=True)
    parser.add_argument("--full-keyring", required=True, type=Path)
    parser.add_argument("--full-keyring-sha256", required=True)
    parser.add_argument("--keyring-source", required=True)
    parser.add_argument("--keyring-source-sha256", required=True)
    parser.add_argument("--keyring-reviewed-at", required=True)
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


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "steamos-nvidia-closure-audit/1"})
    staged = destination.with_name(f".{destination.name}.part-{os.getpid()}")
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, staged.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise ValueError("download exceeds audit size limit")
            output.write(chunk)
    staged.replace(destination)


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
            values = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"%[A-Z0-9_]+%", lines[index]):
                if lines[index]:
                    values.append(lines[index])
                index += 1
            fields[marker.strip("%")] = values
        else:
            index += 1
    return fields


def repository_records(database, repository):
    with tempfile.TemporaryDirectory(prefix="arch-repo-db-") as temporary:
        subprocess.run(["bsdtar", "-xf", str(database), "-C", temporary], check=True)
        records = []
        for desc in Path(temporary).glob("*/desc"):
            fields = percent_fields(desc.read_text(encoding="utf-8"))
            required = {key: fields.get(key, []) for key in ("NAME", "VERSION", "FILENAME", "SHA256SUM")}
            if any(len(values) != 1 for values in required.values()):
                raise ValueError(f"malformed authenticated {repository} repository record")
            records.append({
                "name": required["NAME"][0], "version": required["VERSION"][0],
                "filename": required["FILENAME"][0], "sha256": required["SHA256SUM"][0].lower(),
                "depends": fields.get("DEPENDS", []), "provides": fields.get("PROVIDES", []),
                "repository": repository,
            })
        return records


def installed_records(root):
    records = {}
    for desc in (root / "usr/lib/holo/pacmandb/local").glob("*/desc"):
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
    if not size.isdigit():
        raise ValueError(f"package lacks installed size: {package.name}")
    return {
        "name": metadata["pkgname"], "filename": package.name,
        "signatureFilename": signature.name, "version": metadata["pkgver"],
        "architecture": metadata["arch"], "packageSha256": sha256(package),
        "signatureSha256": sha256(signature), "signerFingerprint": signer,
        "installedSize": int(size), "dependencies": metadata["depends"],
        "provides": metadata["provides"],
    }


def main():
    options = args()
    if not re.fullmatch(r"[0-9]{4}/[0-9]{2}/[0-9]{2}", options.snapshot):
        raise SystemExit("snapshot must be YYYY/MM/DD")
    expected_url = f"https://archive.archlinux.org/repos/{options.snapshot}/"
    if (os.environ.get("PROJECT_TEST_MODE") != "1"
            and options.snapshot_url.rstrip("/") + "/" != expected_url):
        raise SystemExit("snapshot URL must be the matching Arch Linux Archive date")
    if (not re.fullmatch(r"[0-9a-fA-F]{64}", options.full_keyring_sha256)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", options.keyring_source_sha256)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", options.keyring_reviewed_at)):
        raise SystemExit("keyring provenance is malformed")
    if sha256(options.full_keyring) != options.full_keyring_sha256.lower():
        raise SystemExit("full keyring hash mismatch")
    options.stage.mkdir(parents=True, exist_ok=True)
    if any(options.stage.iterdir()):
        raise SystemExit("stage directory must be empty")
    base = options.snapshot_url.rstrip("/") + "/"
    repo_records = []
    repository_locks = []
    for repository in REPOS:
        repo_base = urllib.parse.urljoin(base, f"{repository}/os/x86_64/")
        database = options.stage / f"{repository}.db"
        signature = options.stage / f"{repository}.db.sig"
        download(urllib.parse.urljoin(repo_base, f"{repository}.db"), database)
        download(urllib.parse.urljoin(repo_base, f"{repository}.db.sig"), signature)
        signer = verify(database, signature, options.full_keyring)
        repository_locks.append({
            "repository": repository, "databaseSha256": sha256(database),
            "signatureSha256": sha256(signature), "signerFingerprint": signer,
        })
        repo_records.extend(repository_records(database, repository))
    packages = []
    for package, signature in (
        (options.nvidia_utils, options.nvidia_utils_signature),
        (options.lib32_nvidia_utils, options.lib32_nvidia_utils_signature),
    ):
        packages.append(package_record(package, signature, options.full_keyring))
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
            download(urllib.parse.urljoin(repo_base, selected["filename"]), package)
            download(urllib.parse.urljoin(repo_base, f"{selected['filename']}.sig"), signature)
            locked = package_record(package, signature, options.full_keyring, selected["sha256"])
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
                    "sha256": options.full_keyring_sha256.lower(),
                    "provenance": {"source": options.keyring_source,
                                   "sourceSha256": options.keyring_source_sha256.lower(),
                                   "reviewedAt": options.keyring_reviewed_at}},
        "packages": sorted(packages, key=lambda item: item["name"]),
        "missingReview": sorted(missing_review, key=lambda item: item["packageName"]),
    }
    options.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
