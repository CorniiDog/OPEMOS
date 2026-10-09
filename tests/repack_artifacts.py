#!/usr/bin/env python3
"""Contract tests for deterministic, revisioned module repacking."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from publisher import KERNEL, NVIDIA, SUPPORT_COMMIT, fixture


ROOT = Path(__file__).resolve().parent.parent
REPACK = ROOT / "lib/repack_module_artifact.py"
PUBLISH = ROOT / "bootstrap/publish_artifacts.sh"


def run(paths, output, env, *extra):
    return subprocess.run([
        sys.executable, str(REPACK), "--archive", str(paths[0]),
        "--checksum", str(paths[1]), "--build-info", str(paths[2]),
        "--provenance", str(paths[3]), "--output-dir", str(output),
        "--support-commit", SUPPORT_COMMIT, *extra,
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def main():
    with tempfile.TemporaryDirectory(prefix="repack-contract-") as temporary:
        root = Path(temporary)
        import publisher
        publisher.SOURCE_COMMIT = "40bd1b5d6d39ae4e4180b7a665df144b08854d14"
        # Raw public success/retention dispatch with fixture-only build/install
        # adapters, but real canonical validation, cache, receipt and module checks.
        import shutil
        support = root / "public-support"
        for directory in ("lib", "bootstrap", "policies", "profiles", "trust"):
            shutil.copytree(ROOT / directory, support / directory)
        def script(path, body):
            path.write_text("#!/bin/bash\nset -euo pipefail\n" + body)
            path.chmod(0o755)
        script(support / "bootstrap/build_automatic_for_target.sh",
               'mkdir "$5"\ncp "$MOCK_PRODUCT"/* "$5/"\ncp "$MOCK_MATERIALIZATION" "$5/materialization.json"\necho build >> "$MOCK_BUILD_LOG"\n')
        script(support / "bootstrap/install.sh", r'''archive=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --archive ]]; then archive="$2"; shift 2; else shift; fi
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xzf "$archive" -C "$work"
mkdir -p "$MOCK_TARGET" "$PROJECT_TEST_ROOT/var/lib/open-gpu-kernel-modules-steamos-support"
cp "$work/modules/"*.ko.zst "$MOCK_TARGET/"
chmod 644 "$MOCK_TARGET/"*.ko.zst
cp "$work/BUILD-INFO.txt" "$PROJECT_TEST_ROOT/var/lib/open-gpu-kernel-modules-steamos-support/installed-build-info.txt"
echo install >> "$MOCK_INSTALL_LOG"
''')
        script(support / "bootstrap/install_recovery_guardian.sh",
               'mkdir -p "$PROJECT_TEST_ROOT/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery"\n')
        real_git = shutil.which("git")
        def git_command(*args):
            return subprocess.run([real_git, *map(str, args)], check=True, text=True,
                                  capture_output=True).stdout.strip()
        git_command("-c", "init.defaultBranch=main", "init", "--quiet", support)
        git_command("-C", support, "add", ".")
        git_command("-C", support, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--quiet", "-m", "Public exact checkout fixture")
        global SUPPORT_COMMIT
        SUPPORT_COMMIT = git_command("-C", support, "rev-parse", "HEAD")
        publisher.SUPPORT_COMMIT = SUPPORT_COMMIT
        paths = fixture(root / "source")
        tools = root / "bin"
        tools.mkdir()
        (tools / "modinfo").write_text(
            "#!/bin/sh\n[ \"$2\" = version ] && echo '" + NVIDIA + "' || echo '" + KERNEL + " SMP'\n")
        (tools / "readelf").write_text(
            "#!/bin/sh\necho 'Machine: Advanced Micro Devices X86-64'\n")
        for tool in ("modinfo", "readelf"):
            (tools / tool).chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{tools}:{env['PATH']}"
        env["PROJECT_TEST_MODE"] = "1"
        output = root / "output"

        dry = run(paths, output, env, "--dry-run")
        assert dry.returncode == 0, dry.stderr
        plan = json.loads(dry.stdout)
        assert plan["createOnly"] is True
        assert plan["modulePayloadsByteIdentical"] is True
        assert not output.exists()

        first = run(paths, output, env)
        assert first.returncode == 0, first.stderr
        first_plan = json.loads(first.stdout)
        first_archive = output / first_plan["output"]["archive"]
        first_bytes = first_archive.read_bytes()
        original_provenance = json.loads(paths[3].read_text())
        retained_provenance = json.loads((output / first_plan["output"]["provenance"]).read_text())
        assert retained_provenance["trust"] == original_provenance["trust"]
        assert retained_provenance["source"] == original_provenance["source"]
        assert retained_provenance["target"] == original_provenance["target"]
        assert retained_provenance["repack"]["payloadIdentity"] == "byte-identical"

        second_output = root / "output-second"
        second = run(paths, second_output, env)
        assert second.returncode == 0, second.stderr
        second_plan = json.loads(second.stdout)
        assert (second_output / second_plan["output"]["archive"]).read_bytes() == first_bytes
        assert run(paths, output, env).returncode != 0
        duplicate = run(paths, root / "duplicate", env,
                        "--archive", str(paths[0]), "--dry-run")
        assert duplicate.returncode != 0
        assert "exactly once" in duplicate.stderr
        publish = subprocess.run([
            str(PUBLISH), "--dry-run", "--archive", str(first_archive),
            "--checksum", str(output / (first_archive.name + ".sha256")),
            "--build-info", str(output / first_plan["output"]["buildInfo"]),
            "--provenance", str(output / first_plan["output"]["provenance"]),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert publish.returncode == 0, publish.stderr
        assert json.loads(publish.stdout)["tag"].endswith("-modules-zstd-r1")

        # Compose the actual cache preparation primitives; the locally built
        # input must materialize without claiming published certification.
        product_dir = root / "product"
        subprocess.run([sys.executable, str(ROOT / "lib/build_driver_product.py"),
                        "--archive", str(first_archive),
                        "--checksum", str(output / (first_archive.name + ".sha256")),
                        "--build-info", str(output / first_plan["output"]["buildInfo"]),
                        "--provenance", str(output / first_plan["output"]["provenance"]),
                        "--output-dir", str(product_dir)], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        products = list(product_dir.glob("*.tar.gz"))
        assert len(products) == 1
        manifest = product_dir / "bundle.json"
        subprocess.run([sys.executable, str(ROOT / "lib/driver_binary_bundle.py"), "create",
                        "--archive", str(products[0]), "--sidecar", str(products[0]) + ".sha256",
                        "--output", str(manifest), "--release-tag", first_plan["output"]["tag"]],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import hashlib
        materialized = subprocess.run([sys.executable, str(ROOT / "lib/materialize_driver_product.py"),
            "--manifest", str(manifest), "--asset-dir", str(product_dir),
            "--expected-manifest-sha256", hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "--expected-core-commit", SUPPORT_COMMIT, "--expected-steamos", retained_provenance["target"]["steamosVersion"],
            "--expected-kernel", KERNEL, "--expected-nvidia", NVIDIA,
            "--expected-architecture", "x86_64", "--output-dir", str(root / "materialized")],
            env=env, text=True, capture_output=True)
        assert materialized.returncode == 0, materialized.stderr
        materialization = json.loads(materialized.stdout)
        assert materialization["source"] == {key: retained_provenance["source"][key] for key in ("repository", "commit")}
        assert (root / "materialized" / materialization["outputs"]["archive"]["name"]).read_bytes() == first_bytes
        result_path = root / "materialization.json"
        result_path.write_text(json.dumps(materialization, sort_keys=True, separators=(",", ":")) + "\n")
        cache = root / "recovery-cache"
        exact = ["--steamos", retained_provenance["target"]["steamosVersion"],
                 "--kernel", KERNEL, "--nvidia", NVIDIA, "--support-revision", SUPPORT_COMMIT]
        cache_tool = str(ROOT / "lib/recovery_cached_product.py")
        subprocess.run([sys.executable, cache_tool, "stage", *exact,
                        "--materialization", str(result_path), "--input-dir", str(root / "materialized"),
                        "--destination", str(cache)], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cached = subprocess.run([sys.executable, cache_tool, "show", *exact,
                                 "--directory", str(cache)], env=env, text=True, capture_output=True)
        assert cached.returncode == 0, cached.stderr
        cached_document = json.loads(cached.stdout)
        assert Path(cached_document["paths"]["archive"]).read_bytes() == first_bytes
        assert cached_document["source"] == materialization["source"]
        # Receipt retention binds installed bytes to this actual composed cache.
        import tarfile
        installed_root = root / "installed"
        installed_modules = installed_root / "usr/lib/modules" / KERNEL / "updates/open-gpu-kernel-modules-steamos"
        installed_modules.mkdir(parents=True)
        with tarfile.open(first_archive, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name.startswith("modules/") and member.name.endswith(".ko.zst"):
                    destination = installed_modules / Path(member.name).name
                    destination.write_bytes(archive.extractfile(member).read())
                    destination.chmod(0o644)
        receipt_tool = str(ROOT / "lib/recovery_cached_receipt.py")
        receipt_args = [*exact, "--root", str(installed_root), "--cache", str(cache)]
        subprocess.run([sys.executable, receipt_tool, "commit", *receipt_args],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        receipt = installed_root / "var/lib/open-gpu-kernel-modules-steamos-support/recovery/cached-repair-receipt.json"
        retained_receipt = receipt.read_bytes()
        subprocess.run([sys.executable, receipt_tool, "commit", *receipt_args],
                       env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert receipt.read_bytes() == retained_receipt
        changed_module = installed_modules / "nvidia.ko.zst"
        original_module = changed_module.read_bytes()
        changed_module.write_bytes(original_module + b"corrupt")
        refused_receipt = subprocess.run([sys.executable, receipt_tool, "commit", *receipt_args],
                                        env=env, text=True, capture_output=True)
        assert refused_receipt.returncode != 0
        assert receipt.read_bytes() == retained_receipt
        changed_module.write_bytes(original_module)



        script(tools / "git", f'''if [[ "$1" == clone ]]; then
  [[ "${{MOCK_NETWORK_FAILURE:-0}}" == 0 ]] || exit 7
  exec "{real_git}" clone --quiet --depth 1 "file://$MOCK_SUPPORT" "${{@: -1}}"
fi
exec "{real_git}" "$@"
''')
        script(tools / "curl", '[[ "${MOCK_QUERY_FAILURE:-0}" == 0 ]] || exit 28\nwhile [[ $# -gt 0 ]]; do if [[ "$1" == -o ]]; then printf "[]\\n" > "$2"; exit 0; fi; shift; done; exit 1\n')
        script(tools / "sudo", 'exec "$@"\n')
        script(tools / "nvidia-smi", 'echo 575.64.05\n')
        script(tools / "uname", 'case "$1" in -r) echo "$MOCK_KERNEL";; -m) echo x86_64;; *) echo Linux;; esac\n')
        script(tools / "modinfo", 'if [[ "$1" == -n ]]; then echo "$MOCK_TARGET/nvidia.ko.zst"; elif [[ "$2" == version ]]; then echo 575.64.05; else echo "$MOCK_KERNEL SMP"; fi\n')
        public_root = root / "public-root"
        (public_root / "etc").mkdir(parents=True)
        (public_root / "etc/os-release").write_text('ID=steamos\nVERSION_ID="3.8.16"\n')
        public_env = {**env, "PROJECT_TEST_ROOT": str(public_root), "SUPPORT_REVISION": SUPPORT_COMMIT,
                      "HOME": str(root / "public-home"), "MOCK_SUPPORT": str(support),
                      "MOCK_PRODUCT": str(root / "materialized"), "MOCK_MATERIALIZATION": str(result_path),
                      "MOCK_KERNEL": KERNEL, "MOCK_TARGET": str(public_root / "usr/lib/modules" / KERNEL / "updates/open-gpu-kernel-modules-steamos"),
                      "MOCK_BUILD_LOG": str(root / "build.log"), "MOCK_INSTALL_LOG": str(root / "install.log")}
        unavailable_without_cache = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                                  input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                                  env={**public_env, "MOCK_QUERY_FAILURE": "1"}, text=True, capture_output=True)
        assert unavailable_without_cache.returncode != 0
        assert not (root / "build.log").exists() and not (root / "install.log").exists()
        public = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                env=public_env, text=True, capture_output=True)
        assert public.returncode == 0, (public.stdout, public.stderr)
        assert (root / "install.log").read_text().splitlines() == ["install"]
        assert (public_root / "home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/cached-repair/materialization.json").is_file()
        assert (public_root / "var/lib/open-gpu-kernel-modules-steamos-support/recovery/cached-repair-receipt.json").is_file()
        repeated_public = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                         input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                         env=public_env, text=True, capture_output=True)
        assert repeated_public.returncode == 0, (repeated_public.stdout, repeated_public.stderr)
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install"]
        assert "Reusing the verified exact built-product cache" in repeated_public.stdout
        query_failure = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                       input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                       env={**public_env, "MOCK_QUERY_FAILURE": "1"}, text=True, capture_output=True)
        assert query_failure.returncode == 0, (query_failure.stdout, query_failure.stderr)
        assert "cache only" in query_failure.stdout
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install"]

        receipt_path = public_root / "var/lib/open-gpu-kernel-modules-steamos-support/recovery/cached-repair-receipt.json"
        receipt_before = receipt_path.read_bytes()
        cached_no_flag = subprocess.run(["bash", "-s", "--", "--yes"],
                                        input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                        env=public_env, text=True, capture_output=True)
        assert cached_no_flag.returncode != 0
        assert "--build-as-fallback" in cached_no_flag.stderr
        cached_plan = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--resolve-only"],
                                     input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                     env=public_env, text=True, capture_output=True)
        assert cached_plan.returncode == 0, (cached_plan.stdout, cached_plan.stderr)
        assert receipt_path.read_bytes() == receipt_before
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install"]

        installed_module = Path(public_env["MOCK_TARGET"]) / "nvidia.ko.zst"
        verified_module = installed_module.read_bytes()
        installed_module.write_bytes(b"damaged installed module")
        repaired_public = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                         input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                         env=public_env, text=True, capture_output=True)
        assert repaired_public.returncode == 0, (repaired_public.stdout, repaired_public.stderr)
        assert installed_module.read_bytes() == verified_module
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install", "install"]

        retained_bundle = Path(public_env["HOME"]) / ".cache/open-gpu-kernel-modules-steamos-support/core-checkouts" / (SUPPORT_COMMIT + ".bundle")
        retained_bytes = retained_bundle.read_bytes()
        git_command("-C", support, "bundle", "verify", retained_bundle)
        installed_module.write_bytes(b"damaged before fully offline repair")
        offline_repair = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                        input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                        env={**public_env, "MOCK_QUERY_FAILURE": "1", "MOCK_NETWORK_FAILURE": "1"},
                                        text=True, capture_output=True)
        assert offline_repair.returncode == 0, (offline_repair.stdout, offline_repair.stderr)
        assert "retained Git objects" in offline_repair.stderr
        assert installed_module.read_bytes() == verified_module
        assert retained_bundle.read_bytes() == retained_bytes
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install", "install", "install"]

        cache_manifest = public_root / "home/.steamos/open-gpu-kernel-modules-steamos-support/recovery/cached-repair/materialization.json"
        original_manifest = cache_manifest.read_bytes()
        wrong_source = json.loads(original_manifest)
        wrong_source["source"]["commit"] = "c" * 40
        wrong_source_bytes = (json.dumps(wrong_source, sort_keys=True, separators=(",", ":")) + "\n").encode()
        cache_manifest.write_bytes(wrong_source_bytes)
        optimized_rejection = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                             input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                             env={**public_env, "PYTHONOPTIMIZE": "1"}, text=True, capture_output=True)
        assert optimized_rejection.returncode != 0
        assert cache_manifest.read_bytes() == wrong_source_bytes
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install", "install", "install"]
        cache_manifest.write_bytes(original_manifest)
        damaged_manifest = cache_manifest.read_bytes() + b"invalid"
        cache_manifest.write_bytes(damaged_manifest)
        installed_before = {p.name: p.read_bytes() for p in Path(public_env["MOCK_TARGET"]).iterdir()}
        rejected_public = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                         input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                         env=public_env, text=True, capture_output=True)
        assert rejected_public.returncode != 0
        assert cache_manifest.read_bytes() == damaged_manifest
        assert {p.name: p.read_bytes() for p in Path(public_env["MOCK_TARGET"]).iterdir()} == installed_before
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install", "install", "install"]
        outage_rejection = subprocess.run(["bash", "-s", "--", "--build-as-fallback", "--yes"],
                                          input=(ROOT / "bootstrap/online_install.sh").read_text(),
                                          env={**public_env, "MOCK_QUERY_FAILURE": "1"}, text=True, capture_output=True)
        assert outage_rejection.returncode != 0
        assert cache_manifest.read_bytes() == damaged_manifest
        assert {p.name: p.read_bytes() for p in Path(public_env["MOCK_TARGET"]).iterdir()} == installed_before
        assert (root / "build.log").read_text().splitlines() == ["build"]
        assert (root / "install.log").read_text().splitlines() == ["install", "install", "install"]


        paths[1].write_text("0" * 64 + f"  {paths[0].name}\n")
        assert run(paths, root / "bad", env, "--dry-run").returncode != 0


if __name__ == "__main__":
    main()
