#!/usr/bin/env python3
"""Edge-case tests for immutable release planning and reconciliation."""
import json, subprocess, tempfile
from pathlib import Path
import jsonschema
import publisher
ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "lib" / "release_operation.py"
SCHEMA = json.loads((ROOT / "contracts/schemas/release-operation-v1.schema.json").read_text())
def run(plan, root, observed=None, attempt=1, cancelled=False):
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root / "plan.json"; plan_path.write_text(json.dumps(plan))
    command = [str(TOOL), "--plan", str(plan_path), "--attempt", str(attempt)]
    if cancelled: command.append("--cancelled")
    if observed is not None:
        observed_path = root / "observed.json"; observed_path.write_text(json.dumps(observed))
        command += ["--observed", str(observed_path)]
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def inventory(document):
    return {"repository": document["repository"], "tag": document["tag"], "targetCommit": document["targetCommit"],
            "assets": [{k: asset[k] for k in ("name", "sha256", "bytes")} for asset in document["assets"]]}
def main():
    with tempfile.TemporaryDirectory(prefix="release-operation-") as temporary:
        root = Path(temporary); paths = publisher.fixture(root / "assets")
        validated = publisher.command(paths, "--dry-run"); assert validated.returncode == 0, validated.stderr
        plan = json.loads(validated.stdout)
        planned = run(plan, root / "planned"); assert planned.returncode == 0, planned.stderr
        document = json.loads(planned.stdout); jsonschema.validate(document, SCHEMA)
        assert (document["lifecycle"], document["decision"]) == ("planned", "create")
        assert all(asset["state"] == "pending" for asset in document["assets"])
        assert document["progress"] == {"phase": "planning", "completedAssets": 0, "totalAssets": 4, "indeterminate": True}
        complete = json.loads(run(plan, root / "complete", inventory(document), 2).stdout)
        jsonschema.validate(complete, SCHEMA)
        assert complete["operationId"] == document["operationId"] and complete["attempt"] == 2
        assert (complete["lifecycle"], complete["decision"]) == ("succeeded", "already-complete")
        assert complete["progress"] == {"phase": "complete", "completedAssets": 4, "totalAssets": 4, "indeterminate": False}
        missing_inventory = inventory(document); missing_inventory["assets"].pop()
        missing = json.loads(run(plan, root / "missing", missing_inventory).stdout)
        assert missing["decision"] == "retry-missing"
        assert [asset["state"] for asset in missing["assets"]].count("missing") == 1
        assert missing["progress"] == {"phase": "reconciling", "completedAssets": 3, "totalAssets": 4, "indeterminate": False}
        conflicting = inventory(document); conflicting["assets"][0]["sha256"] = "0" * 64
        conflict = json.loads(run(plan, root / "conflict", conflicting).stdout)
        assert (conflict["lifecycle"], conflict["decision"], conflict["assets"][0]["state"]) == ("failed", "conflict", "conflict")
        wrong_commit = inventory(document); wrong_commit["targetCommit"] = "f" * 40
        target_conflict = json.loads(run(plan, root / "wrong-commit", wrong_commit).stdout)
        assert target_conflict["decision"] == "conflict" and all(a["state"] == "conflict" for a in target_conflict["assets"])
        unexpected = inventory(document); unexpected["assets"].append({"name": "foreign", "sha256": "1" * 64, "bytes": 1})
        assert json.loads(run(plan, root / "unexpected", unexpected).stdout)["decision"] == "conflict"
        duplicate = inventory(document); duplicate["assets"].append(duplicate["assets"][0])
        rejected = run(plan, root / "duplicate", duplicate)
        assert rejected.returncode != 0 and "Traceback" not in rejected.stderr
        cancelled = json.loads(run(plan, root / "cancelled", cancelled=True).stdout)
        assert (cancelled["lifecycle"], cancelled["decision"]) == ("cancelled", "cancelled")
        assert cancelled["progress"] == {"phase": "cancelled", "completedAssets": 0, "totalAssets": 4, "indeterminate": True}
        assert run(plan, root / "cancelled-observed", inventory(document), cancelled=True).returncode != 0
        assert run(plan, root / "attempt-zero", attempt=0).returncode != 0
        malformed = dict(plan); malformed["callerTrust"] = True
        assert run(malformed, root / "additive-plan").returncode != 0
if __name__ == "__main__": main()
