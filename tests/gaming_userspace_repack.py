#!/usr/bin/env python3
"""Synthetic contract tests for deterministic package-level CUDA omission."""

import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from repack_gaming_userspace import RepackError, materialize


def digest(value):
    return hashlib.sha256(value).hexdigest()


def add(archive, name, value, mode=0o644, link=None):
    member = tarfile.TarInfo(name)
    member.mtime = 1_700_000_000
    member.mode = mode
    member.uid = member.gid = 0
    if link is not None:
        member.type = tarfile.SYMTYPE
        member.linkname = link
        archive.addfile(member)
    else:
        member.size = len(value)
        archive.addfile(member, io.BytesIO(value))


def package(path, name, version, omitted_name):
    pkginfo = (f"pkgname = {name}\npkgver = {version}\narch = x86_64\n"
               "size = 12\ndepend = glibc\nprovides = graphics-driver\n").encode()
    buildinfo = f"pkgname = {name}\npkgver = {version}\n".encode()
    omitted = b"compute"
    preserved = b"video"
    mtree = gzip.compress((
        "#mtree\n/set type=file uid=0 gid=0 mode=644\n"
        f"./.BUILDINFO time=1700000000.0 size={len(buildinfo)} sha256digest={digest(buildinfo)}\n"
        f"./.PKGINFO time=1700000000.0 size={len(pkginfo)} sha256digest={digest(pkginfo)}\n"
        f"./{omitted_name} time=1700000000.0 size={len(omitted)} sha256digest={digest(omitted)}\n"
        f"./usr/lib/{name}/graphics.so time=1700000000.0 size={len(preserved)} sha256digest={digest(preserved)}\n"
    ).encode(), mtime=0)
    raw = path.with_suffix("")
    with tarfile.open(raw, "w:", format=tarfile.GNU_FORMAT) as archive:
        add(archive, ".BUILDINFO", buildinfo)
        add(archive, ".MTREE", mtree)
        add(archive, ".PKGINFO", pkginfo)
        add(archive, omitted_name, omitted)
        add(archive, f"usr/lib/{name}/graphics.so", preserved, 0o755)
    with path.open("xb") as output:
        subprocess.run(["zstd", "-q", "-19", "-T1", "-c", str(raw)],
                       stdout=output, check=True)
    raw.unlink()
    return {"path": omitted_name, "type": "file", "size": len(omitted),
            "sha256": digest(omitted)}


def main():
    with tempfile.TemporaryDirectory(prefix="gaming-repack-test-") as name:
        root = Path(name)
        sources = []
        records = []
        specifications = (
            ("nvidia-utils", "575.64.05-2", "usr/lib/libcuda.so.1"),
            ("lib32-nvidia-utils", "575.64.05-1", "usr/lib32/libcuda.so.1"),
        )
        for package_name, version, omitted_name in specifications:
            source = root / f"{package_name}-{version}-x86_64.pkg.tar.zst"
            omission = package(source, package_name, version, omitted_name)
            sources.append(source)
            output_version = version + ".gaming1"
            records.append({
                "name": package_name, "sourceFilename": source.name,
                "sourceSha256": digest(source.read_bytes()),
                "sourceSignatureFilename": source.name + ".sig",
                "sourceSignatureSha256": "1" * 64,
                "sourceSignerFingerprint": "A" * 40,
                "outputFilename": f"{package_name}-{output_version}-x86_64.pkg.tar.zst",
                "outputVersion": output_version, "outputSha256": "0" * 64,
                "installedSize": 5, "savedBytes": 7,
                "requiredMembers": [f"usr/lib/{package_name}/graphics.so"],
                "omittedMembers": [omission],
            })
        profile = root / "profile.json"
        document = {"schemaVersion": 1, "status": "reviewed",
                    "profileId": "gaming-no-cuda-v1", "packages": records}
        profile.write_text(json.dumps(document), encoding="utf-8")
        candidate = root / "candidate"
        candidate.mkdir()
        outputs = materialize(profile, sources, candidate, allow_unpinned=True)
        hashes = {path.name: digest(path.read_bytes()) for path in outputs}
        for record in records:
            record["outputSha256"] = hashes[record["outputFilename"]]
        profile.write_text(json.dumps(document), encoding="utf-8")
        first = root / "first"
        second = root / "second"
        first.mkdir()
        second.mkdir()
        first_outputs = materialize(profile, sources, first)
        second_outputs = materialize(profile, sources, second)
        assert [digest(path.read_bytes()) for path in first_outputs] == [
            digest(path.read_bytes()) for path in second_outputs
        ]
        for path in first_outputs:
            with subprocess.Popen(["zstd", "-q", "-d", "-c", str(path)],
                                  stdout=subprocess.PIPE) as process:
                with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                    members = {member.name for member in archive}
                assert process.wait() == 0
            record = next(item for item in records if item["outputFilename"] == path.name)
            assert record["omittedMembers"][0]["path"] not in members
            assert record["requiredMembers"][0] in members
        corrupt_sources = root / "corrupt-sources"
        corrupt_sources.mkdir()
        corrupted = corrupt_sources / sources[0].name
        corrupted.write_bytes(sources[0].read_bytes() + b"corruption")
        unused = root / "unused"
        unused.mkdir()
        try:
            materialize(profile, [corrupted, sources[1]], unused)
        except RepackError:
            pass
        else:
            raise AssertionError("corrupted source package was accepted")
        missing = json.loads(profile.read_text())
        missing["packages"][0]["omittedMembers"][0]["path"] = "usr/lib/missing"
        missing_profile = root / "missing.json"
        missing_profile.write_text(json.dumps(missing), encoding="utf-8")
        empty = root / "empty"
        empty.mkdir()
        try:
            materialize(missing_profile, sources, empty, allow_unpinned=True)
        except RepackError:
            pass
        else:
            raise AssertionError("missing reviewed omission was accepted")


if __name__ == "__main__":
    main()
