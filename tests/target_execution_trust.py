#!/usr/bin/env python3
"""Host regressions for target-owned hook and initramfs trust snapshots."""

import json
import os
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


def rejected(mutator, expected_condition=None, expected_relative=None):
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
        if expected_relative is not None:
            assert document["targetRelativePath"] == expected_relative, document
        serialized = diagnostic.read_text()
        assert str(base) not in serialized
        assert len(serialized.encode()) <= 16 * 1024


def write_failure_result(base, diagnostic, *, reason="target_execution_trust",
                         phase="target_execution_trust"):
    result_path = base / f"result-{len(list(base.glob('result-*.json')))}.json"
    result = subprocess.run([
        sys.executable, str(RESULT_WRITER), "--output", str(result_path),
        "--status", "failed", "--reason", reason,
        "--message", "Target-owned execution trust validation failed.",
        "--phase", phase, "--root", "/target-root", "--kernel", "unknown",
        "--target-execution-failure", str(diagnostic),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result, result_path


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

    with tempfile.TemporaryDirectory(prefix="target-execution-nested-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        (root / "bin").unlink()
        (root / "opt").mkdir()
        (root / "opt/bin-link").symlink_to("../usr/bin", target_is_directory=True)
        (root / "bin").symlink_to("opt/bin-link", target_is_directory=True)
        manifest = base / "manifest.json"
        assert invoke(root, manifest).returncode == 0
        assert invoke(root, manifest, verify=True).returncode == 0

        (root / "opt/alternate-bin").mkdir()
        alternate = root / "opt/alternate-bin/bash"
        alternate.write_text("#!/bin/sh\nexit 7\n")
        alternate.chmod(0o755)
        (root / "bin").unlink()
        (root / "bin").symlink_to("opt/alternate-bin", target_is_directory=True)
        diagnostic = base / "retarget-diagnostic.json"
        changed = invoke(root, manifest, verify=True, diagnostic=diagnostic)
        assert changed.returncode != 0
        assert json.loads(diagnostic.read_text())["condition"] == (
            "execution_inputs_changed"
        )

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

    def broken_bin(root, base):
        (root / "bin").unlink()
        (root / "bin").symlink_to("missing-bin", target_is_directory=True)
    rejected(broken_bin, "required_input_missing", "bin/bash")

    def looping_bin(root, base):
        (root / "bin").unlink()
        (root / "bin").symlink_to("bin", target_is_directory=True)
    rejected(looping_bin, "required_input_missing", "bin/bash")

    def bin_target_is_file(root, base):
        (root / "bin").unlink()
        (root / "bin").symlink_to("usr/bin/bash")
    rejected(bin_target_is_file, "required_input_missing", "bin/bash")

    def linked_bash(root, base):
        bash = root / "usr/bin/bash"
        bash.rename(root / "usr/bin/bash.real")
        bash.symlink_to("bash.real")
    rejected(linked_bash, "input_symlink")

    def writable_bin_target(root, base):
        (root / "usr/bin").chmod(0o777)
    rejected(writable_bin_target, "unsafe_parent")

    def writable_alias_target(root, base):
        (root / "bin").unlink()
        target = root / "opt/bin"
        target.mkdir(parents=True)
        bash = target / "bash"
        bash.write_text("#!/bin/sh\nexit 0\n")
        bash.chmod(0o755)
        target.chmod(0o777)
        (root / "bin").symlink_to("opt/bin", target_is_directory=True)
    rejected(writable_alias_target, "unsafe_resolved_parent")

    if os.geteuid() == 0:
        def foreign_owned_bin(root, base):
            os.chown(root / "bin", 1, 1, follow_symlinks=False)
        rejected(foreign_owned_bin, "parent_symlink_ownership", "bin/bash")

    with tempfile.TemporaryDirectory(prefix="target-execution-result-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        (root / "bin").unlink()
        (root / "bin").symlink_to(base / "outside", target_is_directory=True)
        diagnostic = base / "diagnostic.json"
        failed = invoke(root, base / "manifest.json", diagnostic=diagnostic)
        assert failed.returncode != 0
        result, result_path = write_failure_result(base, diagnostic)
        assert result.returncode == 0, result.stderr
        written = json.loads(result_path.read_text())
        assert written["targetExecutionFailure"] == json.loads(
            diagnostic.read_text()
        )

        wrong_context, _ = write_failure_result(
            base, diagnostic, reason="module_install", phase="module_install"
        )
        assert wrong_context.returncode != 0

        malformed = json.loads(diagnostic.read_text())
        malformed["targetRelativePath"] = "/host/path"
        malformed_path = base / "malformed.json"
        malformed_path.write_text(json.dumps(malformed))
        malformed_result, _ = write_failure_result(base, malformed_path)
        assert malformed_result.returncode != 0

        duplicate_path = base / "duplicate.json"
        duplicate_path.write_text(
            diagnostic.read_text().replace(
                '"schemaVersion":1', '"schemaVersion":1,"schemaVersion":1'
            )
        )
        duplicate_result, _ = write_failure_result(base, duplicate_path)
        assert duplicate_result.returncode != 0


if __name__ == "__main__":
    main()
