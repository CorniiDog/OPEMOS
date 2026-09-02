#!/usr/bin/env python3
"""Host regressions for target-owned hook and initramfs trust snapshots."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib/snapshot_target_execution.py"
RESULT_WRITER = ROOT / "lib/write_install_result.py"


def invoke(root, manifest, verify=False, diagnostic=None):
    option = "--verify" if verify else "--output"
    command = [str(HELPER), "--root", str(root), option, str(manifest)]
    if diagnostic is not None:
        command.extend(("--diagnostic", str(diagnostic)))
    return subprocess.run(command,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def fixture(base):
    root = base / "target"
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/lib/initcpio/hooks").mkdir(parents=True)
    (root / "usr/lib/initcpio/install").mkdir(parents=True)
    (root / "usr/share/libalpm/hooks").mkdir(parents=True)
    (root / "usr/share/libalpm/scripts").mkdir(parents=True)
    (root / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (root / "etc/mkinitcpio.d").mkdir(parents=True)
    (root / "usr/bin/mkinitcpio").write_text("#!/bin/sh\nexit 0\n")
    (root / "usr/bin/mkinitcpio").chmod(0o755)
    (root / "usr/bin/lsinitcpio").write_text("#!/bin/sh\nexit 0\n")
    (root / "usr/bin/lsinitcpio").chmod(0o755)
    (root / "usr/bin/bash").write_text("#!/bin/sh\nexit 0\n")
    (root / "usr/bin/bash").chmod(0o755)
    (root / "bin").symlink_to("usr/bin", target_is_directory=True)
    executor = root / "usr/share/libalpm/scripts/rebuild"
    executor.write_text("#!/bin/sh\nexit 0\n")
    executor.chmod(0o755)
    (root / "usr/share/libalpm/hooks/rebuild.hook").write_text(
        "[Trigger]\nType = Package\nOperation = Upgrade\nTarget = linux\n"
        "[Action]\nWhen = PostTransaction\nExec = /usr/share/libalpm/scripts/rebuild\n")
    (root / "usr/share/libalpm/hooks/shell.hook").write_text(
        "[Trigger]\nType = Package\nOperation = Upgrade\nTarget = linux\n"
        "[Action]\nWhen = PostTransaction\nExec = /bin/bash -c true\n")
    (root / "etc/mkinitcpio.conf").write_text("HOOKS=(base)\n")
    (root / "etc/mkinitcpio.conf.d/base.conf").write_text("MODULES=()\n")
    return root


def rejected(mutator, expected_condition=None):
    with tempfile.TemporaryDirectory(prefix="target-execution-hostile-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        mutator(root, base)
        diagnostic = base / "diagnostic.json"
        result = invoke(root, base / "manifest.json", diagnostic=diagnostic)
        assert result.returncode != 0, result.stdout
        assert diagnostic.is_file()
        document = json.loads(diagnostic.read_text())
        assert document["status"] == "failed"
        assert document["reason"] == "target_execution_trust_failed"
        assert set(document) == {
            "schemaVersion", "status", "reason", "condition", "message",
            "targetRelativePath",
        }
        if expected_condition is not None:
            assert document["condition"] == expected_condition, document


def main():
    with tempfile.TemporaryDirectory(prefix="target-execution-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        manifest = base / "manifest.json"
        assert invoke(root, manifest).returncode == 0
        document = json.loads(manifest.read_text())
        paths = {record["path"] for record in document["files"]}
        assert "usr/bin/mkinitcpio" in paths
        assert "usr/bin/lsinitcpio" in paths
        assert "bin/bash" in paths
        assert "usr/share/libalpm/hooks/rebuild.hook" in paths
        assert "usr/share/libalpm/scripts/rebuild" in paths
        assert invoke(root, manifest, verify=True).returncode == 0
        (root / "etc/mkinitcpio.conf").write_text("HOOKS=(base udev)\n")
        assert invoke(root, manifest, verify=True).returncode != 0

    rejected(lambda root, base: (root / "etc/pacman.d/hooks").mkdir(parents=True))
    rejected(lambda root, base: (root / "usr/bin/mkinitcpio").chmod(0o777))

    def link_config(root, base):
        outside = base / "outside"
        outside.write_text("HOOKS=(hostile)\n")
        (root / "etc/mkinitcpio.conf").unlink()
        (root / "etc/mkinitcpio.conf").symlink_to(outside)
    rejected(link_config)

    def missing_executor(root, base):
        hook = root / "usr/share/libalpm/hooks/rebuild.hook"
        hook.write_text(hook.read_text().replace("/usr/share/libalpm/scripts/rebuild",
                                                 "/usr/local/bin/unknown"))
    rejected(missing_executor)

    def linked_executor(root, base):
        executor = root / "usr/share/libalpm/scripts/rebuild"
        executor.unlink()
        executor.symlink_to("/bin/sh")
    rejected(linked_executor)

    def escaping_bin(root, base):
        (root / "bin").unlink()
        outside = base / "outside-bin"
        outside.mkdir()
        (outside / "bash").write_text("#!/bin/sh\nexit 0\n")
        (outside / "bash").chmod(0o755)
        (root / "bin").symlink_to(outside, target_is_directory=True)
    rejected(escaping_bin, "input_escape")

    def writable_bin_target(root, base):
        (root / "usr/bin").chmod(0o777)
    rejected(writable_bin_target, "unsafe_parent")

    with tempfile.TemporaryDirectory(prefix="target-execution-result-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        (root / "bin").unlink()
        (root / "bin").symlink_to(base / "outside", target_is_directory=True)
        diagnostic = base / "diagnostic.json"
        failed = invoke(root, base / "manifest.json", diagnostic=diagnostic)
        assert failed.returncode != 0
        result_path = base / "result.json"
        result = subprocess.run([
            sys.executable, str(RESULT_WRITER), "--output", str(result_path),
            "--status", "failed", "--reason", "target_execution_trust",
            "--message", "Target-owned execution trust validation failed.",
            "--phase", "target_execution_trust", "--root", "/target-root",
            "--kernel", "unknown", "--target-execution-failure",
            str(diagnostic),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert result.returncode == 0, result.stderr
        written = json.loads(result_path.read_text())
        assert written["targetExecutionFailure"] == json.loads(
            diagnostic.read_text()
        )


if __name__ == "__main__":
    main()
