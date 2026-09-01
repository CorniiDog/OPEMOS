#!/usr/bin/env python3
"""Host regressions for target-owned hook and initramfs trust snapshots."""

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "lib/snapshot_target_execution.py"


def invoke(root, manifest, verify=False):
    option = "--verify" if verify else "--output"
    return subprocess.run([str(HELPER), "--root", str(root), option, str(manifest)],
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
    executor = root / "usr/share/libalpm/scripts/rebuild"
    executor.write_text("#!/bin/sh\nexit 0\n")
    executor.chmod(0o755)
    (root / "usr/share/libalpm/hooks/rebuild.hook").write_text(
        "[Trigger]\nType = Package\nOperation = Upgrade\nTarget = linux\n"
        "[Action]\nWhen = PostTransaction\nExec = /usr/share/libalpm/scripts/rebuild\n")
    (root / "etc/mkinitcpio.conf").write_text("HOOKS=(base)\n")
    (root / "etc/mkinitcpio.conf.d/base.conf").write_text("MODULES=()\n")
    return root


def rejected(mutator):
    with tempfile.TemporaryDirectory(prefix="target-execution-hostile-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        mutator(root, base)
        result = invoke(root, base / "manifest.json")
        assert result.returncode != 0, result.stdout


def main():
    with tempfile.TemporaryDirectory(prefix="target-execution-") as temporary:
        base = Path(temporary)
        root = fixture(base)
        manifest = base / "manifest.json"
        assert invoke(root, manifest).returncode == 0
        document = json.loads(manifest.read_text())
        paths = {record["path"] for record in document["files"]}
        assert "usr/bin/mkinitcpio" in paths
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


if __name__ == "__main__":
    main()
