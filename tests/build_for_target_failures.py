#!/usr/bin/env python3
"""Fault-injection tests for the offline-target build orchestrator."""

import io
import json
import os
import signal
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "bootstrap/build_for_target.sh"
STEAMOS = "3.8.14"
KERNEL = "6.16.12-valve24.4-1-neptune-616-gfixture"
NVIDIA = "575.64.05"
HEADER_SIGNER = "889B5EBDDD505A683621900DAF1D2199EF0A3CCF"
HEADERS_NAME = (
    "linux-neptune-616-headers-6.16.12.valve24.4-1-x86_64.pkg.tar.zst"
)


def add_bytes(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def make_headers(path):
    build = f"usr/lib/modules/{KERNEL}/build"
    with tarfile.open(path, "w:gz") as archive:
        add_bytes(
            archive,
            ".PKGINFO",
            (
                "pkgname = linux-neptune-616-headers\n"
                "pkgver = 6.16.12.valve24.4-1\n"
                "arch = x86_64\n"
            ).encode(),
        )
        for relative, content in (
            ("Makefile", b"fixture\n"),
            ("include/generated/autoconf.h", b"fixture\n"),
            ("include/generated/compile.h", b'#define LINUX_COMPILER "gcc 15.1.1"\n'),
            ("Module.symvers", b"fixture\n"),
        ):
            add_bytes(archive, f"{build}/{relative}", content)


def make_source(path):
    path.mkdir()
    (path / "kernel-open").mkdir()
    (path / "Makefile").write_text("fixture\n", encoding="utf-8")
    (path / "version.mk").write_text(
        f"NVIDIA_VERSION = {NVIDIA}\n", encoding="utf-8"
    )


def make_mocks(path):
    path.mkdir()
    scripts = {
        "bsdtar": r"""#!/usr/bin/env python3
import sys, tarfile
args=sys.argv[1:]
mode=args[0]
package=args[1]
with tarfile.open(package) as archive:
    if mode == "-tf":
        for member in archive.getmembers(): print(member.name)
    elif mode == "-tvf":
        for member in archive.getmembers():
            kind = "d" if member.isdir() else "-"
            print(f"{kind}rw-r--r-- 0 0 0 {member.size} Jan 1 00:00 {member.name}")
    elif mode == "-xOf":
        sys.stdout.buffer.write(archive.extractfile(args[2]).read())
    elif mode == "-xf":
        destination=args[args.index("-C") + 1]
        archive.extractall(destination)
    else:
        raise SystemExit(2)
""",
        "uname": "#!/bin/sh\n[ \"${1:-}\" = -m ] && echo x86_64 || echo Linux\n",
        "date": "#!/bin/sh\ncase \"${1:-}\" in --iso-8601=seconds) echo 2026-08-31T12:00:00+00:00;; *) /bin/date \"$@\";; esac\n",
        "gcc": "#!/bin/sh\ncase \" $* \" in *' -dumpfullversion -dumpversion '*) echo ${MOCK_DEFAULT_GCC_VERSION:-15.1.1};; *) exit 0;; esac\n",
        "gcc-15": "#!/bin/sh\ncase \" $* \" in *' -dumpfullversion -dumpversion '*) echo ${MOCK_COMPAT_GCC_VERSION:-15.2.1};; *) exit 0;; esac\n",
        "flock": "#!/bin/sh\nexit 0\n",
        "git": "#!/bin/sh\nexit 1\n",
        "gpgv": f"#!/bin/sh\n[ \"${{MOCK_GPG_FAIL:-0}}\" != 1 ] || exit 1\necho '[GNUPG:] VALIDSIG {HEADER_SIGNER} 2026-01-01 0 4 0 1 10 00 {HEADER_SIGNER}'\n",
        "ld": "#!/bin/sh\necho 'GNU ld 2.45'\n",
        "modinfo": f"""#!/bin/sh
case "${{1:-}}" in
  -F)
    case "${{2:-}}" in
      version) echo {NVIDIA} ;;
      vermagic) echo '{KERNEL} SMP preempt mod_unload' ;;
      *) exit 1 ;;
    esac
    ;;
  --version) echo 'kmod version 34' ;;
  *) exit 1 ;;
esac
""",
        "nproc": "#!/bin/sh\necho 2\n",
        "readelf": "#!/bin/sh\necho 'Machine: Advanced Micro Devices X86-64'\n",
        "sed": """#!/bin/sh
case " $* " in
  *" /etc/os-release "*) echo 'Fedora Linux 44 (Fixture)' ;;
  *) exec /usr/bin/sed "$@" ;;
esac
""",
        "make": """#!/bin/sh
[ -z "${MOCK_MAKE_LOG:-}" ] || printf '%s\n' "$*" >> "$MOCK_MAKE_LOG"
case " $* " in
  *" clean "*) exit 0 ;;
  *" modules "*)
    if [ "${MOCK_MAKE_MODE:-sleep}" = complete ]; then
      source=
      previous=
      for argument in "$@"; do
        [ "$previous" != -C ] || source=$argument
        previous=$argument
      done
      for module in nvidia nvidia-drm nvidia-modeset nvidia-peermem nvidia-uvm; do
        printf 'fixture %s\n' "$module" > "${source:?}/kernel-open/$module.ko"
      done
    else
      echo $$ > "${MOCK_CHILD_PID:?}"
      : > "${MOCK_CHILD_STARTED:?}"
      trap 'exit 143' INT TERM
      sleep 30 & wait
    fi
    ;;
  *" --version "*) echo 'GNU Make 4.4' ;;
  *) exit 0 ;;
esac
""",
        "curl": """#!/bin/sh
output=
previous=
for argument in "$@"; do
    [ "$previous" != -o ] || output=$argument
    previous=$argument
done
case "${MOCK_CURL_MODE:?}" in
  fail) exit 22 ;;
  truncate) printf truncated > "${output:?}" ;;
  valid)
    case "$output" in
      *.sig) printf fixture-signature > "${output:?}" ;;
      *) cp "${MOCK_HEADERS_FIXTURE:?}" "${output:?}" ;;
    esac
    ;;
  sleep)
    echo $$ > "${MOCK_CHILD_PID:?}"
    : > "${MOCK_CHILD_STARTED:?}"
    trap 'exit 143' INT TERM
    sleep 30 & wait
    ;;
  *) exit 64 ;;
esac
""",
        "mv": """#!/bin/sh
if [ "${MOCK_OUTPUT_EXHAUSTED:-0}" = 1 ]; then
    echo 'mv: No space left on device' >&2
    exit 28
fi
exec /bin/mv "$@"
""",
    }
    for name, content in scripts.items():
        script = path / name
        script.write_text(content, encoding="utf-8")
        script.chmod(0o755)


def command(fixture, result, output, *, local_headers=False):
    arguments = [
        str(BUILDER),
        "--steamos", STEAMOS,
        "--kernel", KERNEL,
        "--nvidia", NVIDIA,
        "--source", str(fixture / "source"),
        "--output", str(output),
        "--result-json", str(result),
    ]
    if local_headers:
        arguments.extend([
            "--headers-package", str(fixture / HEADERS_NAME),
            "--headers-signature", str(fixture / f"{HEADERS_NAME}.sig"),
            "--header-keyring", str(fixture / "keyring.gpg"),
            "--header-signer", HEADER_SIGNER,
        ])
    else:
        arguments.extend([
            "--headers-url",
            "https://steamdeck-packages.steamos.cloud/archlinux-mirror/"
            f"jupiter-main/os/x86_64/{HEADERS_NAME}",
        ])
    return arguments


def environment(fixture, mode):
    env = os.environ.copy()
    env.update(
        PATH=f"{fixture / 'bin'}:{env['PATH']}",
        HOME=str(fixture / "home"),
        MOCK_CURL_MODE=mode,
        MOCK_CHILD_PID=str(fixture / "child.pid"),
        MOCK_CHILD_STARTED=str(fixture / "child.started"),
        MOCK_HEADERS_FIXTURE=str(fixture / HEADERS_NAME),
    )
    return env


def assert_clean(fixture, output):
    assert not list(output.glob("nvidia-open-*"))
    cache = fixture / "home/.cache/open-gpu-kernel-modules-steamos-support"
    assert not list(cache.glob("target-build.*"))


def run_failure(fixture, mode, expected_reason):
    result = fixture / f"{mode}.result.json"
    output = fixture / f"{mode}.output"
    completed = subprocess.run(
        command(fixture, result, output),
        env=environment(fixture, mode),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode != 0
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["status"] == "failed"
    assert document["reason"] == expected_reason, (document, completed.stderr)
    assert str(fixture) not in document["message"]
    assert_clean(fixture, output)


def wait_for(path, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process.poll() is None:
        if path.exists():
            return
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate()
    raise AssertionError(
        f"fault-injection child did not start: stdout={stdout!r} stderr={stderr!r}"
    )


def child_is_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def run_cancellation(fixture, mode, *, local_headers=False):
    for marker in (fixture / "child.pid", fixture / "child.started"):
        marker.unlink(missing_ok=True)
    result = fixture / f"cancel-{mode}.result.json"
    output = fixture / f"cancel-{mode}.output"
    process = subprocess.Popen(
        command(fixture, result, output, local_headers=local_headers),
        env=environment(fixture, mode),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wait_for(fixture / "child.started", process)
    child_pid = int((fixture / "child.pid").read_text())
    process.send_signal(signal.SIGTERM)
    _, stderr = process.communicate(timeout=5)
    assert process.returncode != 0, stderr
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not child_is_gone(child_pid):
        time.sleep(0.05)
    assert child_is_gone(child_pid), "cancelled child process remains alive"
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["status"] == "cancelled"
    assert document["reason"] == "cancelled"
    assert_clean(fixture, output)


def run_local_headers_success(fixture):
    result = fixture / "local-success.result.json"
    output = fixture / "local-success.output"
    env = environment(fixture, "unused")
    env.update(MOCK_MAKE_MODE="complete")
    completed = subprocess.run(
        command(fixture, result, output, local_headers=True),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["status"] == "success"
    assert document["reason"] == "build_complete"
    build_info = next(output.glob("*.build-info.txt")).read_text(encoding="utf-8")
    assert f"header_package={HEADERS_NAME}\n" in build_info
    assert "header_url=local-file\n" in build_info
    assert "header_authentication=detached-signature-verified-with-pinned-keyring\n" in build_info
    assert f"header_signing_key_fingerprint={HEADER_SIGNER}\n" in build_info
    assert len(list(output.glob("*.tar.gz"))) == 1
    assert len(list(output.glob("*.tar.gz.sha256"))) == 1
    assert len(list(output.glob("*.provenance.json"))) == 1
    cache = fixture / "home/.cache/open-gpu-kernel-modules-steamos-support"
    assert not list(cache.glob("target-build.*"))


def run_compiler_selection(fixture):
    make_log = fixture / "compiler.make.log"
    result = fixture / "compiler.result.json"
    output = fixture / "compiler.output"
    env = environment(fixture, "unused")
    env.update(
        MOCK_MAKE_MODE="complete",
        MOCK_DEFAULT_GCC_VERSION="16.2.1",
        MOCK_COMPAT_GCC_VERSION="15.2.1",
        MOCK_MAKE_LOG=str(make_log),
    )
    completed = subprocess.run(
        command(fixture, result, output, local_headers=True),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Using installed GCC 15 compatibility compiler." in completed.stdout
    build_info = next(output.glob("*.build-info.txt")).read_text(encoding="utf-8")
    assert "compiler_command=gcc-15\n" in build_info
    assert "compiler_version=15.2.1\n" in build_info
    assert "kernel_compiler_version=15.1.1\n" in build_info
    assert "compiler_major_match=1\n" in build_info
    module_calls = [line for line in make_log.read_text().splitlines() if " modules " in f" {line} "]
    assert len(module_calls) == 1 and "CC=gcc-15" in module_calls[0], module_calls

    mismatch_result = fixture / "compiler-mismatch.result.json"
    mismatch_output = fixture / "compiler-mismatch.output"
    mismatch_args = command(
        fixture, mismatch_result, mismatch_output, local_headers=True
    ) + ["--require-compiler-major-match"]
    mismatch_env = environment(fixture, "unused")
    mismatch_env.update(
        MOCK_MAKE_MODE="complete",
        MOCK_DEFAULT_GCC_VERSION="16.2.1",
        MOCK_COMPAT_GCC_VERSION="14.3.0",
    )
    mismatch = subprocess.run(
        mismatch_args, env=mismatch_env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    assert mismatch.returncode != 0
    document = json.loads(mismatch_result.read_text(encoding="utf-8"))
    assert document["reason"] == "compiler_policy_mismatch", document
    assert "does not match target kernel compiler major 15" in mismatch.stderr
    assert_clean(fixture, mismatch_output)


def run_output_exhaustion(fixture):
    result = fixture / "output-exhaustion.result.json"
    output = fixture / "output-exhaustion.output"
    env = environment(fixture, "unused")
    env.update(MOCK_MAKE_MODE="complete", MOCK_OUTPUT_EXHAUSTED="1")
    completed = subprocess.run(
        command(fixture, result, output, local_headers=True),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode != 0
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["status"] == "failed"
    assert document["reason"] == "packaging_failed", (
        document, completed.stdout, completed.stderr
    )
    assert "No space left on device" in completed.stderr
    assert_clean(fixture, output)


def run_authenticated_header_cache(fixture):
    keyring = fixture / "keyring.gpg"
    keyring.write_bytes(b"fixture")
    for index, curl_mode in enumerate(("valid", "fail"), start=1):
        result = fixture / f"cache-{index}.result.json"
        output = fixture / f"cache-{index}.output"
        arguments = command(fixture, result, output)
        arguments.extend(["--header-keyring", str(keyring), "--header-signer", HEADER_SIGNER])
        env = environment(fixture, curl_mode)
        env.update(MOCK_MAKE_MODE="complete")
        completed = subprocess.run(
            arguments, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert completed.returncode == 0, completed.stderr
    cache = fixture / "home/.cache/open-gpu-kernel-modules-steamos-support/authenticated-headers"
    assert (cache / HEADERS_NAME).is_file()
    assert (cache / f"{HEADERS_NAME}.sig").is_file()
    cache_hash = cache / f"{HEADERS_NAME}.sha256"
    assert cache_hash.is_file()
    assert cache_hash.read_text().strip() == __import__("hashlib").sha256(
        (cache / HEADERS_NAME).read_bytes()
    ).hexdigest()
    assert "Using cached authenticated Valve headers candidate" in (
        completed.stdout + completed.stderr
    )

    (cache / f"{HEADERS_NAME}.sig").write_bytes(b"corrupt-cached-signature")
    result = fixture / "cache-corrupt.result.json"
    output = fixture / "cache-corrupt.output"
    arguments = command(fixture, result, output)
    arguments.extend(["--header-keyring", str(keyring), "--header-signer", HEADER_SIGNER])
    env = environment(fixture, "fail")
    env.update(MOCK_MAKE_MODE="complete", MOCK_GPG_FAIL="1")
    rejected = subprocess.run(
        arguments, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert rejected.returncode != 0
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["reason"] == "header_signature_invalid", document
    assert "Using cached authenticated Valve headers candidate" in rejected.stdout
    assert_clean(fixture, output)

    # A package changed after authentication must miss the cache before gpgv.
    (cache / f"{HEADERS_NAME}.sig").write_bytes(b"fixture-signature")
    (cache / HEADERS_NAME).write_bytes(b"tampered-cached-package")
    result = fixture / "cache-hash-mismatch.result.json"
    output = fixture / "cache-hash-mismatch.output"
    arguments = command(fixture, result, output)
    arguments.extend(["--header-keyring", str(keyring), "--header-signer", HEADER_SIGNER])
    missed = subprocess.run(
        arguments, env=environment(fixture, "fail"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert missed.returncode != 0
    document = json.loads(result.read_text(encoding="utf-8"))
    assert document["reason"] == "header_download_failed", document
    assert "Ignoring cached Valve headers" in missed.stderr
    assert "Using cached authenticated Valve headers candidate" not in missed.stdout
    assert_clean(fixture, output)

    for label, hash_text in (("missing", None), ("malformed", "not-a-sha256\n")):
        (cache / HEADERS_NAME).write_bytes((fixture / HEADERS_NAME).read_bytes())
        (cache / f"{HEADERS_NAME}.sig").write_bytes(b"fixture-signature")
        if hash_text is None:
            cache_hash.unlink(missing_ok=True)
        else:
            cache_hash.write_text(hash_text)
        result = fixture / f"cache-hash-{label}.result.json"
        output = fixture / f"cache-hash-{label}.output"
        arguments = command(fixture, result, output)
        arguments.extend(["--header-keyring", str(keyring), "--header-signer", HEADER_SIGNER])
        incomplete = subprocess.run(
            arguments, env=environment(fixture, "fail"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert incomplete.returncode != 0
        document = json.loads(result.read_text(encoding="utf-8"))
        assert document["reason"] == "header_download_failed", (label, document)
        assert "Using cached authenticated Valve headers candidate" not in incomplete.stdout
        assert_clean(fixture, output)


def main():
    with tempfile.TemporaryDirectory(prefix="target-build-failures-") as temporary:
        fixture = Path(temporary)
        (fixture / "home").mkdir()
        make_source(fixture / "source")
        make_headers(fixture / HEADERS_NAME)
        (fixture / f"{HEADERS_NAME}.sig").write_bytes(b"fixture-signature")
        (fixture / "keyring.gpg").write_bytes(b"fixture-keyring")
        make_mocks(fixture / "bin")
        run_failure(fixture, "fail", "header_download_failed")
        run_failure(fixture, "truncate", "header_identity_mismatch")
        run_cancellation(fixture, "sleep")
        run_cancellation(fixture, "unused", local_headers=True)
        run_local_headers_success(fixture)
        run_compiler_selection(fixture)
        run_output_exhaustion(fixture)
        run_authenticated_header_cache(fixture)


if __name__ == "__main__":
    main()
