#!/usr/bin/env python3
"""Validate the exact Windows zstd dependency used by the Core publisher."""

import hashlib
import json
import struct
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "windows-publisher-zstd-v1.json"
EXPECTED_TOP_LEVEL = {
    "schemaVersion", "kind", "platform", "component", "source", "payload",
    "license", "consumer",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_exact_keys(value, keys, label):
    assert isinstance(value, dict), f"{label} is not an object"
    assert set(value) == set(keys), f"{label} is not closed"


def main():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    require_exact_keys(contract, EXPECTED_TOP_LEVEL, "contract")
    assert contract["schemaVersion"] == 1
    assert contract["kind"] == "opemos-windows-publisher-zstd-dependency"
    assert contract["platform"] == "windows-x86_64"

    component = contract["component"]
    require_exact_keys(component, {"name", "version", "license"}, "component")
    assert component == {
        "name": "zstd", "version": "1.5.7", "license": "BSD-3-Clause"
    }

    source = contract["source"]
    require_exact_keys(
        source, {"repository", "tag", "releaseId", "publishedAt", "asset"},
        "source",
    )
    assert source["repository"] == "facebook/zstd"
    assert source["tag"] == "v1.5.7"
    asset = source["asset"]
    require_exact_keys(
        asset, {"name", "url", "localPath", "size", "sha256"}, "asset"
    )
    archive_path = ROOT / asset["localPath"]
    assert archive_path.is_file() and not archive_path.is_symlink()
    archive_bytes = archive_path.read_bytes()
    assert len(archive_bytes) == asset["size"]
    assert digest(archive_bytes) == asset["sha256"]

    payload = contract["payload"]
    require_exact_keys(
        payload, {"member", "runtimePath", "size", "sha256", "peMachine", "argv"},
        "payload",
    )
    assert payload["runtimePath"] == "bin/zstd.exe"
    assert payload["argv"] == ["zstd.exe", "-q", "-d", "-c", "{input}"]
    assert payload["peMachine"] == "x86_64"

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        normalized = [str(PurePosixPath(name)) + ("/" if name.endswith("/") else "")
                      for name in names]
        assert names == normalized
        assert len(names) == len(set(names))
        assert all(not PurePosixPath(name).is_absolute()
                   and ".." not in PurePosixPath(name).parts for name in names)
        assert payload["member"] in names
        info = archive.getinfo(payload["member"])
        assert not info.is_dir() and info.file_size == payload["size"]
        executable = archive.read(info)

    assert len(executable) == payload["size"]
    assert digest(executable) == payload["sha256"]
    assert executable[:2] == b"MZ"
    pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
    assert executable[pe_offset:pe_offset + 4] == b"PE\0\0"
    assert struct.unpack_from("<H", executable, pe_offset + 4)[0] == 0x8664

    license_record = contract["license"]
    require_exact_keys(
        license_record,
        {"sourceUrl", "gitBlob", "localPath", "runtimePath", "size", "sha256"},
        "license",
    )
    license_path = ROOT / license_record["localPath"]
    assert license_path.is_file() and not license_path.is_symlink()
    license_bytes = license_path.read_bytes()
    assert len(license_bytes) == license_record["size"]
    assert digest(license_bytes) == license_record["sha256"]
    assert license_record["runtimePath"] == "licenses/zstd-1.5.7-LICENSE"

    consumer = contract["consumer"]
    require_exact_keys(
        consumer, {"entryPoint", "resolution", "requiredOperation"}, "consumer"
    )
    assert consumer["entryPoint"] == "lib/validate_publish_inputs.py"
    assert consumer["resolution"] == "PATH"
    validator = (ROOT / consumer["entryPoint"]).read_text(encoding="utf-8")
    assert '["zstd", "-q", "-d", "-c", temporary_name]' in validator
    assert "MAX_MODULE_BYTES" in validator
    assert "payload.update(chunk)" in validator


if __name__ == "__main__":
    main()
