#!/usr/bin/env python3
"""Build the bounded schema-1 Open OPEMOS view model from recovery status."""

import argparse
import json
import stat
from pathlib import Path

PHASE_LABELS = {
    "offline_waiting": "Waiting for a trusted network",
    "retry_scheduled": "Repair retry scheduled",
    "downloading": "Downloading the exact release",
    "verifying": "Verifying NVIDIA recovery",
    "rebuilding": "Building for the exact kernel",
    "installing": "Installing the exact NVIDIA stack",
    "restored": "NVIDIA graphics restored",
    "cancelled": "Automatic repair paused",
    "failed": "Recovery needs attention",
}


def load(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
        raise ValueError("status document is unsafe or excessive")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or value.get("status") not in {
        "healthy", "recovery-required", "fallback-active", "unknown"
    }:
        raise ValueError("status document is unsupported")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, type=Path)
    args = parser.parse_args()
    status = load(args.status)
    transaction = status.get("transaction", {"phase": "restored", "active": False})
    phase = transaction.get("phase", "failed")
    if phase not in PHASE_LABELS:
        phase = "failed"
    healthy = status.get("moduleVerification", {}).get("status") == "verified"
    fallback = bool(status.get("fallback", {}).get("active"))
    actions = [
        {"id": "refresh", "label": "Refresh status", "privileged": False,
         "command": ["status", "--json"], "enabled": True},
        {"id": "repair", "label": "Repair NVIDIA", "privileged": True,
         "command": ["repair-online", "--json"], "enabled": not healthy},
        {"id": "cancel", "label": "Pause automatic repair", "privileged": True,
         "command": ["cancel-repair", "--json"],
         "enabled": bool(transaction.get("active"))},
        {"id": "restore-graphics", "label": "Return to NVIDIA graphics",
         "privileged": True, "command": ["disable-fallback", "--json"],
         "enabled": healthy and fallback},
        {"id": "igpu", "label": "Use validated integrated graphics",
         "privileged": True,
         "command": ["enable-fallback", "--profile", "igpu-desktop", "--json"],
         "enabled": not healthy},
        {"id": "nouveau", "label": "Use experimental Nouveau",
         "privileged": True,
         "command": ["enable-fallback", "--profile", "nouveau-experimental",
                     "--allow-nouveau", "--json"], "enabled": not healthy,
         "warning": "Experimental; never selected automatically."},
    ]
    document = {
        "schemaVersion": 1, "applicationId": "org.opemos.OpenOPEMOS",
        "title": "Open OPEMOS", "status": status["status"],
        "phase": phase, "phaseLabel": PHASE_LABELS[phase],
        "desktop": {"requiredForGraphicalRecovery": True,
                    "requestAction": "request-desktop-mode"},
        "actions": actions,
        "privilegeBoundary": "recoveryctl-fixed-actions-only",
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > 64 * 1024:
        raise SystemExit("Open OPEMOS view model exceeded its bound")
    print(encoded, end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"open_opemos_contract.py: {error}") from None
