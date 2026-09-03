#!/usr/bin/env python3
"""Emit canonical bounded initramfs-verification compatibility fixtures."""

import copy
import json
import sys

from generate_installer_result_fixtures import KERNEL, initramfs_verification


MAX_OUTPUT_BYTES = 512 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024
OTHER_KERNEL = "6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45"


def case(name, document, record=True, proof=None):
    return {
        "name": name,
        "expected": {
            "recordAccepted": record,
            "successProofAccepted": record if proof is None else proof,
        },
        "document": document,
    }


def changed(base, update):
    document = copy.deepcopy(base)
    update(document)
    return document


def main():
    valid = initramfs_verification()
    cases = [case("valid-normalized-success", valid)]
    cases.append(case(
        "safe-additive-top-level",
        changed(valid, lambda value: value.update({
            "producer": {"name": "verify_initramfs.py", "schemaVersion": 1}
        })),
    ))

    def bind_other_kernel(value):
        value["kernelVersion"] = OTHER_KERNEL
        for image in value["images"]:
            image["modules"] = {
                name: path.replace(KERNEL, OTHER_KERNEL)
                for name, path in image["modules"].items()
            }

    cases.append(case(
        "kernel-binding-mismatch", changed(valid, bind_other_kernel), True, False
    ))
    cases.append(case(
        "alternate-valid-image-hashes",
        changed(valid, lambda value: value["images"][0].update({
            "sha256": "8" * 64, "listingSha256": "9" * 64,
        })),
    ))
    cases.extend([
        case("malformed-kernel", changed(valid, lambda v: v.update(
            {"kernelVersion": "../kernel"})), False),
        case("unknown-kernel", changed(valid, lambda v: v.update(
            {"kernelVersion": "unknown"})), False),
        case("missing-required-module", changed(valid, lambda v:
            v["requiredModules"].pop()), False),
        case("required-module-order", changed(valid, lambda v:
            v["requiredModules"].reverse()), False),
        case("extra-required-module", changed(valid, lambda v:
            v["requiredModules"].append("nvidia-peermem.ko")), False),
        case("missing-rootfs-only-module", changed(valid, lambda v:
            v.update({"rootfsOnlyModules": []})), False),
        case("peermem-in-initramfs", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia-peermem.ko": f"usr/lib/modules/{KERNEL}/nvidia-peermem.ko.zst"
            })), False),
        case("missing-tool", changed(valid, lambda v:
            v["tools"].pop("lsinitcpio")), False),
        case("extra-tool", changed(valid, lambda v:
            v["tools"].update({"dracut": copy.deepcopy(v["tools"]["mkinitcpio"])})), False),
        case("wrong-tool-path", changed(valid, lambda v:
            v["tools"]["mkinitcpio"].update({"path": "/usr/bin/lsinitcpio"})), False),
        case("zero-tool-size", changed(valid, lambda v:
            v["tools"]["mkinitcpio"].update({"sizeBytes": 0})), False),
        case("excessive-tool-size", changed(valid, lambda v:
            v["tools"]["mkinitcpio"].update({"sizeBytes": 8 * 1024 * 1024 + 1})), False),
        case("malformed-tool-hash", changed(valid, lambda v:
            v["tools"]["mkinitcpio"].update({"sha256": "x" * 64})), False),
        case("wrong-config-path", changed(valid, lambda v:
            v["config"].update({"path": "/tmp/config"})), False),
        case("zero-config-size", changed(valid, lambda v:
            v["config"].update({"sizeBytes": 0})), False),
        case("excessive-config-size", changed(valid, lambda v:
            v["config"].update({"sizeBytes": 1024 * 1024 + 1})), False),
        case("malformed-config-hash", changed(valid, lambda v:
            v["config"].update({"sha256": "short"})), False),
        case("missing-images", changed(valid, lambda v:
            v.update({"images": []})), False),
        case("duplicate-image-identity", changed(valid, lambda v:
            v["images"].append(copy.deepcopy(v["images"][0]))), False),
        case("unsafe-image-filename", changed(valid, lambda v:
            v["images"][0].update({"filename": "../initramfs.img"})), False),
        case("empty-image-filename", changed(valid, lambda v:
            v["images"][0].update({"filename": ""})), False),
        case("zero-image-size", changed(valid, lambda v:
            v["images"][0].update({"sizeBytes": 0})), False),
        case("excessive-image-size", changed(valid, lambda v:
            v["images"][0].update({"sizeBytes": 2 * 1024**3 + 1})), False),
        case("zero-listing-entries", changed(valid, lambda v:
            v["images"][0].update({"entries": 0})), False),
        case("excessive-listing-entries", changed(valid, lambda v:
            v["images"][0].update({"entries": 200001})), False),
        case("malformed-image-hash", changed(valid, lambda v:
            v["images"][0].update({"sha256": "x" * 64})), False),
        case("malformed-listing-hash", changed(valid, lambda v:
            v["images"][0].update({"listingSha256": "x" * 64})), False),
        case("missing-image-module", changed(valid, lambda v:
            v["images"][0]["modules"].pop("nvidia-drm.ko")), False),
        case("extra-image-module", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia-peermem.ko": f"usr/lib/modules/{KERNEL}/nvidia-peermem.ko.zst"
            })), False),
        case("duplicate-module-path", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia-drm.ko": v["images"][0]["modules"]["nvidia.ko"]
            })), False),
        case("module-path-traversal", changed(valid, lambda v:
            v["images"][0]["modules"].update({"nvidia.ko": "../nvidia.ko"})), False),
        case("absolute-module-path", changed(valid, lambda v:
            v["images"][0]["modules"].update({"nvidia.ko": "/usr/lib/modules/nvidia.ko"})), False),
        case("wrong-module-basename", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia.ko": f"usr/lib/modules/{KERNEL}/nouveau.ko"
            })), False),
        case("wrong-kernel-module-path", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia.ko": f"usr/lib/modules/{OTHER_KERNEL}/nvidia.ko.zst"
            })), False),
        case("unsupported-module-compression", changed(valid, lambda v:
            v["images"][0]["modules"].update({
                "nvidia.ko": f"usr/lib/modules/{KERNEL}/nvidia.ko.bz2"
            })), False),
        case("wrong-listing-config-path", changed(valid, lambda v:
            v["images"][0].update({"configPath": "etc/modprobe.d/other.conf"})), False),
        case("unknown-image-field", changed(valid, lambda v:
            v["images"][0].update({"unexpected": True})), False),
    ])

    excessive_images = copy.deepcopy(valid)
    excessive_images["images"] = []
    for index in range(33):
        image = copy.deepcopy(valid["images"][0])
        image["filename"] = f"initramfs-{index}.img"
        image["sha256"] = f"{index + 1:064x}"
        image["listingSha256"] = f"{index + 34:064x}"
        excessive_images["images"].append(image)
    cases.append(case("excessive-image-set", excessive_images, False))
    cases.extend([
        {
            "name": "malformed-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": "{",
        },
        {
            "name": "duplicate-json-key",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":1,"schemaVersion":1}',
        },
        {
            "name": "non-finite-json",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "rawDocument": '{"schemaVersion":NaN}',
        },
        {
            "name": "oversized-document",
            "expected": {"recordAccepted": False, "successProofAccepted": False},
            "documentRecipe": {
                "kind": "top-level-padding",
                "baseCase": "valid-normalized-success",
                "paddingBytes": MAX_DOCUMENT_BYTES,
            },
        },
    ])
    matrix = {
        "schemaVersion": 1,
        "kind": "opemos-installer-initramfs-verification-compatibility-fixtures",
        "initramfsVerificationSchemaVersion": 1,
        "targetKernel": KERNEL,
        "unfrozenFields": [],
        "failureContract": "outer-installer-result-only",
        "limits": {"maxDocumentBytes": MAX_DOCUMENT_BYTES},
        "cases": cases,
    }
    payload = (json.dumps(matrix, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit("Generated initramfs-verification fixtures exceed their bound.")
    sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
