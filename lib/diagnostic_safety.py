#!/usr/bin/env python3
"""Bound and redact diagnostic command output before contract emission."""

import re


def sanitize_diagnostic(value, maximum):
    if not value:
        return None
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise ValueError("maximum must be a positive integer")
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    value = re.sub(
        r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)\s+\S+",
        "authorization=<redacted>", value,
    )
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s/:]+/)*[^\s:]*", "<path>", value)
    value = re.sub(
        r"(?i)\b(token|password|secret|authorization|credential)\s*[:=]\s*\S+",
        r"\1=<redacted>", value,
    )
    value = " ".join(value.replace("\x00", " ").split())
    value = "".join(character if 32 <= ord(character) < 127 else "?" for character in value)
    return value[:maximum] or None
