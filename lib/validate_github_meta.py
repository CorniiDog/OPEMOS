#!/usr/bin/env python3
"""Validate a bounded GitHub Meta response used only as a connectivity probe."""

import ipaddress
import json
import sys

MAX_RESPONSE_BYTES = 64 * 1024
MAX_HOOKS = 1024


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def main():
    data = sys.stdin.buffer.read(MAX_RESPONSE_BYTES + 1)
    if not data or len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("response is empty or excessive")
    document = json.loads(data.decode("utf-8"), object_pairs_hook=strict_object)
    if not isinstance(document, dict):
        raise ValueError("response is not an object")
    hooks = document.get("hooks")
    if not isinstance(hooks, list) or not 1 <= len(hooks) <= MAX_HOOKS:
        raise ValueError("hooks are missing or malformed")
    for value in hooks:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("hook network is malformed")
        ipaddress.ip_network(value, strict=True)


if __name__ == "__main__":
    try:
        main()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"validate_github_meta.py: {error}") from None
