#!/usr/bin/env python3
"""Contract tests for desktop update trust onboarding and publication."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "lib/desktop_update_release.py"
PUBLISHER = ROOT / "bootstrap/publish_desktop_update.sh"
REVISION = "a" * 40
SIGNER = "0123456789ABCDEF0123456789ABCDEF01234567"
OTHER_SIGNER = "89ABCDEF0123456789ABCDEF0123456789ABCDEF"


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


def executable(path):
    payload = bytearray(128)
    payload[:6] = b"\x7fELF\x02\x01"
    payload[18:20] = (62).to_bytes(2, "little")
    path.write_bytes(payload)
    path.chmod(0o755)
    return bytes(payload)


def run(*arguments, environment=None, success=True):
    process = subprocess.run(
        [str(item) for item in arguments], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=environment, check=False,
    )
    if success and process.returncode != 0:
        raise AssertionError(process.stderr)
    if not success and process.returncode == 0:
        raise AssertionError("command unexpectedly succeeded")
    return process


def make_mock_tools(root):
    tools = root / "tools"
    tools.mkdir()
    gpgv = tools / "gpgv"
    gpgv.write_text(f"""#!/bin/sh
if [ "${{MOCK_SIGNATURE_VALID:-1}}" != 1 ]; then exit 1; fi
if [ -n "${{MOCK_GPGV_PID_FILE:-}}" ]; then
  printf '%s\n' "$$" > "$MOCK_GPGV_PID_FILE"
  sleep "${{MOCK_GPGV_SLEEP:-30}}"
fi
printf '[GNUPG:] VALIDSIG %s 2026-01-01 0 4 0 1 10 00 00\n' "${{MOCK_SIGNER:-{SIGNER}}}"
""", encoding="utf-8")
    gpg = tools / "gpg"
    gpg.write_text(f"""#!/bin/sh
printf 'pub:-:2048:1:0000000000000000:0:0:::::::\n'
printf 'fpr:::::::::%s:\n' "${{MOCK_LISTED_SIGNER:-{SIGNER}}}"
""", encoding="utf-8")
    gh = tools / "gh"
    gh.write_text("""#!/bin/sh
printf '%s\n' "$*" >> "$MOCK_GH_LOG"
if [ "$1" = auth ] && [ "$2" = status ]; then exit "${MOCK_AUTH_EXIT:-0}"; fi
if [ "$1" = api ]; then
  case "$2" in
    */commits/*) printf '%s\n' "${MOCK_REMOTE_COMMIT:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}" ;;
    *) printf '%s\n' "${MOCK_PUSH_PERMISSION:-true}" ;;
  esac
  exit 0
fi
if [ "$1" = release ] && [ "$2" = view ]; then exit "${MOCK_RELEASE_EXISTS:-1}"; fi
if [ "$1" = release ] && [ "$2" = create ]; then
  [ -z "${MOCK_CREATE_PID_FILE:-}" ] || printf '%s\n' "$$" > "$MOCK_CREATE_PID_FILE"
  [ -z "${MOCK_CREATE_SLEEP:-}" ] || sleep "$MOCK_CREATE_SLEEP"
  exit 0
fi
exit 1
""", encoding="utf-8")
    for path in (gpgv, gpg, gh):
        path.chmod(0o755)
    return tools, gpgv, gpg


def fixture(root):
    binary = root / "opemos-recovery-status"
    payload = executable(binary)
    manifest = root / "opemos-desktop-v1.2.3.manifest.json"
    document = {
        "schemaVersion": 1,
        "kind": "opemos-desktop-update",
        "releaseTag": "opemos-desktop-v1.2.3",
        "version": "1.2.3",
        "architecture": "x86_64",
        "filename": "opemos-recovery-status",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "supportRevision": REVISION,
        "minimumGuardianSchema": 1,
    }
    manifest.write_text(canonical(document), encoding="utf-8")
    signature = root / "opemos-desktop-v1.2.3.manifest.json.sig"
    signature.write_bytes(b"detached-signature")
    keyring = root / "reviewed-keyring.gpg"
    keyring.write_bytes(b"public-keyring")
    policy = root / "reviewed-policy.json"
    policy.write_text(canonical({
        "schemaVersion": 1,
        "status": "active",
        "keyringSha256": hashlib.sha256(keyring.read_bytes()).hexdigest(),
        "signers": [{"fingerprint": SIGNER, "status": "active",
                     "scope": "opemos-desktop-update"}],
    }), encoding="utf-8")
    return binary, manifest, signature, keyring, policy


def plan(environment, files, success=True, repository=None):
    binary, manifest, signature, keyring, policy = files
    arguments = [
        sys.executable, HELPER, "plan", "--binary", binary, "--manifest", manifest,
        "--signature", signature, "--keyring", keyring, "--policy", policy,
        "--version", "1.2.3",
    ]
    if repository:
        arguments += ["--repository", repository]
    return run(*arguments, environment=environment, success=success)


def main():
    with tempfile.TemporaryDirectory(prefix="desktop-publisher-") as name:
        root = Path(name)
        tools, gpgv, gpg = make_mock_tools(root)
        environment = {
            **os.environ,
            "OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE": "1",
            "OPEMOS_TEST_GPGV": str(gpgv),
            "OPEMOS_TEST_GPG": str(gpg),
        }
        files = fixture(root)

        # Manifest production is byte-for-byte compatible with the updater.
        generated = run(
            sys.executable, HELPER, "manifest", "--binary", files[0],
            "--version", "1.2.3", "--support-revision", REVISION,
            environment=environment,
        )
        assert generated.stdout == files[1].read_text(encoding="utf-8")
        output = root / "opemos-desktop-v1.2.3.manifest.json.created"
        run(sys.executable, HELPER, "manifest", "--binary", files[0],
            "--version", "1.2.3", "--support-revision", REVISION,
            "--output", output, environment=environment, success=False)
        correct_output = root / "out" / files[1].name
        correct_output.parent.mkdir()
        run(sys.executable, HELPER, "manifest", "--binary", files[0],
            "--version", "1.2.3", "--support-revision", REVISION,
            "--output", correct_output, environment=environment)
        run(sys.executable, HELPER, "manifest", "--binary", files[0],
            "--version", "1.2.3", "--support-revision", REVISION,
            "--output", correct_output, environment=environment, success=False)

        # Public-key onboarding validates key membership and writes create-only.
        policy_output = root / "policy-output.json"
        created = run(sys.executable, HELPER, "trust-policy", "--keyring", files[3],
                      "--signer", SIGNER.lower(), "--output", policy_output,
                      environment=environment)
        assert created.stdout == policy_output.read_text(encoding="utf-8")
        assert json.loads(created.stdout)["keyringSha256"] == hashlib.sha256(files[3].read_bytes()).hexdigest()
        run(sys.executable, HELPER, "trust-policy", "--keyring", files[3],
            "--signer", OTHER_SIGNER, environment=environment, success=False)
        run(sys.executable, HELPER, "trust-policy", "--keyring", files[3],
            "--signer", SIGNER, "--output", policy_output,
            environment=environment, success=False)

        validated = json.loads(plan(environment, files).stdout)
        assert validated["status"] == "validated"
        assert validated["repository"] == "CorniiDog/OPEMOS"
        assert [item["name"] for item in validated["assets"]] == [
            "opemos-recovery-status", "opemos-desktop-v1.2.3.manifest.json",
            "opemos-desktop-v1.2.3.manifest.json.sig",
        ]
        assert validated["notes"].endswith("\n")
        assert json.loads(plan(environment, files).stdout) == validated

        # Every trust and identity binding fails before any publisher mutation.
        original_manifest = files[1].read_bytes()
        files[1].write_bytes(original_manifest + b" ")
        plan(environment, files, success=False)
        malformed = json.loads(original_manifest)
        malformed["version"] = 123
        files[1].write_text(canonical(malformed))
        plan(environment, files, success=False)
        files[1].write_bytes(original_manifest)
        files[0].write_bytes(files[0].read_bytes() + b"tamper")
        plan(environment, files, success=False)
        executable(files[0])
        original_policy = files[4].read_text()
        policy_document = json.loads(original_policy)
        policy_document["keyringSha256"] = "0" * 64
        files[4].write_text(canonical(policy_document))
        plan(environment, files, success=False)
        files[4].write_text(original_policy)
        plan({**environment, "MOCK_SIGNER": OTHER_SIGNER}, files, success=False)
        plan({**environment, "MOCK_SIGNATURE_VALID": "0"}, files, success=False)
        wrong_name = root / "renamed-manifest.json"
        wrong_name.write_bytes(files[1].read_bytes())
        wrong_files = (files[0], wrong_name, files[2], files[3], files[4])
        plan(environment, wrong_files, success=False)
        symlink = root / "linked-keyring.gpg"
        symlink.symlink_to(files[3])
        linked_files = (files[0], files[1], files[2], symlink, files[4])
        plan(environment, linked_files, success=False)
        plan(environment, files, success=False, repository="someone/fork")

        # A terminated validator reaps its signature verifier and removes its
        # private keyring/manifest snapshot directory.
        verifier_pid_file = root / "gpgv.pid"
        helper_tmp = root / "helper-tmp"
        helper_tmp.mkdir()
        binary, manifest, signature, keyring, policy = files
        validating = subprocess.Popen([
            sys.executable, str(HELPER), "plan", "--binary", str(binary),
            "--manifest", str(manifest), "--signature", str(signature),
            "--keyring", str(keyring), "--policy", str(policy),
            "--version", "1.2.3",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={
            **environment, "TMPDIR": str(helper_tmp),
            "MOCK_GPGV_PID_FILE": str(verifier_pid_file), "MOCK_GPGV_SLEEP": "30",
        })
        for _ in range(100):
            if verifier_pid_file.exists():
                break
            time.sleep(0.02)
        assert verifier_pid_file.exists()
        validating.terminate()
        validating.communicate(timeout=5)
        assert validating.returncode == 130
        assert not list(helper_tmp.glob("opemos-release-verify-*"))
        try:
            os.kill(int(verifier_pid_file.read_text()), 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("cancelled signature validator remained alive")

        # Shell dry-run never calls gh. Live mode is mandatory create-only,
        # refuses existing releases, and has stable asset ordering.
        gh_log = root / "gh.log"
        shell_env = {
            **environment, "PATH": f"{tools}:{os.environ['PATH']}",
            "MOCK_GH_LOG": str(gh_log),
        }
        command = [
            PUBLISHER, "--binary", files[0], "--manifest", files[1],
            "--signature", files[2], "--keyring", files[3], "--policy", files[4],
            "--version", "1.2.3",
        ]
        dry = run(*command, "--dry-run", environment=shell_env)
        assert json.loads(dry.stdout) == validated
        assert not gh_log.exists()
        production_env = {key: value for key, value in shell_env.items()
                          if key != "OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE"}
        run(*command, "--dry-run", environment=production_env, success=False)
        assert not gh_log.exists()
        run(*command, environment=shell_env, success=False)
        run(*command, "--create-only", environment={
            **shell_env, "MOCK_RELEASE_EXISTS": "0"}, success=False)
        log = gh_log.read_text()
        assert "release create" not in log
        gh_log.write_text("")
        run(*command, "--create-only", environment={
            **shell_env, "MOCK_REMOTE_COMMIT": "b" * 40}, success=False)
        assert "release create" not in gh_log.read_text()
        gh_log.write_text("")
        run(*command, "--create-only", environment=shell_env)
        lines = gh_log.read_text().splitlines()
        create = next(line for line in lines if line.startswith("release create "))
        assert create.index("/opemos-recovery-status") < create.index("/opemos-desktop-v1.2.3.manifest.json ")
        assert create.index("/opemos-desktop-v1.2.3.manifest.json ") < create.index("/opemos-desktop-v1.2.3.manifest.json.sig")
        cancellation_root = root / "cancellation-tmp"
        cancellation_root.mkdir()
        pid_file = root / "create.pid"
        cancelled = subprocess.Popen(
            [str(item) for item in (*command, "--create-only")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**shell_env, "TMPDIR": str(cancellation_root),
                 "MOCK_CREATE_PID_FILE": str(pid_file), "MOCK_CREATE_SLEEP": "30"},
        )
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.02)
        assert pid_file.exists()
        cancelled.terminate()
        cancelled.communicate(timeout=5)
        assert cancelled.returncode == 130
        assert not list(cancellation_root.glob("opemos-desktop-publish.*"))
        try:
            os.kill(int(pid_file.read_text()), 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("cancelled GitHub publisher remained alive")
        run(*command, "--dry-run", "--development-repository", "someone/fork",
            environment=production_env, success=False)
        development = run(*command, "--dry-run", "--development-repository", "someone/fork",
                          environment={**shell_env, "OPEMOS_DEVELOPMENT_PUBLISH_OVERRIDE": "1"})
        assert json.loads(development.stdout)["repository"] == "someone/fork"


if __name__ == "__main__":
    main()
