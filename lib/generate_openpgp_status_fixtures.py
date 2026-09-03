#!/usr/bin/env python3
"""Emit deterministic OpenPGP status compatibility fixtures."""

import json
import sys

from userspace_lock_generation_contract import MAX_OPENPGP_STATUS_BYTES, canonical


PRIMARY = "A" * 40
SUBKEY = "B" * 40


def valid(hash_algorithm=8, signing=SUBKEY, primary=PRIMARY, version=4,
          signature_class="00"):
    return (
        f"[GNUPG:] NEWSIG\n"
        f"[GNUPG:] KEY_CONSIDERED {primary} 0\n"
        f"[GNUPG:] VALIDSIG {signing} 2026-09-03 1788436800 0 "
        f"{version} 0 1 {hash_algorithm} {signature_class} {primary}\n"
    )


def case(name, status, accepted):
    return {"name": name, "expected": {"accepted": accepted}, "status": status}


def matrix():
    cases = [
        case("valid-sha256-subkey", valid(8), True),
        case("valid-sha384-primary", valid(9, PRIMARY), True),
        case("valid-sha512-subkey", valid(10), True),
        case("weak-sha1", valid(2), False),
        case("wrong-primary", valid(primary="C" * 40), False),
        case("missing-primary", valid().rsplit(f" {PRIMARY}", 1)[0] + "\n", False),
        case("multiple-valid-signatures", valid() + valid(signing="C" * 40), False),
        case("valid-plus-revoked", valid() + "[GNUPG:] REVKEYSIG key user\n", False),
        case("valid-plus-expired", valid() + "[GNUPG:] EXPKEYSIG key user\n", False),
        case("signature-version-five", valid(version=5), False),
        case("text-signature-class", valid(signature_class="01"), False),
        case("lowercase-signing-fingerprint", valid(signing=SUBKEY.lower()), False),
        case("invalid-creation-date", valid().replace("2026-09-03", "2026-02-30"), False),
        case("non-status-output", "diagnostic\n" + valid(), False),
        case("empty-status", "", False),
        {
            "name": "oversized-status",
            "expected": {"accepted": False},
            "statusRecipe": {
                "kind": "append-padding",
                "baseCase": "valid-sha256-subkey",
                "paddingBytes": MAX_OPENPGP_STATUS_BYTES,
            },
        },
    ]
    return {
        "schemaVersion": 1,
        "kind": "opemos-openpgp-status-compatibility-fixtures",
        "signatureScheme": "openpgp-detached-v1",
        "expectedPrimaryFingerprint": PRIMARY,
        "limits": {
            "maxStatusBytes": MAX_OPENPGP_STATUS_BYTES,
            "maxCases": 32,
        },
        "cases": cases,
    }


def main():
    payload = canonical(matrix())
    if len(payload) > 256 * 1024:
        raise SystemExit("OpenPGP status compatibility matrix is excessive")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
