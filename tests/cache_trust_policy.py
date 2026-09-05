#!/usr/bin/env python3
"""Freeze authentication gates on reusable Core build inputs and outputs."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
target = (ROOT / "bootstrap/build_for_target.sh").read_text(encoding="utf-8")
compile_script = (ROOT / "bootstrap/compile.sh").read_text(encoding="utf-8")
assert "authenticated-headers" in target
assert 'HEADER_CACHE_ELIGIBLE=1' in target
assert target.index('HEADER_CACHE_ELIGIBLE=1') > target.index('[[ -n "$HEADER_KEYRING"')
assert target.index('HEADER_CACHE_HIT=1') < target.index('HEADER_SIGNATURE_STATUS=verified')
assert target.index('HEADER_SIGNATURE_STATUS=verified') < target.index('if [[ "${HEADER_CACHE_ELIGIBLE:-0}"')
for required in (
    'CACHED_TRUST" == "locally-built-verified',
    'validate_publish_inputs.py', 'CACHED_SOURCE" == "$SOURCE_COMMIT',
    'CACHED_SUPPORT" == "$SUPPORT_COMMIT', 'CACHED_CONTAINER" == "$CONTAINER_IMAGE_REF',
    'CACHE_CONTRACT_VALID" == "1',
):
    assert required in compile_script, f"compiled artifact cache omits gate: {required}"
assert compile_script.index('CACHE_CONTRACT_VALID=0') < compile_script.index('CACHE_HIT=1')
print("cache trust policy checks passed")
