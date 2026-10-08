#!/usr/bin/env python3
"""Use the existing Automatic source contract for the public installer."""

import argparse
import sys

from source_intent_contract import canonical, decision
from resolve_target import MAX_RELEASES_BYTES, read_bounded_regular, strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steamos", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--releases", required=True)
    parser.add_argument("--repository", default="CorniiDog/OPEMOS")
    parser.add_argument("--build-as-fallback", action="store_true")
    args = parser.parse_args()
    try:
        releases = strict_json(read_bounded_regular(args.releases, MAX_RELEASES_BYTES))
        if not isinstance(releases, list):
            raise ValueError("release metadata must be an array")
        intent = {"schemaVersion": 1, "kind": "opemos-source-intent",
                  "mode": "automatic", "selection": None,
                  "target": {"steamosVersion": args.steamos,
                             "kernelVersion": args.kernel, "architecture": "x86_64"}}
        document = decision(intent, releases, args.repository)
    except (OSError, ValueError) as error:
        parser.exit(2, f"automatic_install_selection.py: {error}\n")
    if (document["status"] == "authorized"
            and document["action"]["kind"] == "build_exact_target"
            and not args.build_as_fallback):
        parser.exit(1, "No published exact product; use --build-as-fallback to opt into the reviewed build.\n")
    sys.stdout.buffer.write(canonical(document))
    return 0 if document["status"] == "authorized" else 1


if __name__ == "__main__":
    raise SystemExit(main())
