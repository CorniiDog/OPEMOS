#!/usr/bin/env python3
"""Create a confined pacman config for already validated offline payloads."""

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from atomic_output import atomic_create_bytes

MAX_CONFIG_BYTES = 1024 * 1024
SECTION = re.compile(r"^[ \t]*\[([^]\r\n]+)\][ \t]*(?:#.*)?$")
CHECK_SPACE = re.compile(r"^[ \t]*CheckSpace[ \t]*(?:#.*)?$")
CHECK_SPACE_PREFIX = re.compile(r"^[ \t]*CheckSpace(?:[ \t=]|$)")
INCLUDE = re.compile(r"^[ \t]*Include[ \t]*=")
LOCAL_SIGLEVEL = re.compile(r"^[ \t]*LocalFileSigLevel[ \t]*=")


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--check-space-policy", choices=("disable-measured", "preserve"),
        default="disable-measured",
    )
    parser.add_argument(
        "--local-file-policy", choices=("preserve", "validated-derived"),
        default="preserve",
    )
    return parser.parse_args()


def fail(message):
    raise SystemExit(f"prepare_pacman_config.py: {message}")


def main():
    args = arguments()
    try:
        if (args.source.is_symlink() or not args.source.is_file()
                or not 0 < args.source.stat().st_size <= MAX_CONFIG_BYTES):
            fail("source pacman configuration is unsafe or excessive")
        text = args.source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        fail("source pacman configuration is unreadable")
    if "\x00" in text:
        fail("source pacman configuration contains invalid data")

    options = []
    in_options = False
    options_sections = 0
    check_space_count = 0
    local_siglevel_count = 0
    for line in text.splitlines(keepends=True):
        section = SECTION.fullmatch(line.rstrip("\r\n"))
        if section:
            in_options = section.group(1).strip().lower() == "options"
            if in_options:
                options_sections += 1
                options.append("[options]\n")
            continue
        if not in_options:
            continue
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            options.append(line)
            continue
        if INCLUDE.match(line):
            fail("option-level Include directives cannot be confined")
        if CHECK_SPACE.fullmatch(line.rstrip("\r\n")):
            check_space_count += 1
            if args.check_space_policy == "disable-measured":
                options.append("# CheckSpace disabled after measured Btrfs admission\n")
            else:
                options.append(line)
        elif CHECK_SPACE_PREFIX.match(line):
            fail("CheckSpace has an unsupported or ambiguous form")
        elif LOCAL_SIGLEVEL.match(line):
            local_siglevel_count += 1
            if args.local_file_policy == "validated-derived":
                options.append(
                    "LocalFileSigLevel = Never # exact derived hashes already validated\n"
                )
            else:
                options.append(line)
        else:
            options.append(line)

    if options_sections != 1:
        fail("source must contain exactly one options section")
    if check_space_count != 1:
        fail("source must contain exactly one active CheckSpace directive")
    if local_siglevel_count > 1:
        fail("source contains ambiguous LocalFileSigLevel directives")
    if (args.local_file_policy == "validated-derived"
            and local_siglevel_count == 0):
        options.append(
            "LocalFileSigLevel = Never # exact derived hashes already validated\n"
        )
    if not options or options[-1][-1:] != "\n":
        options.append("\n")
    try:
        atomic_create_bytes(args.output, "".join(options).encode("utf-8"), mode=0o600)
    except FileExistsError:
        fail("refusing to overwrite a pacman transaction configuration")
    except OSError:
        fail("pacman transaction configuration could not be created")


if __name__ == "__main__":
    main()
