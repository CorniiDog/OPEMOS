#!/usr/bin/env python3
import hashlib,json,subprocess
import jsonschema
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; TOOL=ROOT/"lib/maintainer_release_workflow.py"
TARGET=["--steamos","3.8.14","--kernel","6.16.12-valve24.4-1-neptune-616-gfe145653a794","--nvidia","575.64.05","--architecture","x86_64"]
def call(*args): return subprocess.run([str(TOOL),*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def main():
 first=call(*TARGET); second=call(*TARGET); assert first.returncode==0,first.stderr; assert first.stdout==second.stdout
 value=json.loads(first.stdout); schema=json.loads((ROOT/"contracts/schemas/maintainer-release-workflow-v1.schema.json").read_text()); jsonschema.validate(value,schema); assert value["status"]=="available" and value["capability"]["available"] is True
 assert value["publication"]=={"mode":"dry-run-only","requiresSeparateAuthorization":True,"combinedNvidiaSteamOsAsset":False}
 assert value["contracts"]["buildResult"]=={"schemaVersion":1,"writer":"lib/write_build_result.py"}
 assert value["contracts"]["releaseProgressResult"]["schema"]=="contracts/schemas/release-operation-v1.schema.json"
 assert [s["id"] for s in value["steps"]]==["resolve","build","package","bundle","validate","release-dry-run"]
 assert all(not s["mutatesRemote"] and s["requiredInputs"] for s in value["steps"])
 assert value["steps"][-1]["requiredInputs"][-1]=="dryRun"
 assert value["capability"]["policy"]["sha256"]==hashlib.sha256((ROOT/"policies/exact-target-builds-v1.json").read_bytes()).hexdigest()
 missing=call("--steamos","3.8.15",*TARGET[2:]); assert missing.returncode==0,missing.stderr
 absent=json.loads(missing.stdout); jsonschema.validate(absent,schema); assert absent["status"]=="unavailable" and absent["steps"]==[] and "source" not in absent
 for bad in ([],["--steamos","3.8","--kernel","k","--nvidia","575.64.05","--architecture","x86_64"],TARGET[:-1]+["aarch64"]):
  result=call(*bad); assert result.returncode!=0 and not result.stdout
if __name__=="__main__": main()
