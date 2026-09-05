#!/usr/bin/env python3
"""Contract tests for immutable installer-bundle publication."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "bootstrap" / "publish_installer_bundle.sh"


def run(commit, *extra, env=None):
    return subprocess.run(
        [str(PUBLISHER), "--support-commit", commit, *extra],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main():
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    first = run(commit, "--dry-run")
    second = run(commit, "--dry-run")
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    plan = json.loads(first.stdout)
    tag = f"opemos-installer-bundle-{commit}"
    assert plan["schemaVersion"] == 1
    assert plan["status"] == "ready"
    canonical_repository = "CorniiDog/OPEMOS"
    assert plan["repository"] == canonical_repository
    assert plan["tag"] == tag
    assert plan["targetCommit"] == commit
    assert plan["asset"]["name"] == f"{tag}.json"
    assert len(plan["asset"]["sha256"]) == 64
    assert len(plan["asset"]["bundleId"]) == 64
    assert plan["asset"]["files"] >= 1
    assert commit in plan["notes"]
    assert "/tmp/" not in first.stdout and "/private/" not in first.stdout

    for invalid in ("", "a" * 39, "A" * 40, "f" * 40):
        failed = run(invalid, "--dry-run")
        assert failed.returncode != 0

    development = run(
        commit, "--development-repository", "owner/testing", "--dry-run"
    )
    assert development.returncode == 0, development.stderr
    assert json.loads(development.stdout)["repository"] == "owner/testing"

    with tempfile.TemporaryDirectory(prefix="bundle-publisher-") as temporary:
        fixture = Path(temporary)
        fake_bin = fixture / "bin"
        fake_bin.mkdir()
        real_git = shutil.which("git")
        assert real_git is not None
        git = fake_bin / "git"
        git.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[-3:] == ['remote', 'get-url', 'origin'] "
            "and 'FAKE_ORIGIN' in os.environ:\n"
            "    print(os.environ['FAKE_ORIGIN'])\n"
            "    raise SystemExit(0)\n"
            "os.execv(os.environ['REAL_GIT'], [os.environ['REAL_GIT'], *args])\n",
            encoding="utf-8",
        )
        git.chmod(0o755)
        gh_log = fixture / "gh.log"
        gh = fake_bin / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['GH_LOG'], 'a', encoding='utf-8') as log:\n"
            "    log.write(' '.join(args) + '\\n')\n"
            "command = args[:2]\n"
            "if command == ['auth', 'status']:\n"
            "    raise SystemExit(0)\n"
            "if command == ['api', 'repos/CorniiDog/OPEMOS']:\n"
            "    print('true')\n"
            "    raise SystemExit(0)\n"
            "if command == ['release', 'view']:\n"
            "    raise SystemExit(0 if os.environ.get('GH_RELEASE_EXISTS') == '1' else 1)\n"
            "if command == ['release', 'create']:\n"
            "    asset = pathlib.Path(args[3])\n"
            "    if not asset.is_file():\n"
            "        raise SystemExit(17)\n"
            "    json.loads(asset.read_text(encoding='utf-8'))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(19)\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["REAL_GIT"] = real_git
        env["GH_LOG"] = str(gh_log)

        legacy_env = env.copy()
        legacy_env["FAKE_ORIGIN"] = (
            "https://github.com/CorniiDog/open-gpu-kernel-modules-steamos-support"
        )
        legacy = run(commit, "--dry-run", env=legacy_env)
        assert legacy.returncode == 0, legacy.stderr
        assert json.loads(legacy.stdout)["repository"] == canonical_repository
        assert "open-gpu-kernel-modules-steamos-support" not in legacy.stdout

        mismatched_env = env.copy()
        mismatched_env["FAKE_ORIGIN"] = "https://github.com/attacker/other"
        mismatched = run(commit, "--dry-run", env=mismatched_env)
        assert mismatched.returncode != 0
        assert "origin does not match canonical repository" in mismatched.stderr
        assert mismatched.stdout == ""
        development_mismatch = run(
            commit, "--development-repository", "owner/testing", "--dry-run",
            env=mismatched_env,
        )
        assert development_mismatch.returncode == 0, development_mismatch.stderr
        assert json.loads(development_mismatch.stdout)["repository"] == "owner/testing"

        env["GH_RELEASE_EXISTS"] = "1"
        existing = run(commit, env=env)
        assert existing.returncode != 0
        assert "release already exists" in existing.stderr
        assert "release create" not in gh_log.read_text(encoding="utf-8")

        gh_log.write_text("", encoding="utf-8")
        env["GH_RELEASE_EXISTS"] = "0"
        created = run(commit, env=env)
        assert created.returncode == 0, created.stderr + gh_log.read_text(encoding="utf-8")
        log = gh_log.read_text(encoding="utf-8")
        assert f"release create {tag} " in log
        assert f"--target {commit}" in log
        assert "release edit" not in log and "--clobber" not in log


if __name__ == "__main__":
    main()
