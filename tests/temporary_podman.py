#!/usr/bin/env python3
"""Temporary Automatic-build dependencies preserve preexisting package state."""
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = "6.18.50-valve2-1-neptune-618-gc7289a96b14d"


def script(path, body):
    path.write_text("#!/bin/bash\nset -eu\n" + body)
    path.chmod(0o755)


def main():
    with tempfile.TemporaryDirectory(prefix="temporary-podman-fixture-") as temporary:
        root = Path(temporary)
        support = root / "support"
        (support / "bootstrap").mkdir(parents=True)
        for directory in ("lib", "policies", "profiles"):
            shutil.copytree(ROOT / directory, support / directory)
        shutil.copy2(ROOT / "bootstrap/setup_build_env.sh", support / "bootstrap/setup_build_env.sh")
        tools = root / "bin"
        tools.mkdir()
        # No host Podman, pacman, or sudo can be reached through this PATH.
        for name in ("python3", "dirname", "mktemp", "mkdir", "uname", "cp", "rm", "sleep"):
            (tools / name).symlink_to(shutil.which(name))
        script(tools / "sudo", 'exec "$@"\n')
        script(tools / "steamos-readonly", '''case "$1" in
status) cat "$MOCK_RO";;
enable|disable) echo "$1" >> "$MOCK_RO_LOG"; if [[ "$1" == enable ]]; then echo enabled > "$MOCK_RO"; else echo disabled > "$MOCK_RO"; fi;;
esac
'''.replace('cat "$MOCK_RO"', 'while read -r line; do echo "$line"; done < "$MOCK_RO"'))
        script(tools / "pacman", '''echo "$*" >> "$MOCK_PACKAGE_LOG"
case "$1" in
-Q) while read -r line; do echo "$line"; done < "$MOCK_DB";;
-Sp) echo 'podman 5'; echo 'dependency 1'; [[ "$MOCK_MODE" != upgrade ]] || echo 'existing 2';;
-S) [[ "$3" == --noconfirm && "$4" == --cachedir && -d "$5" && "$6" == podman ]] || exit 92
    printf 'owned package download' > "$5/podman.pkg.tar.zst"
    if [[ "$MOCK_MODE" == installfailure ]]; then printf 'existing 1\ndependency 1\n' > "$MOCK_DB"; exit 5; fi
    printf 'existing 1\npodman 5\ndependency 1\n' > "$MOCK_DB"
    printf '#!/bin/bash\nexit 0\n' > "$MOCK_BIN/podman"
    /bin/chmod 755 "$MOCK_BIN/podman";;
-R) [[ "$*" == '-R --noconfirm dependency podman' || "$*" == '-R --noconfirm dependency' ]] || exit 90
    echo 'existing 1' > "$MOCK_DB"; /bin/rm -f "$MOCK_BIN/podman";;
*) exit 91;;
esac
''')
        script(support / "bootstrap/build_automatic_for_target.sh", '''echo build > "$MOCK_BUILD"
case "$MOCK_MODE" in
failure) exit 76;;
changed) printf 'existing 2\npodman 5\ndependency 1\n' > "$MOCK_DB";;
replaced) python3 - <<'PY'
import os
from pathlib import Path
p = next(Path(os.environ['TMPDIR']).glob('opemos-podman-provision.*'))
p.rename(p.with_name(p.name + '.preserved'))
p.mkdir(mode=0o700)
(p / 'sentinel').write_text('replacement preserved')
PY
;;
cancel) while true; do sleep 0.1; done;;
esac
''')
        system = root / "system/etc"
        system.mkdir(parents=True)
        (system / "os-release").write_text('ID=steamos\nVERSION_ID="3.8.28"\n')
        releases = root / "releases"
        releases.write_text("[]\n")
        env = {**os.environ, "PATH": str(tools), "PROJECT_TEST_MODE": "1",
               "PROJECT_TEST_ROOT": str(root / "system"), "TMPDIR": str(root),
               "MOCK_DB": str(root / "packages"), "MOCK_BIN": str(tools),
               "MOCK_PACKAGE_LOG": str(root / "package-log"), "MOCK_RO": str(root / "readonly"),
               "MOCK_RO_LOG": str(root / "readonly-log"), "MOCK_BUILD": str(root / "build")}
        command = ["/bin/bash", str(support / "bootstrap/setup_build_env.sh"), "--temporary-podman",
                   "3.8.28", KERNEL, "575.64.05", str(releases), str(root / "output")]
        for mode, expected in (("success", 0), ("failure", 76), ("installfailure", 5), ("upgrade", 1), ("changed", 1), ("replaced", 1), ("unauthorized", 1), ("cancel", 143), ("preexisting", 0)):
            env["MOCK_MODE"] = mode
            (root / "packages").write_text("existing 1\n")
            (root / "readonly").write_text("enabled\n")
            for name in ("package-log", "readonly-log", "build"):
                (root / name).unlink(missing_ok=True)
            if mode == "preexisting":
                script(tools / "podman", 'exit 0\n')
            invocation = command.copy()
            if mode == "unauthorized":
                invocation[5] = "580.119.02"
            process = subprocess.Popen(invocation, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if mode == "cancel":
                deadline = time.monotonic() + 10
                while not (root / "build").exists() and time.monotonic() < deadline:
                    assert process.poll() is None
                    time.sleep(0.05)
                assert (root / "build").exists()
                process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=20)
            assert process.returncode == expected, (mode, out, err)
            assert (root / "readonly").read_text() == "enabled\n"
            if mode == "preexisting":
                assert (tools / "podman").exists()
                assert not (root / "package-log").exists()
                assert not (root / "readonly-log").exists()
                (tools / "podman").unlink()
            elif mode in ("changed", "replaced"):
                assert (tools / "podman").exists()
                assert (root / "packages").read_text().startswith("existing 2\n" if mode == "changed" else "existing 1\n")
                assert '-R ' not in (root / "package-log").read_text()
                assert list(root.glob("opemos-podman-provision.*"))
                if mode == "replaced":
                    assert any((p / "sentinel").is_file() for p in root.glob("opemos-podman-provision.*"))
                # Fixture-owned residue only, never production cleanup authority.
                (tools / "podman").unlink()
                for residue in root.glob("opemos-podman-provision.*"):
                    shutil.rmtree(residue)
            else:
                assert (root / "packages").read_text() == "existing 1\n"
                assert not (tools / "podman").exists()
                assert not list(root.glob("opemos-podman-provision.*"))
            if mode in ("upgrade", "unauthorized"):
                assert not (root / "build").exists()
                assert not (root / "readonly-log").exists()


if __name__ == "__main__":
    main()
