#!/usr/bin/env python3
"""Closed non-production end-to-end release-session fixture."""
import json, os, subprocess, tempfile
from pathlib import Path
import publisher
ROOT=Path(__file__).resolve().parent.parent
TOOL=ROOT/"lib/release_operation_session.py"

def call(root,command,*extra):
    return subprocess.run([str(TOOL),command,"--state",str(root/"state.json"),*map(str,extra)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

def inventory(document):
    return {"repository":document["repository"],"tag":document["tag"],"targetCommit":document["targetCommit"],
            "assets":[{k:a[k] for k in ("name","sha256","bytes")} for a in document["assets"]]}

def main():
  with tempfile.TemporaryDirectory(prefix="release-session-") as temporary:
    root=Path(temporary);paths=publisher.fixture(root/"assets")
    validated=publisher.command(paths,"--dry-run");assert validated.returncode==0,validated.stderr
    plan=root/"plan.json";plan.write_text(validated.stdout)
    started=call(root,"execute","--plan",plan);assert started.returncode==0,started.stderr
    start=json.loads(started.stdout);assert start["decision"]=="create"
    assert (root/"state.json").stat().st_mode & 0o777 == 0o600
    assert json.loads(call(root,"status").stdout)==start
    assert json.loads(call(root,"execute","--plan",plan,"--attempt","2").stdout)==start

    foreign=dict(json.loads(validated.stdout));foreign["tag"]+="x"
    foreign_path=root/"foreign.json";foreign_path.write_text(json.dumps(foreign))
    mismatch=call(root,"execute","--plan",foreign_path)
    assert mismatch.returncode!=0 and "different release operation" in mismatch.stderr

    observed=root/"observed.json";remote=inventory(start);remote["assets"].pop()
    observed.write_text(json.dumps(remote))
    retried=call(root,"reconcile","--plan",plan,"--observed",observed,"--attempt","2")
    assert retried.returncode==0,retried.stderr
    retry=json.loads(retried.stdout);assert retry["decision"]=="retry-missing" and retry["attempt"]==2
    assert retry["progress"]["completedAssets"]==3

    cancelled=json.loads(call(root,"cancel").stdout)
    assert cancelled["lifecycle"]=="cancelled"
    assert json.loads(call(root,"cancel").stdout)==cancelled
    rejected=call(root,"reconcile","--plan",plan,"--observed",observed,"--attempt","3")
    assert rejected.returncode!=0 and "cannot be reconciled" in rejected.stderr

    status_extra=call(root,"status","--plan",plan)
    assert status_extra.returncode!=0 and "Status accepts only" in status_extra.stderr

    hostile=root/"hostile";hostile.mkdir()
    (hostile/"state.json").symlink_to(root/"state.json")
    refused=call(hostile,"status")
    assert refused.returncode!=0 and "bounded regular file" in refused.stderr

    complete_root=root/"complete";complete_root.mkdir()
    execute=call(complete_root,"execute","--plan",plan);document=json.loads(execute.stdout)
    complete_observed=complete_root/"observed.json";complete_observed.write_text(json.dumps(inventory(document)))
    complete=json.loads(call(complete_root,"reconcile","--plan",plan,"--observed",complete_observed).stdout)
    assert complete["lifecycle"]=="succeeded" and complete["progress"]["completedAssets"]==4
    assert json.loads(call(complete_root,"cancel").stdout)==complete
if __name__=="__main__":main()
