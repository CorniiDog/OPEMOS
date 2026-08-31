#!/usr/bin/env python3
"""Executable synthetic contract tests for the userspace closure audit."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "bootstrap/audit_userspace_closure.py"
NVIDIA = "575.64.05"
SIGNERS = {
    "nvidia-utils": "05C7775A9E8B977407FE08E69D4C5AA15426DA0A",
    "lib32-nvidia-utils": "D2E95FEC015CF1F911AAAB0C3D4C5008BB5C8D29",
    "egl-wayland": "83BC8889351B5DEBBB68416EB8AC08600F108CDF",
    "eglexternalplatform": "83BC8889351B5DEBBB68416EB8AC08600F108CDF",
    "egl-gbm": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(archive, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, __import__("io").BytesIO(data))


def package(path, name, version="1-1", arch="x86_64", dependencies=()):
    metadata = (
        f"pkgname = {name}\npkgver = {version}\narch = {arch}\nsize = 1024\n"
        + "".join(f"depend = {item}\n" for item in dependencies)
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        add(archive, ".PKGINFO", metadata)
        add(archive, f"usr/lib/{name}/fixture", b"fixture")
    path.with_suffix(path.suffix + ".sig").write_bytes(b"signature")


def repository_db(path, records):
    with tarfile.open(path, "w:gz") as archive:
        for record in records:
            text = (
                f"%NAME%\n{record['name']}\n\n%VERSION%\n{record['version']}\n\n"
                f"%FILENAME%\n{record['filename']}\n\n%SHA256SUM%\n{record['sha256']}\n\n"
                + ("%DEPENDS%\n" + "\n".join(record.get("depends", [])) + "\n\n"
                   if record.get("depends") else "")
            ).encode()
            add(archive, f"{record['name']}-{record['version']}/desc", text)


def holo(root):
    for name in ("filesystem", "glibc", "pacman"):
        record = root / f"usr/lib/holo/pacmandb/local/{name}-1-1"
        record.mkdir(parents=True)
        (record / "desc").write_text(
            f"%NAME%\n{name}\n\n%VERSION%\n1-1\n\n%ISIZE%\n1024\n",
            encoding="utf-8",
        )


def run(fixture, success, **environment):
    stage = Path(tempfile.mkdtemp(prefix="userspace-audit-stage-"))
    output = stage.parent / f"{stage.name}.json"
    command = [
        sys.executable, str(AUDIT), "--root", str(fixture["root"]),
        "--snapshot", "2025/08/01", "--snapshot-url", fixture["snapshot_url"],
        "--full-keyring", str(fixture["keyring"]),
        "--keyring-source", str(fixture["source"]),
        "--keyring-source-signature", str(fixture["source_sig"]),
        "--nvidia-utils", str(fixture["nvidia"]),
        "--nvidia-utils-signature", str(fixture["nvidia_sig"]),
        "--lib32-nvidia-utils", str(fixture["lib32"]),
        "--lib32-nvidia-utils-signature", str(fixture["lib32_sig"]),
        "--steamos", "3.8.14", "--nvidia", NVIDIA,
        "--stage", str(stage / "files"), "--output", str(output),
    ]
    env = os.environ.copy()
    env.update(
        PATH=f"{fixture['bin']}:{env['PATH']}", PROJECT_TEST_MODE="1",
        PROJECT_TEST_KEYRING_PROVENANCE=str(fixture["keyring_manifest"]),
        PROJECT_TEST_SNAPSHOT_MANIFEST=str(fixture["snapshot_manifest"]),
        **environment,
    )
    completed = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert (completed.returncode == 0) == success, completed.stderr
    return json.loads(output.read_text()) if success else completed.stderr


def fixture(base):
    root = base / "target"
    holo(root)
    nvidia = base / "nvidia-utils.pkg.tar.gz"
    lib32 = base / "lib32-nvidia-utils.pkg.tar.gz"
    package(nvidia, "nvidia-utils", f"{NVIDIA}-2", dependencies=("egl-wayland",))
    package(lib32, "lib32-nvidia-utils", f"{NVIDIA}-1", dependencies=(f"nvidia-utils={NVIDIA}-2",))
    snapshot = base / "snapshot"
    records = []
    for name, dependencies in (
        ("egl-wayland", ("eglexternalplatform",)),
        ("eglexternalplatform", ("egl-gbm",)),
        ("egl-gbm", ("glibc",)),
    ):
        path = snapshot / "extra/os/x86_64" / f"{name}-1-1-x86_64.pkg.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        package(path, name, dependencies=dependencies)
        records.append({"name": name, "version": "1-1", "filename": path.name,
                        "sha256": digest(path), "depends": dependencies})
    database_hashes = {}
    for repository in ("core", "extra", "multilib"):
        path = snapshot / f"{repository}/os/x86_64/{repository}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        repository_db(path, records if repository == "extra" else [])
        database_hashes[repository] = digest(path)
    keyring = base / "archlinux.gpg"
    keyring.write_bytes(b"full-keyring")
    source = base / "archlinux-keyring.pkg.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        add(archive, "usr/share/pacman/keyrings/archlinux.gpg", keyring.read_bytes())
    source_sig = source.with_suffix(source.suffix + ".sig")
    source_sig.write_bytes(b"source-signature")
    keyring_manifest = base / "keyring.json"
    keyring_manifest.write_text(json.dumps({
        "schemaVersion": 1, "snapshot": "2025/08/01",
        "source": {"package": source.name, "sha256": digest(source),
                   "signature": source_sig.name, "signatureSha256": digest(source_sig)},
        "keyring": {"path": "usr/share/pacman/keyrings/archlinux.gpg",
                    "sha256": digest(keyring)}, "reviewedAt": "2026-08-31",
    }))
    snapshot_manifest = base / "snapshot.json"
    snapshot_manifest.write_text(json.dumps({
        "schemaVersion": 1, "identity": "2025/08/01",
        "url": snapshot.as_uri() + "/", "databases": database_hashes,
    }))
    binaries = base / "bin"
    binaries.mkdir()
    (binaries / "gpg").write_text(
        "#!/bin/sh\ncase \" $* \" in *' --show-keys '*) "
        + "".join(f"echo 'fpr:::::::::{fingerprint}:';" for fingerprint in SIGNERS.values())
        + " exit 0;; esac\nout=; prev=; for arg in \"$@\"; do [ \"$prev\" != --output ] || out=$arg; prev=$arg; done; eval input=\\${$#}; cp \"$input\" \"$out\"\n"
    )
    cases = "".join(
        f"*{name}*) signer={SIGNERS[name]};;"
        for name in sorted(SIGNERS, key=len, reverse=True)
    )
    (binaries / "gpgv").write_text(
        f"#!/bin/sh\ncase \"$*\" in *\"${{MOCK_INVALID_SIGNATURE:-never}}\"*) exit 1;; esac\ncase \"$*\" in {cases} *) exit 2;; esac\necho \"[GNUPG:] VALIDSIG $signer 0 0 0 0 0 0 0 0 $signer\"\n"
    )
    (binaries / "vercmp").write_text("#!/bin/sh\n[ \"$1\" = \"$2\" ] && echo 0 || echo 1\n")
    for executable in binaries.iterdir():
        executable.chmod(0o755)
    return {"root": root, "nvidia": nvidia, "nvidia_sig": nvidia.with_suffix(nvidia.suffix + ".sig"),
            "lib32": lib32, "lib32_sig": lib32.with_suffix(lib32.suffix + ".sig"),
            "snapshot_url": snapshot.as_uri() + "/", "snapshot": snapshot,
            "keyring": keyring, "source": source, "source_sig": source_sig,
            "keyring_manifest": keyring_manifest, "snapshot_manifest": snapshot_manifest,
            "bin": binaries}


def main():
    with tempfile.TemporaryDirectory(prefix="userspace-audit-test-") as temporary:
        data = fixture(Path(temporary))
        candidate = run(data, True)
        assert [item["name"] for item in candidate["packages"]] == [
            "egl-gbm", "egl-wayland", "eglexternalplatform",
            "lib32-nvidia-utils", "nvidia-utils",
        ]
        assert candidate["missingReview"] == [{
            "packageName": "egl-gbm", "signerFingerprint": SIGNERS["egl-gbm"],
        }]
        policy = Path(temporary) / "reviewed-policy.json"
        grouped = {}
        for package_name, signer in SIGNERS.items():
            grouped.setdefault(signer, []).append(package_name)
        policy.write_text(json.dumps({"schemaVersion": 1, "signers": [
            {"fingerprint": signer, "status": "active", "packages": packages}
            for signer, packages in grouped.items()
        ]}))
        candidate_path = Path(temporary) / "candidate.json"
        candidate_path.write_text(json.dumps(candidate))
        reviewed_path = Path(temporary) / "reviewed.json"
        finalizer = [sys.executable, str(ROOT / "bootstrap/finalize_userspace_lock.py"),
                     "--candidate", str(candidate_path), "--minimal-keyring", str(data["keyring"]),
                     "--reviewed-policy", str(policy), "--reviewed-at", "2026-08-31",
                     "--output", str(reviewed_path)]
        finalizer_env = os.environ.copy()
        finalizer_env["PATH"] = f"{data['bin']}:{finalizer_env['PATH']}"
        assert subprocess.run(
            finalizer, env=finalizer_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).returncode == 0
        assert json.loads(reviewed_path.read_text())["status"] == "reviewed"
        assert subprocess.run(
            finalizer, env=finalizer_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).returncode != 0
        assert "cryptographic signature verification failed" in run(
            data, False, MOCK_INVALID_SIGNATURE="egl-gbm"
        )
        data["keyring"].write_bytes(b"wrong")
        assert "support-owned provenance" in run(data, False)
        data = fixture(Path(temporary) / "seeds")
        for name, version, arch in (("wrong", NVIDIA, "x86_64"),
                                    ("nvidia-utils", "999.0", "x86_64"),
                                    ("nvidia-utils", NVIDIA, "aarch64")):
            package(data["nvidia"], name, f"{version}-2", arch)
            assert "seed package does not match" in run(data, False)
        data = fixture(Path(temporary) / "unsafe")
        extra = data["snapshot"] / "extra/os/x86_64/extra.db"
        repository_db(extra, [{"name": "bad", "version": "1-1",
                               "filename": "../bad.pkg.tar.zst", "sha256": "0" * 64}])
        manifest = json.loads(data["snapshot_manifest"].read_text())
        manifest["databases"]["extra"] = digest(extra)
        data["snapshot_manifest"].write_text(json.dumps(manifest))
        assert "unsafe authenticated" in run(data, False)
        data = fixture(Path(temporary) / "symlink")
        local = data["root"] / "usr/lib/holo/pacmandb/local"
        moved = local.with_name("local-real")
        local.rename(moved)
        local.symlink_to(moved)
        assert "target destination traverses a symlink" in run(data, False)


if __name__ == "__main__":
    main()
