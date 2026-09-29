#!/usr/bin/env python3
"""Behavioral regressions for installed Gamescope visible-session fallback."""

import importlib.util
import stat
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runtime = load("gamescope_visible_fallback", ROOT / "lib/gamescope_visible_fallback.py")
installer = load("configure_gamescope_fallback", ROOT / "lib/configure_gamescope_fallback.py")


def target_fixture(root: Path) -> None:
    files = {
        "usr/bin/start-gamescope-session": "#!/bin/sh\n",
        "usr/bin/startplasma-wayland": "#!/bin/sh\n",
        "usr/lib/plasma-dbus-run-session-if-needed": "fixture\n",
        "usr/share/wayland-sessions/gamescope-wayland.desktop": (
            "[Desktop Entry]\nExec=start-gamescope-session\n"
        ),
        "usr/share/wayland-sessions/plasma.desktop": (
            "[Desktop Entry]\nExec=/usr/lib/plasma-dbus-run-session-if-needed "
            "/usr/bin/startplasma-wayland\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755 if relative.startswith("usr/bin/") else 0o644)


def main() -> None:
    crash = (
        "MESA: error: vdrm_device_connect failed\n"
        "radv/amdgpu: failed to initialize device\n"
    )
    assertion = "vkroots: CreateDispatchTable assertion failed; ABRT core dump\n"
    assert runtime.has_vulkan_initialization_failure(crash)
    assert runtime.has_vulkan_initialization_failure(assertion)
    assert not runtime.has_vulkan_initialization_failure("Gamescope Session Ended\n")
    assert not runtime.has_vulkan_initialization_failure("failed to initialize device\n")

    environment = runtime.plasma_environment(
        {"PATH": "/usr/bin", "XDG_DESKTOP_PORTAL_DIR": "/gamescope"}
    )
    assert environment["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert environment["GALLIUM_DRIVER"] == "llvmpipe"
    assert environment["XDG_SESSION_TYPE"] == "wayland"
    assert "XDG_DESKTOP_PORTAL_DIR" not in environment

    completed = mock.Mock(returncode=23)
    with mock.patch.object(runtime, "journal_cursor", return_value="cursor"), mock.patch.object(
        runtime.subprocess, "run", return_value=completed
    ), mock.patch.object(runtime, "journal_after", return_value="normal exit"), mock.patch.object(
        runtime.os, "execve"
    ) as execute:
        assert runtime.main() == 23
        execute.assert_not_called()

    with mock.patch.object(runtime, "journal_cursor", return_value="cursor"), mock.patch.object(
        runtime.subprocess, "run", return_value=completed
    ), mock.patch.object(runtime, "journal_after", return_value=crash), mock.patch.object(
        runtime, "run_bounded"
    ), mock.patch.object(runtime.os, "execve", side_effect=RuntimeError("executed")) as execute:
        try:
            runtime.main()
        except RuntimeError as error:
            assert str(error) == "executed"
        else:
            raise AssertionError("Vulkan failure did not launch the Plasma fallback")
        executable, arguments, fallback_environment = execute.call_args.args
        assert executable == runtime.PLASMA_RUNNER
        assert arguments == [runtime.PLASMA_RUNNER, runtime.PLASMA]
        assert fallback_environment["LIBGL_ALWAYS_SOFTWARE"] == "1"

    with tempfile.TemporaryDirectory(prefix="gamescope-fallback-") as temporary:
        root = Path(temporary) / "root"
        root.mkdir()
        target_fixture(root)
        installer.configure(root, ROOT / "lib/gamescope_visible_fallback.py")
        runtime_path = root / "usr/local/libexec/opemos-gamescope-visible-fallback"
        desktop = root / "usr/local/share/wayland-sessions/opemos-gamescope-wayland.desktop"
        config = root / "etc/sddm.conf.d/zz-opemos-visible-session.conf"
        assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o755
        assert "Exec=/usr/local/libexec/opemos-gamescope-visible-fallback" in desktop.read_text()
        assert "Session=opemos-gamescope-wayland.desktop" in config.read_text()
        installer.configure(root, ROOT / "lib/gamescope_visible_fallback.py")
        assert stat.S_IMODE(config.stat().st_mode) == 0o644

        (root / "usr/share/wayland-sessions/gamescope-wayland.desktop").write_text(
            "[Desktop Entry]\nExec=unexpected-session\n", encoding="utf-8"
        )
        try:
            installer.configure(root, ROOT / "lib/gamescope_visible_fallback.py")
        except ValueError as error:
            assert "unsupported" in str(error)
        else:
            raise AssertionError("unsupported Gamescope session was accepted")

    with tempfile.TemporaryDirectory(prefix="gamescope-fallback-symlink-") as temporary:
        root = Path(temporary) / "root"
        outside = Path(temporary) / "outside"
        root.mkdir()
        outside.mkdir()
        target_fixture(root)
        (root / "usr/local").mkdir(parents=True)
        (root / "usr/local/libexec").symlink_to(outside, target_is_directory=True)
        try:
            installer.configure(root, ROOT / "lib/gamescope_visible_fallback.py")
        except ValueError as error:
            assert "unsafe" in str(error)
        else:
            raise AssertionError("symlinked fallback destination was accepted")
        assert list(outside.iterdir()) == []


if __name__ == "__main__":
    main()
