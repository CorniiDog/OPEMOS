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
        "uname": "#!/bin/sh\n[ \"${1:-}\" = -m ] && echo x86_64 || echo Linux\n",
        "date": "#!/bin/sh\ncase \"${1:-}\" in --iso-8601=seconds) echo 2026-08-31T12:00:00+00:00;; *) /bin/date \"$@\";; esac\n",
        "gcc": "#!/bin/sh\ncase \" $* \" in *' -dumpfullversion -dumpversion '*) echo 15.1.1;; *) exit 0;; esac\n",
        "flock": "#!/bin/sh\nexit 0\n",
        "git": "#!/bin/sh\nexit 1\n",
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
        arguments.extend(["--headers-package", str(fixture / HEADERS_NAME)])
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
    process.kill()
    raise AssertionError("fault-injection child did not start")


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


def main():
    with tempfile.TemporaryDirectory(prefix="target-build-failures-") as temporary:
        fixture = Path(temporary)
        (fixture / "home").mkdir()
        make_source(fixture / "source")
        make_headers(fixture / HEADERS_NAME)
        make_mocks(fixture / "bin")
        run_failure(fixture, "fail", "header_download_failed")
        run_failure(fixture, "truncate", "header_identity_mismatch")
        run_cancellation(fixture, "sleep")
        run_cancellation(fixture, "unused", local_headers=True)
        run_output_exhaustion(fixture)


if __name__ == "__main__":
    main()
