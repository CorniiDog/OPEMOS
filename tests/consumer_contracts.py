#!/usr/bin/env python3
"""Canonical cross-frontend schema and immutable bundle-manifest tests."""

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from installer_bundle_manifest import (  # noqa: E402
    ContractError,
    FILES,
    build_manifest,
    canonical,
    validate_inventory,
    validate_manifest,
    write_create_only,
)
from validate_install_contract import (  # noqa: E402
    MAX_PROGRESS_BYTES,
    MAX_RESULT_BYTES,
    load_document as load_install_result,
    validate_progress,
    validate_result,
)
from write_install_result import (  # noqa: E402
    MAX_INITRAMFS_VERIFICATION_BYTES,
    MAX_MODULE_VERIFICATION_BYTES,
    MAX_PAYLOAD_RECEIPT_BYTES,
    MAX_USERSPACE_VERIFICATION_BYTES,
    MAX_WORKSPACE_VERIFICATION_BYTES,
    load_initramfs_verification,
    load_initramfs_workspace,
    load_module_verification,
    load_payload_receipt,
    load_userspace_verification,
    validate_initramfs_verification_binding,
    validate_initramfs_workspace_binding,
    validate_module_verification_binding,
    validate_payload_receipt_binding,
    validate_userspace_verification_binding,
    validate_verified_metadata,
)
import resolve_target as resolver_module  # noqa: E402
from resolve_target import resolve_target  # noqa: E402


def command(*arguments, cwd):
    subprocess.run(arguments, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def expect_failure(function, *arguments):
    try:
        function(*arguments)
    except (ContractError, OSError):
        return
    raise AssertionError("unsafe contract input was accepted")


def assert_expected(actual, expected):
    """Compare a fixture's intentional stable subset without freezing messages."""
    assert isinstance(actual, dict) and isinstance(expected, dict)
    for key, value in expected.items():
        if isinstance(value, dict):
            assert_expected(actual[key], value)
        else:
            assert actual[key] == value


def validate_resolver_compatibility_fixtures(path):
    payload = resolver_module.read_bounded_regular(path, 512 * 1024)
    document = resolver_module.strict_json(payload)
    assert set(document) == {
        "schemaVersion", "kind", "repository", "resolverSchemaVersion", "cases"
    }
    assert document["schemaVersion"] == 1
    assert document["kind"] == "opemos-resolver-compatibility-fixtures"
    assert document["repository"] == "CorniiDog/OPEMOS"
    assert document["resolverSchemaVersion"] == 2
    cases = document["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    names = []
    for case in cases:
        assert isinstance(case, dict) and set(case) == {
            "name", "target", "releases", "expected", "absentFields"
        }
        names.append(case["name"])
        assert isinstance(case["name"], str)
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["name"])
        assert isinstance(case["releases"], list) and len(case["releases"]) <= 2000
        assert isinstance(case["expected"], dict) and case["expected"]
        assert isinstance(case["absentFields"], list)
        assert case["absentFields"] == sorted(set(case["absentFields"]))
        assert len(case["absentFields"]) <= 16
        assert all(
            isinstance(field, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", field)
            for field in case["absentFields"]
        )
        target = case["target"]
        assert set(target) == {"steamosVersion", "kernelVersion", "architecture"}
        actual = resolve_target(
            target["steamosVersion"], target["kernelVersion"],
            target["architecture"], case["releases"], document["repository"],
        )
        assert actual == resolve_target(
            target["steamosVersion"], target["kernelVersion"],
            target["architecture"], copy.deepcopy(case["releases"]),
            document["repository"],
        )
        assert actual["schemaVersion"] == document["resolverSchemaVersion"]
        assert_expected(actual, case["expected"])
        assert all(field not in actual for field in case["absentFields"])
        validate_resolver_fixture(actual)
    assert len(names) == len(set(names))
    assert set(names) == {
        "invalid-steamos", "invalid-kernel", "unsupported-architecture",
        "malformed-release-metadata", "duplicate-release-metadata",
        "incomplete-canonical-assets", "duplicate-canonical-asset",
        "unreviewed-exact-target", "reviewed-exact-target-build",
    }

    with tempfile.TemporaryDirectory(prefix="opemos-fixture-confinement-") as temporary:
        root = Path(temporary)
        duplicate = root / "duplicate.json"
        duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
        try:
            resolver_module.strict_json(
                resolver_module.read_bounded_regular(duplicate, 512 * 1024)
            )
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate fixture JSON key was accepted")
        linked = root / "linked.json"
        linked.symlink_to(path)
        try:
            resolver_module.read_bounded_regular(linked, 512 * 1024)
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError("linked fixture document was accepted")


def validate_installer_result_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].endswith(b"\n")
    document = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(document) == {
        "schemaVersion", "kind", "resultSchemaVersion", "unfrozenFields", "cases"
    }
    assert document["schemaVersion"] == 1
    assert document["kind"] == "opemos-installer-result-compatibility-fixtures"
    assert document["resultSchemaVersion"] == 1
    assert document["unfrozenFields"] == ["message"]
    cases = document["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-result-fixtures-") as temporary:
        result_path = Path(temporary) / "result.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) in ({"accepted"}, {"accepted", "status"})
            assert isinstance(expected["accepted"], bool)
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            else:
                assert expected == {"accepted": False}
                payload = fixture["rawDocument"]
            assert 1 <= len(payload.encode()) <= MAX_RESULT_BYTES
            result_path.write_text(payload, encoding="utf-8")
            try:
                parsed = validate_result(result_path)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                accepted = False
            else:
                accepted = True
                assert parsed["schemaVersion"] == document["resultSchemaVersion"]
                assert parsed["status"] == expected["status"]
                if parsed["status"] in {"success", "validated"}:
                    validation = parsed.get("validation")
                    assert isinstance(validation, dict)
                    validate_verified_metadata(validation)
                    assert set(validation) >= {
                        "archiveSha256", "provenanceSha256", "userspaceLock",
                        "pacmanDatabase", "boot", "keyring", "packages", "modules",
                        "storage", "packageDependencyClosure", "compression",
                        "gamingPayload",
                    }
                    workspace = parsed.get("initramfsWorkspace")
                    assert isinstance(workspace, dict)
                    assert {"requiredBytes", "requiredInodes", "availableBytes",
                            "availableInodes", "inodeCapacityMode", "mode"} <= set(workspace)
                elif "moduleVerification" in parsed:
                    nested = Path(temporary) / "failed-module-verification.json"
                    nested.write_text(json.dumps(parsed["moduleVerification"]), encoding="utf-8")
                    assert load_module_verification(nested)["status"] == "failed"
                    assert "userspaceVerification" not in parsed
                elif "userspaceVerification" in parsed:
                    nested = Path(temporary) / "failed-userspace-verification.json"
                    nested.write_text(json.dumps(parsed["userspaceVerification"]), encoding="utf-8")
                    assert load_userspace_verification(nested)["status"] == "failed"
                    assert "moduleVerification" not in parsed
                else:
                    raise AssertionError("accepted failed result lacks actionable diagnostics")
            assert accepted is expected["accepted"], name
        assert len(names) == len(set(names))
        assert set(names) == {
            "validated-success", "mutation-success", "safe-additive-fields",
            "failed-module-diagnostic", "failed-userspace-diagnostic",
            "missing-module-verification", "missing-userspace-verification",
            "missing-workspace-verification", "missing-initramfs-verification",
            "missing-payload-receipt", "target-proof-mismatch",
            "module-payload-binding-mismatch",
            "unsafe-input-identity", "cleanup-incomplete", "malformed-json",
            "duplicate-json-key",
        }
        linked = Path(temporary) / "linked-result.json"
        linked.symlink_to(result_path)
        try:
            load_install_result(linked, MAX_RESULT_BYTES)
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError("linked installer-result fixture was accepted")


def validate_installer_progress_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].endswith(b"\n")
    document = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(document) == {
        "schemaVersion", "kind", "progressSchemaVersion", "unfrozenFields",
        "limits", "cases",
    }
    assert document["schemaVersion"] == 1
    assert document["kind"] == "opemos-installer-progress-compatibility-fixtures"
    assert document["progressSchemaVersion"] == 1
    assert document["unfrozenFields"] == ["message"]
    assert document["limits"] == {
        "maxLineBytes": 4096, "maxStreamBytes": MAX_PROGRESS_BYTES,
    }
    cases = document["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-progress-fixtures-") as temporary:
        progress_path = Path(temporary) / "progress.log"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "stream"},
                {"name", "expected", "streamRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) in ({"accepted"}, {"accepted", "progressRecords"})
            assert isinstance(expected["accepted"], bool)
            if "stream" in fixture:
                stream = fixture["stream"]
                assert isinstance(stream, str) and stream
                assert len(stream.encode()) <= 512 * 1024
                progress_path.write_text(stream, encoding="utf-8")
            else:
                recipe = fixture["streamRecipe"]
                assert set(recipe) == {"kind", "text", "count"}
                assert recipe["kind"] == "repeat"
                assert isinstance(recipe["text"], str) and 1 <= len(recipe["text"]) <= 256
                assert isinstance(recipe["count"], int) and not isinstance(recipe["count"], bool)
                assert 1 <= recipe["count"] <= MAX_PROGRESS_BYTES + 1
                expanded_bytes = len(recipe["text"].encode()) * recipe["count"]
                assert MAX_PROGRESS_BYTES < expanded_bytes <= MAX_PROGRESS_BYTES + 4096
                with progress_path.open("w", encoding="utf-8") as output:
                    output.write(recipe["text"] * recipe["count"])
                assert progress_path.stat().st_size > MAX_PROGRESS_BYTES
            try:
                count = validate_progress(progress_path)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                accepted = False
            else:
                accepted = True
                assert count == expected["progressRecords"]
            assert accepted is expected["accepted"], name
        assert len(names) == len(set(names))
        assert set(names) == {
            "indeterminate-heartbeats", "monotonic-bytes", "monotonic-items",
            "phase-transition-reset", "attempt-advancement-reset",
            "unknown-additive-fields", "unknown-phase-token",
            "non-protocol-noise-ignored", "attempt-regression",
            "completed-regression", "total-change", "unit-change",
            "determinate-fields-on-indeterminate", "missing-determinate-fields",
            "completed-exceeds-total", "zero-total", "unsupported-schema-version",
            "invalid-phase-token", "malformed-json", "duplicate-json-key",
            "non-finite-json", "oversized-line", "oversized-stream",
            "no-progress-records",
        }
        progress_path.write_text(
            "STEAMOS_NVIDIA_PROGRESS "
            '{"schemaVersion":1,"attempt":1,"phase":"hashing",'
            '"indeterminate":true}\n',
            encoding="utf-8",
        )
        linked = Path(temporary) / "linked-progress.log"
        linked.symlink_to(progress_path)
        try:
            validate_progress(linked)
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError("linked installer-progress fixture was accepted")


def validate_installer_validation_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "validationSchemaVersion", "unfrozenFields",
        "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == "opemos-installer-validation-compatibility-fixtures"
    assert matrix["validationSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == ["message"]
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    for fixture in cases:
        assert isinstance(fixture, dict)
        assert set(fixture) in (
            {"name", "expected", "document"},
            {"name", "expected", "documentRecipe"},
            {"name", "expected", "rawDocument"},
        )
        name = fixture["name"]
        names.append(name)
        assert isinstance(name, str)
        assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
        assert fixture["expected"] in ({"accepted": True}, {"accepted": False})
        if "document" in fixture:
            candidate = copy.deepcopy(fixture["document"])
        elif "documentRecipe" in fixture:
            recipe = fixture["documentRecipe"]
            assert recipe == {
                "kind": "extend-dependency-closure",
                "baseCase": "valid-direct-input",
                "additionalRecords": 4091,
            }
            candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
            candidate["packageDependencyClosure"].extend(
                {"name": f"installed-{index}", "version": "1-1", "source": "installed"}
                for index in range(recipe["additionalRecords"])
            )
            assert len(candidate["packageDependencyClosure"]) == 4097
        else:
            try:
                candidate = resolver_module.strict_json(fixture["rawDocument"].encode())
            except (UnicodeError, json.JSONDecodeError, ValueError):
                accepted = False
            else:
                accepted = True
            assert accepted is fixture["expected"]["accepted"], name
            continue
        try:
            validate_verified_metadata(candidate)
        except SystemExit:
            accepted = False
        else:
            accepted = True
        assert accepted is fixture["expected"]["accepted"], name
    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-direct-input", "valid-authenticated-bundle-input",
        "safe-additive-fields", "missing-input-source",
        "missing-archive-identity", "missing-boot-policy", "missing-storage",
        "input-source-identity-mismatch", "invalid-archive-hash",
        "unsafe-lock-filename", "boot-policy-mismatch",
        "dependency-version-mismatch", "duplicate-package-identity",
        "compression-storage-mismatch", "root-metadata-reserve-mismatch",
        "var-reserve-mismatch", "dependency-closure-limit",
        "malformed-json", "duplicate-json-key", "non-finite-json",
    }


def validate_installer_module_verification_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "moduleVerificationSchemaVersion",
        "targetKernel", "validationModules", "unfrozenFields", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-installer-module-verification-compatibility-fixtures"
    )
    assert matrix["moduleVerificationSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == ["message"]
    assert matrix["limits"] == {
        "maxDocumentBytes": MAX_MODULE_VERIFICATION_BYTES
    }
    assert isinstance(matrix["validationModules"], list)
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-module-fixtures-") as temporary:
        root = Path(temporary)
        candidate_path = root / "module-verification.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
                {"name", "expected", "documentRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {"recordAccepted", "successProofAccepted"}
            assert all(isinstance(value, bool) for value in expected.values())
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            elif "rawDocument" in fixture:
                payload = fixture["rawDocument"]
                assert isinstance(payload, str) and payload
            else:
                recipe = fixture["documentRecipe"]
                assert recipe == {
                    "kind": "top-level-padding",
                    "baseCase": "valid-normalized-success",
                    "paddingBytes": MAX_MODULE_VERIFICATION_BYTES,
                }
                candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
                candidate["padding"] = "x" * recipe["paddingBytes"]
                payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                assert (MAX_MODULE_VERIFICATION_BYTES < len(payload.encode())
                        <= MAX_MODULE_VERIFICATION_BYTES + 16384)
            candidate_path.write_text(payload, encoding="utf-8")
            try:
                parsed = load_module_verification(candidate_path)
            except SystemExit:
                record_accepted = False
                success_accepted = False
            else:
                record_accepted = True
                try:
                    if parsed["status"] != "verified":
                        raise SystemExit("failure diagnostics are not success proofs")
                    validate_module_verification_binding(
                        matrix["validationModules"], parsed, matrix["targetKernel"]
                    )
                except SystemExit:
                    success_accepted = False
                else:
                    success_accepted = True
            assert record_accepted is expected["recordAccepted"], name
            assert success_accepted is expected["successProofAccepted"], name

        linked = root / "linked-module-verification.json"
        linked.symlink_to(candidate_path)
        try:
            load_module_verification(linked)
        except SystemExit:
            pass
        else:
            raise AssertionError("linked module-verification fixture was accepted")

    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-normalized-success", "safe-additive-top-level",
        "valid-failure-diagnostic", "missing-module",
        "duplicate-module-identity", "unknown-module-identity",
        "oversized-module-set", "payload-hash-binding-mismatch",
        "actual-payload-hash-mismatch", "raw-representation",
        "wrong-kernel-path", "path-traversal", "mode-mismatch",
        "uid-mismatch", "gid-mismatch", "decompression-mismatch",
        "zero-compressed-size", "missing-required-field",
        "unknown-record-field", "malformed-json", "duplicate-json-key",
        "non-finite-json", "oversized-document",
    }


def validate_installer_userspace_verification_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "userspaceVerificationSchemaVersion",
        "targetNvidiaVersion", "validation", "unfrozenFields", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-installer-userspace-verification-compatibility-fixtures"
    )
    assert matrix["userspaceVerificationSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == ["message"]
    assert matrix["limits"] == {
        "maxDocumentBytes": MAX_USERSPACE_VERIFICATION_BYTES
    }
    validate_verified_metadata(matrix["validation"])
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-userspace-fixtures-") as temporary:
        root = Path(temporary)
        candidate_path = root / "userspace-verification.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
                {"name", "expected", "documentRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {"recordAccepted", "successProofAccepted"}
            assert all(isinstance(value, bool) for value in expected.values())
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            elif "rawDocument" in fixture:
                payload = fixture["rawDocument"]
                assert isinstance(payload, str) and payload
            else:
                recipe = fixture["documentRecipe"]
                assert recipe == {
                    "kind": "top-level-padding",
                    "baseCase": "valid-normalized-success",
                    "paddingBytes": MAX_USERSPACE_VERIFICATION_BYTES,
                }
                candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
                candidate["padding"] = "x" * recipe["paddingBytes"]
                payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                assert (MAX_USERSPACE_VERIFICATION_BYTES < len(payload.encode())
                        <= MAX_USERSPACE_VERIFICATION_BYTES + 32768)
            candidate_path.write_text(payload, encoding="utf-8")
            try:
                parsed = load_userspace_verification(candidate_path)
            except SystemExit:
                record_accepted = False
                success_accepted = False
            else:
                record_accepted = True
                try:
                    validate_userspace_verification_binding(
                        matrix["validation"], parsed,
                        matrix["targetNvidiaVersion"],
                    )
                except SystemExit:
                    success_accepted = False
                else:
                    success_accepted = True
            assert record_accepted is expected["recordAccepted"], name
            assert success_accepted is expected["successProofAccepted"], name
        linked = root / "linked-userspace-verification.json"
        linked.symlink_to(candidate_path)
        try:
            load_userspace_verification(linked)
        except SystemExit:
            pass
        else:
            raise AssertionError("linked userspace-verification fixture was accepted")

    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-normalized-success", "safe-additive-top-level",
        "valid-failure-diagnostic", "missing-package", "extra-package",
        "duplicate-package", "lock-binding-mismatch",
        "provenance-binding-mismatch", "filename-mismatch", "version-mismatch",
        "package-hash-mismatch", "dependencies-mismatch", "provides-mismatch",
        "query-not-verified", "pacman-integrity-not-verified",
        "payload-not-verified", "database-not-consistent",
        "database-count-mismatch", "database-path-mismatch",
        "payload-path-unconfined", "payload-hash-not-verified",
        "payload-mode-not-verified", "payload-ownership-not-verified",
        "payload-link-not-verified", "duplicate-dependency-relation",
        "duplicate-provider-relation", "reordered-relations",
        "unsafe-package-filename",
        "oversized-relations", "firmware-version-mismatch",
        "firmware-path-escape", "missing-firmware",
        "duplicate-firmware-path", "non-gsp-firmware-name",
        "zero-payload-entries", "shared-library-count-inconsistent",
        "unknown-package-field",
        "malformed-json", "duplicate-json-key", "non-finite-json",
        "oversized-document",
    }


def validate_resolver_fixture(document):
    assert document["schemaVersion"] == 2
    assert document["status"] in {
        "compatible", "invalid_target", "no_compatible_artifact",
        "resolver_error", "unsupported_target",
    }
    target = document["target"]
    assert set(target) == {"steamosVersion", "kernelVersion", "architecture"}
    if document["status"] == "compatible":
        assert document["compatibility"] in {"exact", "same_series_fallback"}
        assert document["artifact"]["trust"]["classification"] == (
            "pending-provenance-verification"
        )
        assert document["artifact"]["checksum"]["algorithm"] == "sha256"
        assert {"optionalCudaOmission"} <= set(document["capabilities"])
    else:
        assert isinstance(document["reason"], str) and isinstance(document["message"], str)
        if document["reason"] == "no_compatible_release":
            action = document["nextAction"]
            assert {key: action[key] for key in (
                "schemaVersion", "kind", "entrypoint",
                "executionArchitecture", "kernelPolicy",
            )} == {
                "schemaVersion": 1,
                "kind": "build_exact_target",
                "entrypoint": "bootstrap/build_for_target.sh",
                "executionArchitecture": "x86_64",
                "kernelPolicy": "exact",
            }
            plan = action["buildPlan"]
            assert plan["schemaVersion"] == 1
            assert plan["target"]["kernelVersion"] == target["kernelVersion"]
            assert plan["target"]["nvidiaVersion"] == "575.64.05"
            assert plan["source"]["ref"] == "refs/heads/nvidia/575.64.05"
            assert len(plan["source"]["commit"]) == 40
            assert len(plan["policy"]["sha256"]) == 64
        else:
            assert "nextAction" not in document


def main():
    validate_inventory(FILES)
    for relative, _role, expected_mode in FILES:
        path = ROOT / relative
        info = path.lstat()
        assert path.is_file() and not path.is_symlink(), relative
        actual_mode = "0755" if info.st_mode & 0o111 else "0644"
        assert actual_mode == expected_mode, relative

    schema_root = ROOT / "contracts/schemas"
    resolver_schema = json.loads(
        (schema_root / "resolver-result-v2.schema.json").read_text(encoding="utf-8")
    )
    progress_schema = json.loads(
        (schema_root / "installer-progress-v1.schema.json").read_text(encoding="utf-8")
    )
    result_schema = json.loads(
        (schema_root / "installer-result-v1.schema.json").read_text(encoding="utf-8")
    )
    validation_schema = json.loads(
        (schema_root / "installer-validation-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    module_verification_schema = json.loads(
        (schema_root / "installer-module-verification-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    userspace_verification_schema = json.loads(
        (schema_root / "installer-userspace-verification-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    initramfs_verification_schema = json.loads(
        (schema_root / "installer-initramfs-verification-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    payload_receipt_schema = json.loads(
        (schema_root / "installer-payload-receipt-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    workspace_schema = json.loads(
        (schema_root / "installer-initramfs-workspace-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert resolver_schema["$schema"].endswith("2020-12/schema")
    assert resolver_schema["properties"]["schemaVersion"]["const"] == 2
    assert resolver_schema["unevaluatedProperties"] is True
    assert progress_schema["properties"]["schemaVersion"]["const"] == 1
    assert progress_schema["properties"]["indeterminate"]["type"] == "boolean"
    assert progress_schema["unevaluatedProperties"] is True
    assert result_schema["$schema"].endswith("2020-12/schema")
    assert result_schema["properties"]["schemaVersion"]["const"] == 1
    assert result_schema["properties"]["status"]["enum"] == [
        "success", "failed", "cancelled", "validated"
    ]
    success_contract = result_schema["allOf"][0]["then"]
    assert success_contract["required"] == [
        "validation", "moduleVerification", "userspaceVerification",
        "initramfsWorkspace", "initramfsVerification", "payloadReceipt",
    ]
    assert result_schema["properties"]["moduleVerification"]["$ref"] == (
        "installer-module-verification-v1.schema.json"
    )
    assert result_schema["unevaluatedProperties"] is True
    assert validation_schema["$schema"].endswith("2020-12/schema")
    assert validation_schema["properties"]["inputSource"]["required"] == [
        "mode", "bundleCacheId"
    ]
    assert set(validation_schema["required"]) == {
        "inputSource", "archiveSha256", "provenanceSha256", "userspaceLock",
        "pacmanDatabase", "boot", "keyring", "packages", "modules", "storage",
        "packageDependencyClosure", "compression", "gamingPayload",
    }
    assert validation_schema["properties"]["packages"]["maxItems"] == 64
    assert validation_schema["properties"]["packageDependencyClosure"][
        "maxItems"
    ] == 4096
    assert validation_schema["unevaluatedProperties"] is True
    assert module_verification_schema["$schema"].endswith("2020-12/schema")
    assert module_verification_schema["unevaluatedProperties"] is True
    verified_contract = module_verification_schema["oneOf"][0]
    assert verified_contract["properties"]["modules"]["minItems"] == 5
    assert verified_contract["properties"]["modules"]["maxItems"] == 5
    assert module_verification_schema["$defs"]["verifiedRecord"][
        "additionalProperties"
    ] is False
    assert userspace_verification_schema["$schema"].endswith("2020-12/schema")
    assert userspace_verification_schema["unevaluatedProperties"] is True
    assert userspace_verification_schema["$defs"]["verifiedPackage"][
        "additionalProperties"
    ] is False
    assert initramfs_verification_schema["$schema"].endswith("2020-12/schema")
    assert initramfs_verification_schema["unevaluatedProperties"] is True
    assert initramfs_verification_schema["properties"]["requiredModules"]["const"] == [
        "nvidia.ko", "nvidia-modeset.ko", "nvidia-uvm.ko", "nvidia-drm.ko",
    ]
    assert initramfs_verification_schema["properties"]["rootfsOnlyModules"][
        "const"
    ] == ["nvidia-peermem.ko"]
    assert payload_receipt_schema["$schema"].endswith("2020-12/schema")
    assert payload_receipt_schema["unevaluatedProperties"] is True
    assert payload_receipt_schema["properties"]["records"]["minItems"] == 6
    assert payload_receipt_schema["properties"]["records"]["maxItems"] == 6
    assert payload_receipt_schema["$defs"]["record"]["additionalProperties"] is False
    assert payload_receipt_schema["$defs"]["target"]["additionalProperties"] is False
    assert workspace_schema["$schema"].endswith("2020-12/schema")
    assert workspace_schema["unevaluatedProperties"] is True
    assert workspace_schema["properties"]["requiredInodes"]["maximum"] == 65536
    assert len(workspace_schema["oneOf"]) == 4
    assert result_schema["properties"]["validation"]["$ref"] == (
        "installer-validation-v1.schema.json"
    )
    assert result_schema["allOf"][0]["then"]["properties"]["validation"][
        "$ref"
    ] == "installer-validation-v1.schema.json"
    assert result_schema["allOf"][0]["then"]["properties"][
        "moduleVerification"
    ]["$ref"] == "installer-module-verification-v1.schema.json"
    assert result_schema["properties"]["userspaceVerification"]["$ref"] == (
        "installer-userspace-verification-v1.schema.json"
    )
    assert result_schema["allOf"][0]["then"]["properties"][
        "userspaceVerification"
    ]["$ref"] == "installer-userspace-verification-v1.schema.json"
    assert result_schema["properties"]["initramfsVerification"]["$ref"] == (
        "installer-initramfs-verification-v1.schema.json"
    )
    assert result_schema["allOf"][0]["then"]["properties"][
        "initramfsVerification"
    ]["$ref"] == "installer-initramfs-verification-v1.schema.json"
    assert result_schema["properties"]["payloadReceipt"]["$ref"] == (
        "installer-payload-receipt-v1.schema.json"
    )
    assert result_schema["allOf"][0]["then"]["properties"][
        "payloadReceipt"
    ]["$ref"] == "installer-payload-receipt-v1.schema.json"
    assert result_schema["properties"]["initramfsWorkspace"]["$ref"] == (
        "installer-initramfs-workspace-v1.schema.json"
    )
    assert result_schema["allOf"][0]["then"]["properties"][
        "initramfsWorkspace"
    ]["$ref"] == "installer-initramfs-workspace-v1.schema.json"

    validate_resolver_compatibility_fixtures(
        ROOT / "contracts/fixtures/resolver-compatibility-v2.json"
    )
    validate_installer_result_compatibility_fixtures(
        ROOT / "lib/generate_installer_result_fixtures.py"
    )
    validate_installer_progress_compatibility_fixtures(
        ROOT / "lib/generate_installer_progress_fixtures.py"
    )
    validate_installer_validation_compatibility_fixtures(
        ROOT / "lib/generate_installer_validation_fixtures.py"
    )
    validate_installer_module_verification_compatibility_fixtures(
        ROOT / "lib/generate_installer_module_verification_fixtures.py"
    )
    validate_installer_userspace_verification_compatibility_fixtures(
        ROOT / "lib/generate_installer_userspace_verification_fixtures.py"
    )
    validate_installer_initramfs_verification_compatibility_fixtures(
        ROOT / "lib/generate_installer_initramfs_verification_fixtures.py"
    )
    validate_installer_payload_receipt_compatibility_fixtures(
        ROOT / "lib/generate_installer_payload_receipt_fixtures.py"
    )
    validate_installer_initramfs_workspace_compatibility_fixtures(
        ROOT / "lib/generate_installer_initramfs_workspace_fixtures.py"
    )

    tag = "steamos-3.8.14-nvidia-575.64.05-k6.16.12-valve24.4-x86"
    archive = f"nvidia-open-{tag}-x86_64.tar.gz"
    releases = [{
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [{"name": name} for name in (
            archive,
            archive + ".sha256",
            f"nvidia-open-{tag}-x86_64.provenance.json",
        )],
    }]
    compatible = resolve_target(
        "3.8.14", "6.16.12-valve24.4-x86", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(compatible)
    assert compatible["status"] == "compatible"
    unavailable = resolve_target(
        "3.8.15", "different-kernel", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(unavailable)
    assert unavailable["status"] == "no_compatible_artifact"
    assert unavailable["reason"] == "no_reviewed_exact_target_build_plan"
    assert "nextAction" not in unavailable
    reviewed_build = resolve_target(
        "3.8.14",
        "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
        "x86_64", [], "CorniiDog/OPEMOS",
    )
    validate_resolver_fixture(reviewed_build)
    assert reviewed_build["nextAction"]["kind"] == "build_exact_target"
    assert reviewed_build["nextAction"]["buildPlan"]["source"]["commit"] == (
        "40bd1b5d6d39ae4e4180b7a665df144b08854d14"
    )
    with tempfile.TemporaryDirectory(prefix="opemos-build-policy-") as temporary:
        malformed_policy = Path(temporary) / "policy.json"
        for payload in (
            '{"schemaVersion":1,"plans":{}}',
            '{"schemaVersion":1,"schemaVersion":1,"plans":[]}',
            '{"schemaVersion":NaN,"plans":[]}',
        ):
            malformed_policy.write_text(payload, encoding="utf-8")
            with mock.patch.object(resolver_module, "BUILD_PLAN_POLICY", malformed_policy):
                invalid_policy = resolve_target(
                    "3.8.14",
                    "6.16.12-valve24.4-1-neptune-616-gfe145653a794",
                    "x86_64", [], "CorniiDog/OPEMOS",
                )
            assert invalid_policy["status"] == "resolver_error"
            assert invalid_policy["reason"] == "build_plan_policy_invalid"
            assert "nextAction" not in invalid_policy
    incomplete = copy.deepcopy(releases)
    incomplete[0]["assets"] = []
    incomplete_result = resolve_target(
        "3.8.14", "6.16.12-valve24.4-x86", "x86_64", incomplete,
        "CorniiDog/OPEMOS",
    )
    assert incomplete_result["reason"] == "release_assets_missing"
    assert "nextAction" not in incomplete_result
    duplicate_asset = copy.deepcopy(releases)
    duplicate_asset[0]["assets"].append(copy.deepcopy(duplicate_asset[0]["assets"][0]))
    duplicate_result = resolve_target(
        "3.8.14", "6.16.12-valve24.4-x86", "x86_64", duplicate_asset,
        "CorniiDog/OPEMOS",
    )
    assert duplicate_result["reason"] == "release_assets_ambiguous"
    assert "nextAction" not in duplicate_result
    invalid = resolve_target(
        "3.8", "kernel", "x86_64", releases, "CorniiDog/OPEMOS"
    )
    validate_resolver_fixture(invalid)
    assert invalid["status"] == "invalid_target"

    with tempfile.TemporaryDirectory(prefix="opemos-consumer-contract-") as temporary:
        repository = Path(temporary) / "repository"
        repository.mkdir()
        command("git", "init", "-q", cwd=repository)
        command("git", "config", "user.name", "OPEMOS tests", cwd=repository)
        command("git", "config", "user.email", "tests@example.invalid", cwd=repository)
        first = repository / "bin/tool"
        second = repository / "policy/data.json"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        second.write_text("{}\n", encoding="utf-8")
        first.chmod(0o755)
        command("git", "add", ".", cwd=repository)
        command("git", "commit", "-q", "-m", "fixture", cwd=repository)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip()
        inventory = (
            ("bin/tool", "entrypoint", "0755"),
            ("policy/data.json", "policy", "0644"),
        )
        document = build_manifest(repository, commit, inventory)
        assert document == build_manifest(repository, commit, inventory)
        assert [record["path"] for record in document["files"]] == [
            "bin/tool", "policy/data.json"
        ]
        assert document["bundleId"] == hashlib.sha256(canonical({
            key: document[key] for key in (
                "schemaVersion", "kind", "repository", "supportCommit", "files"
            )
        })).hexdigest()
        validate_manifest(document, commit, inventory)

        # A mutable checkout cannot influence a manifest generated from the
        # committed blobs.
        first.write_text("changed after commit\n", encoding="utf-8")
        assert build_manifest(repository, commit, inventory) == document

        wrong_hash = copy.deepcopy(document)
        wrong_hash["bundleId"] = "0" * 64
        expect_failure(validate_manifest, wrong_hash, commit, inventory)
        duplicate = copy.deepcopy(document)
        duplicate["files"].append(copy.deepcopy(duplicate["files"][0]))
        expect_failure(validate_manifest, duplicate, commit, inventory)
        unsorted = copy.deepcopy(document)
        unsorted["files"].reverse()
        expect_failure(validate_manifest, unsorted, commit, inventory)
        expect_failure(validate_inventory, (("../escape", "policy", "0644"),))
        expect_failure(validate_inventory, (
            ("same", "policy", "0644"), ("same", "policy", "0644")
        ))
        expect_failure(build_manifest, repository, commit, (
            ("bin/tool", "entrypoint", "0644"),
        ))

        output = Path(temporary) / "manifest.json"
        payload = canonical(document) + b"\n"
        write_create_only(output, payload)
        assert output.read_bytes() == payload
        assert os.stat(output).st_mode & 0o777 == 0o644
        expect_failure(write_create_only, output, payload)


def validate_installer_initramfs_verification_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "initramfsVerificationSchemaVersion",
        "targetKernel", "unfrozenFields", "failureContract", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-installer-initramfs-verification-compatibility-fixtures"
    )
    assert matrix["initramfsVerificationSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == []
    assert matrix["failureContract"] == "outer-installer-result-only"
    assert matrix["limits"] == {
        "maxDocumentBytes": MAX_INITRAMFS_VERIFICATION_BYTES
    }
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-initramfs-fixtures-") as temporary:
        root = Path(temporary)
        candidate_path = root / "initramfs-verification.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
                {"name", "expected", "documentRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {"recordAccepted", "successProofAccepted"}
            assert all(isinstance(value, bool) for value in expected.values())
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            elif "rawDocument" in fixture:
                payload = fixture["rawDocument"]
                assert isinstance(payload, str) and payload
            else:
                recipe = fixture["documentRecipe"]
                assert recipe == {
                    "kind": "top-level-padding",
                    "baseCase": "valid-normalized-success",
                    "paddingBytes": MAX_INITRAMFS_VERIFICATION_BYTES,
                }
                candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
                candidate["padding"] = "x" * recipe["paddingBytes"]
                payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                assert (MAX_INITRAMFS_VERIFICATION_BYTES < len(payload.encode())
                        <= MAX_INITRAMFS_VERIFICATION_BYTES + 16384)
            candidate_path.write_text(payload, encoding="utf-8")
            try:
                parsed = load_initramfs_verification(candidate_path)
            except SystemExit:
                record_accepted = False
                success_accepted = False
            else:
                record_accepted = True
                try:
                    validate_initramfs_verification_binding(
                        parsed, matrix["targetKernel"]
                    )
                except SystemExit:
                    success_accepted = False
                else:
                    success_accepted = True
            assert record_accepted is expected["recordAccepted"], name
            assert success_accepted is expected["successProofAccepted"], name

        linked = root / "linked-initramfs-verification.json"
        linked.symlink_to(candidate_path)
        try:
            load_initramfs_verification(linked)
        except SystemExit:
            pass
        else:
            raise AssertionError("linked initramfs-verification fixture was accepted")

    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-normalized-success", "safe-additive-top-level",
        "kernel-binding-mismatch", "alternate-valid-image-hashes",
        "malformed-kernel", "unknown-kernel", "missing-required-module",
        "required-module-order",
        "extra-required-module", "missing-rootfs-only-module",
        "peermem-in-initramfs", "missing-tool", "extra-tool",
        "wrong-tool-path", "zero-tool-size", "excessive-tool-size",
        "malformed-tool-hash", "wrong-config-path", "zero-config-size",
        "excessive-config-size", "malformed-config-hash", "missing-images",
        "duplicate-image-identity", "excessive-image-set",
        "unsafe-image-filename", "empty-image-filename", "zero-image-size",
        "excessive-image-size",
        "zero-listing-entries", "excessive-listing-entries",
        "malformed-image-hash", "malformed-listing-hash",
        "missing-image-module", "extra-image-module", "duplicate-module-path",
        "module-path-traversal", "absolute-module-path",
        "wrong-module-basename", "wrong-kernel-module-path",
        "unsupported-module-compression", "wrong-listing-config-path",
        "unknown-image-field", "malformed-json", "duplicate-json-key",
        "non-finite-json", "oversized-document",
    }


def validate_installer_payload_receipt_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "payloadReceiptSchemaVersion", "target",
        "unfrozenFields", "failureContract", "bindingScope", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == "opemos-installer-payload-receipt-compatibility-fixtures"
    assert matrix["payloadReceiptSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == []
    assert matrix["failureContract"] == "outer-installer-result-only"
    assert matrix["bindingScope"] == "target-and-self-identity"
    assert matrix["limits"] == {"maxDocumentBytes": MAX_PAYLOAD_RECEIPT_BYTES}
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-receipt-fixtures-") as temporary:
        root = Path(temporary)
        candidate_path = root / "payload-receipt.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
                {"name", "expected", "documentRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {"recordAccepted", "successProofAccepted"}
            assert all(isinstance(value, bool) for value in expected.values())
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            elif "rawDocument" in fixture:
                payload = fixture["rawDocument"]
                assert isinstance(payload, str) and payload
            else:
                recipe = fixture["documentRecipe"]
                assert recipe == {
                    "kind": "top-level-padding",
                    "baseCase": "valid-normalized-success",
                    "paddingBytes": MAX_PAYLOAD_RECEIPT_BYTES,
                }
                candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
                candidate["padding"] = "x" * recipe["paddingBytes"]
                payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                assert (MAX_PAYLOAD_RECEIPT_BYTES < len(payload.encode())
                        <= MAX_PAYLOAD_RECEIPT_BYTES + 16384)
            candidate_path.write_text(payload, encoding="utf-8")
            try:
                parsed = load_payload_receipt(candidate_path)
            except SystemExit:
                record_accepted = False
                success_accepted = False
            else:
                record_accepted = True
                try:
                    validate_payload_receipt_binding(parsed, matrix["target"])
                except SystemExit:
                    success_accepted = False
                else:
                    success_accepted = True
            assert record_accepted is expected["recordAccepted"], name
            assert success_accepted is expected["successProofAccepted"], name

        linked = root / "linked-payload-receipt.json"
        linked.symlink_to(candidate_path)
        try:
            load_payload_receipt(linked)
        except SystemExit:
            pass
        else:
            raise AssertionError("linked payload-receipt fixture was accepted")

    assert len(names) == len(set(names))
    static_names = {
        "valid-normalized-success", "safe-additive-top-level",
        "target-binding-mismatch", "alternate-record-hash",
        "receipt-id-mismatch", "missing-record", "extra-record",
        "duplicate-record", "records-out-of-order", "unknown-role",
        "unsafe-role", "wrong-role-filename", "unsafe-filename",
        "empty-filename", "zero-record-size", "malformed-record-hash",
        "missing-record-field", "unknown-record-field", "missing-target-field",
        "unknown-target-field", "unknown-target-kernel",
        "wrong-target-architecture", "malformed-target-version",
        "wrong-rootfs-relative-path", "path-traversal", "wrong-status",
        "wrong-reason", "malformed-json", "duplicate-json-key",
        "non-finite-json", "oversized-document",
    }
    role_slugs = {
        "build-info", "provenance", "validation", "module-verification",
        "userspace-verification", "initramfs-verification",
    }
    bound_names = {
        f"{prefix}-{slug}-size"
        for prefix in ("maximum", "excessive") for slug in role_slugs
    }
    assert set(names) == static_names | bound_names


def validate_installer_initramfs_workspace_compatibility_fixtures(generator):
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(generator)], cwd="/", check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.stderr == b""
        assert 1 <= len(completed.stdout) <= 512 * 1024
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] and outputs[0].endswith(b"\n")
    matrix = json.loads(
        outputs[0], object_pairs_hook=resolver_module.unique_object,
        parse_constant=resolver_module.reject_json_constant,
    )
    assert set(matrix) == {
        "schemaVersion", "kind", "initramfsWorkspaceSchemaVersion",
        "validationStorage", "unfrozenFields", "limits", "cases",
    }
    assert matrix["schemaVersion"] == 1
    assert matrix["kind"] == (
        "opemos-installer-initramfs-workspace-compatibility-fixtures"
    )
    assert matrix["initramfsWorkspaceSchemaVersion"] == 1
    assert matrix["unfrozenFields"] == ["message"]
    assert matrix["limits"] == {
        "maxDocumentBytes": MAX_WORKSPACE_VERIFICATION_BYTES,
        "maxCapacity": 2**63 - 1,
        "maxRequiredInodes": 65536,
    }
    validation = {"storage": matrix["validationStorage"]}
    cases = matrix["cases"]
    assert isinstance(cases, list) and 1 <= len(cases) <= 64
    base_cases = {
        fixture["name"]: fixture["document"]
        for fixture in cases if "document" in fixture
    }
    names = []
    with tempfile.TemporaryDirectory(prefix="opemos-workspace-fixtures-") as temporary:
        root = Path(temporary)
        candidate_path = root / "workspace.json"
        for fixture in cases:
            assert isinstance(fixture, dict)
            assert set(fixture) in (
                {"name", "expected", "document"},
                {"name", "expected", "rawDocument"},
                {"name", "expected", "documentRecipe"},
            )
            name = fixture["name"]
            names.append(name)
            assert isinstance(name, str)
            assert re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
            expected = fixture["expected"]
            assert set(expected) == {
                "recordAccepted", "validatedResultAccepted",
                "mutationSuccessAccepted",
            }
            assert all(isinstance(value, bool) for value in expected.values())
            if "document" in fixture:
                payload = json.dumps(
                    fixture["document"], sort_keys=True, separators=(",", ":")
                ) + "\n"
            elif "rawDocument" in fixture:
                payload = fixture["rawDocument"]
                assert isinstance(payload, str) and payload
            else:
                recipe = fixture["documentRecipe"]
                assert recipe == {
                    "kind": "top-level-padding",
                    "baseCase": "valid-target-finite",
                    "paddingBytes": MAX_WORKSPACE_VERIFICATION_BYTES,
                }
                candidate = copy.deepcopy(base_cases[recipe["baseCase"]])
                candidate["padding"] = "x" * recipe["paddingBytes"]
                payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
                assert (MAX_WORKSPACE_VERIFICATION_BYTES < len(payload.encode())
                        <= MAX_WORKSPACE_VERIFICATION_BYTES + 16384)
            candidate_path.write_text(payload, encoding="utf-8")
            try:
                parsed = load_initramfs_workspace(candidate_path)
            except SystemExit:
                record_accepted = False
                validated_accepted = False
                mutation_accepted = False
            else:
                record_accepted = True
                try:
                    validate_initramfs_workspace_binding(
                        parsed, "validated", validation
                    )
                except SystemExit:
                    validated_accepted = False
                else:
                    validated_accepted = True
                try:
                    validate_initramfs_workspace_binding(
                        parsed, "success", validation
                    )
                except SystemExit:
                    mutation_accepted = False
                else:
                    mutation_accepted = True
            assert record_accepted is expected["recordAccepted"], name
            assert validated_accepted is expected["validatedResultAccepted"], name
            assert mutation_accepted is expected["mutationSuccessAccepted"], name

        linked = root / "linked-workspace.json"
        linked.symlink_to(candidate_path)
        try:
            load_initramfs_workspace(linked)
        except SystemExit:
            pass
        else:
            raise AssertionError("linked workspace fixture was accepted")

    assert len(names) == len(set(names))
    assert set(names) == {
        "valid-target-finite", "valid-target-bind-inodes",
        "valid-preparation-finite", "valid-preparation-bind-inodes",
        "valid-mounted-finite", "valid-mounted-dynamic",
        "valid-backing-finite", "safe-additive-top-level",
        "valid-failure-insufficient-bytes", "valid-failure-insufficient-inodes",
        "valid-failure-dynamic-probe", "storage-reserve-binding-mismatch",
        "mutation-inode-binding-mismatch", "validation-byte-binding-mismatch",
        "validation-inode-binding-mismatch", "maximum-capacity-and-inodes",
        "negative-required-bytes", "excessive-required-bytes",
        "excessive-required-inodes", "boolean-required-inodes",
        "negative-available-bytes", "nested-available-bytes",
        "finite-missing-inodes", "finite-null-inodes",
        "finite-insufficient-inodes-verified", "insufficient-bytes-verified",
        "dynamic-with-reported-inodes", "dynamic-target-state",
        "bind-mode-mounted-state", "probe-failure-verified", "missing-mode",
        "wrong-mode", "verified-message", "preparation-nonnull-mode",
        "preparation-message", "preparation-insufficient-bytes",
        "failure-missing-message", "failure-available-condition",
        "failure-contradictory-bytes", "failure-contradictory-inodes",
        "target-reason-contradiction", "mounted-phase-contradiction",
        "unknown-phase", "missing-required-field", "malformed-json",
        "duplicate-json-key", "non-finite-json", "oversized-document",
    }


if __name__ == "__main__":
    main()
