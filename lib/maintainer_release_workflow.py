#!/usr/bin/env python3
"""Emit a network-free exact-target maintainer workflow capability."""
import argparse, hashlib, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
POLICY=ROOT/"policies/exact-target-builds-v1.json"
TARGET_FIELDS=("steamosVersion","kernelVersion","nvidiaVersion","architecture")
def fail(message): raise SystemExit(message)
def unique(pairs):
 value={}
 for key,item in pairs:
  if key in value: raise ValueError("duplicate")
  value[key]=item
 return value
def main():
 p=argparse.ArgumentParser()
 for name in ("steamos","kernel","nvidia","architecture"): p.add_argument("--"+name,required=True)
 a=p.parse_args(); target=dict(zip(TARGET_FIELDS,(a.steamos,a.kernel,a.nvidia,a.architecture)))
 patterns=(r"[0-9]+\.[0-9]+\.[0-9]+",r"[A-Za-z0-9._+~-]{1,255}",r"[0-9]+\.[0-9]+(?:\.[0-9]+)?",r"x86_64")
 if any(re.fullmatch(pattern,value) is None for pattern,value in zip(patterns,target.values())): fail("Requested exact target is malformed or unsupported.")
 try:
  raw=POLICY.read_bytes(); policy=json.loads(raw,object_pairs_hook=unique)
 except (OSError,UnicodeError,json.JSONDecodeError,ValueError): fail("Exact-target build policy is unreadable.")
 if not isinstance(policy,dict) or set(policy)!={"schemaVersion","plans"} or policy["schemaVersion"]!=1 or not isinstance(policy["plans"],list): fail("Exact-target build policy is malformed.")
 matches=[plan for plan in policy["plans"] if isinstance(plan,dict) and plan.get("target")==target]; available=len(matches)==1
 result={"schemaVersion":1,"kind":"opemos-maintainer-release-workflow","status":"available" if available else "unavailable","target":target,
  "capability":{"id":"exact-target-driver-release-v1","available":available,"policy":{"name":POLICY.name,"sha256":hashlib.sha256(raw).hexdigest()}},
  "contracts":{"workflow":"maintainer-release-workflow-v1.schema.json","buildResult":{"schemaVersion":1,"writer":"lib/write_build_result.py"},"releaseProgressResult":{"schemaVersion":1,"schema":"contracts/schemas/release-operation-v1.schema.json","session":"lib/release_operation_session.py"}},
  "publication":{"mode":"dry-run-only","requiresSeparateAuthorization":True,"combinedNvidiaSteamOsAsset":False},"steps":[]}
 if available:
  result["source"]=matches[0]["source"]
  definitions=(
   ("resolve","bootstrap/build_for_target.sh","build-plan-json",["target","source"]),
   ("build","bootstrap/build_for_target.sh","build-result-v1",["target","source","authenticatedHeaders"]),
   ("package","lib/build_driver_product.py","compiled-driver-product-v1",["buildArtifacts","provenance"]),
   ("bundle","lib/driver_binary_bundle.py","driver-binary-bundle-v1",["compiledDriverProduct","releaseIdentity"]),
   ("validate","lib/validate_publish_inputs.py","publication-plan-v1",["canonicalAssetSet","repository"]),
   ("release-dry-run","bootstrap/publish_artifacts.sh","publication-plan-v1",["canonicalAssetSet","dryRun"]))
  result["steps"]=[{"id":i,"entrypoint":e,"result":o,"requiredInputs":inputs,"mutatesRemote":False} for i,e,o,inputs in definitions]
 print(json.dumps(result,sort_keys=True,separators=(",",":")))
if __name__=="__main__": main()
