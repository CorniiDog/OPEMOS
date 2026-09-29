#!/usr/bin/env python3
"""Install the bounded OPEMOS Gamescope-to-Plasma visible-session fallback."""

import argparse
import os
import tempfile
from pathlib import Path


GAMESCOPE_DESKTOP = """[Desktop Entry]
Encoding=UTF-8
Name=SteamOS (gamescope with visible fallback)
Comment=SteamOS Big Picture session with bounded display fallback
Exec=/usr/local/libexec/opemos-gamescope-visible-fallback
Icon=steamicon.png
Type=Application
DesktopNames=gamescope
"""
SDDM_CONFIG = """# Managed by OPEMOS
[Autologin]
Session=opemos-gamescope-wayland.desktop
"""
REQUIRED = {
    "usr/bin/start-gamescope-session": "file",
    "usr/bin/startplasma-wayland": "file",
    "usr/lib/plasma-dbus-run-session-if-needed": "file",
    "usr/share/wayland-sessions/gamescope-wayland.desktop": "gamescope-desktop",
    "usr/share/wayland-sessions/plasma.desktop": "plasma-desktop",
}


def regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def validate_target(root: Path) -> None:
    for relative, kind in REQUIRED.items():
        path = root / relative
        if not regular(path):
            raise ValueError(f"required SteamOS session input is unsafe or absent: {relative}")
        if kind == "gamescope-desktop" and "Exec=start-gamescope-session" not in path.read_text(
            encoding="utf-8"
        ).splitlines():
            raise ValueError("SteamOS Gamescope session contract is unsupported")
        if kind == "plasma-desktop" and not any(
            line.startswith("Exec=") and "startplasma-wayland" in line
            for line in path.read_text(encoding="utf-8").splitlines()
        ):
            raise ValueError("SteamOS Plasma Wayland session contract is unsupported")


def validate_destination(root: Path, relative: str) -> Path:
    current = root
    parts = Path(relative).parts
    for part in parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise ValueError(f"fallback destination parent is unsafe: {relative}")
        else:
            current.mkdir(mode=0o755)
    destination = root / relative
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"fallback destination is unsafe: {relative}")
    return destination


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(root: Path, runtime: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("target root is unsafe")
    if not regular(runtime):
        raise ValueError("fallback runtime is unsafe or absent")
    validate_target(root)
    runtime_destination = validate_destination(
        root, "usr/local/libexec/opemos-gamescope-visible-fallback"
    )
    desktop_destination = validate_destination(
        root, "usr/local/share/wayland-sessions/opemos-gamescope-wayland.desktop"
    )
    config_destination = validate_destination(
        root, "etc/sddm.conf.d/zz-opemos-visible-session.conf"
    )
    atomic_write(
        runtime_destination,
        runtime.read_bytes(),
        0o755,
    )
    atomic_write(
        desktop_destination,
        GAMESCOPE_DESKTOP.encode(),
        0o644,
    )
    atomic_write(
        config_destination,
        SDDM_CONFIG.encode(),
        0o644,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    try:
        configure(args.root, args.runtime)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
