#!/usr/bin/env python3
import hashlib,json,subprocess,sys
import jsonschema
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; TOOL=ROOT/"lib/maintainer_release_workflow.py"
sys.path.insert(0,str(ROOT/"lib"))
from resolve_target import exact_target_build_action
TARGET=["--steamos","3.8.14","--kernel","6.16.12-valve24.4-1-neptune-616-gfe145653a794","--nvidia","575.64.05","--architecture","x86_64"]
UPDATED_TARGET=["--steamos","3.8.16","--kernel","6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45","--nvidia","575.64.05","--architecture","x86_64"]
def call(*args): return subprocess.run([str(TOOL),*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def main():
 first=call(*TARGET); second=call(*TARGET); assert first.returncode==0,first.stderr; assert first.stdout==second.stdout
 value=json.loads(first.stdout); schema=json.loads((ROOT/"contracts/schemas/maintainer-release-workflow-v1.schema.json").read_text()); jsonschema.validate(value,schema); assert value["status"]=="available" and value["capability"]["available"] is True
 assert value["publication"]=={"mode":"dry-run-only","requiresSeparateAuthorization":True,"combinedNvidiaSteamOsAsset":False}
 assert value["contracts"]["buildResult"]=={"schemaVersion":1,"writer":"lib/write_build_result.py"}
 assert value["contracts"]["releaseProgressResult"]["schema"]=="contracts/schemas/release-operation-v1.schema.json"
 assert [s["id"] for s in value["steps"]]==["resolve","build","package","bundle","validate","release-dry-run"]
 assert all(not s["mutatesRemote"] and s["requiredInputs"] for s in value["steps"])
 import copy
 mutations=[]
 for field,replacement in (("id","substituted"),("entrypoint","arbitrary/tool"),("result","arbitrary-result"),("requiredInputs",["wrongInput"])):
  candidate=copy.deepcopy(value); candidate["steps"][2][field]=replacement; mutations.append((field,candidate))
 duplicate=copy.deepcopy(value); duplicate["steps"][1]=copy.deepcopy(duplicate["steps"][0]); mutations.append(("duplicate",duplicate))
 reordered=copy.deepcopy(value); reordered["steps"][0],reordered["steps"][1]=reordered["steps"][1],reordered["steps"][0]; mutations.append(("reordered",reordered))
 for name,candidate in mutations:
  try: jsonschema.validate(candidate,schema)
  except jsonschema.ValidationError: pass
  else: raise AssertionError(f"schema accepted {name} workflow mutation")
 assert value["steps"][-1]["requiredInputs"][-1]=="dryRun"
 assert value["capability"]["policy"]["sha256"]==hashlib.sha256((ROOT/"policies/exact-target-builds-v1.json").read_bytes()).hexdigest()
 updated=call(*UPDATED_TARGET); assert updated.returncode==0,updated.stderr
 updated_value=json.loads(updated.stdout); jsonschema.validate(updated_value,schema)
 assert updated_value["status"]=="available" and updated_value["capability"]["available"] is True
 assert updated_value["source"]=={"repository":"CorniiDog/open-gpu-kernel-modules-steamos","ref":"refs/heads/nvidia/575.64.05","commit":"40bd1b5d6d39ae4e4180b7a665df144b08854d14"}
 build=exact_target_build_action("3.8.16","6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45","x86_64")
 assert build["buildPlan"]["target"]==updated_value["target"]
 assert build["buildPlan"]["source"]==updated_value["source"]
 assert build["buildPlan"]["baseline"]["releaseTag"]=="steamos-3.8.16-nvidia-575.64.05-k6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45"
 assert build["buildPlan"]["policy"]["sha256"]==updated_value["capability"]["policy"]["sha256"]
 missing=call("--steamos","3.8.15",*TARGET[2:]); assert missing.returncode==0,missing.stderr
 absent=json.loads(missing.stdout); jsonschema.validate(absent,schema); assert absent["status"]=="unavailable" and absent["steps"]==[] and "source" not in absent
 for bad in ([],["--steamos","3.8","--kernel","k","--nvidia","575.64.05","--architecture","x86_64"],TARGET[:-1]+["aarch64"]):
  result=call(*bad); assert result.returncode!=0 and not result.stdout
if __name__=="__main__": main()
