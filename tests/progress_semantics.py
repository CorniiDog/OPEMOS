#!/usr/bin/env python3
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'lib'))
from progress_semantics import ContractError, PHASES, adapt, adapt_result, strict

def reject(value):
 try: adapt(value)
 except ContractError: return
 raise AssertionError(value)

FORBIDDEN_PRESENTATION_KEYS={"label","layout","widget","focus","window","animation","toolkit","platform","accessibility","rendering","interaction"}
def assert_frontend_neutral(value):
 if isinstance(value,dict):
  assert not (set(value) & FORBIDDEN_PRESENTATION_KEYS), set(value) & FORBIDDEN_PRESENTATION_KEYS
  for child in value.values(): assert_frontend_neutral(child)
 elif isinstance(value,list):
  for child in value: assert_frontend_neutral(child)

def main():
 from generate_progress_semantics_fixtures import matrix as semantics_matrix
 import subprocess
 fixture=ROOT/"contracts/fixtures/progress-semantics-v1.json"
 generated=subprocess.run([sys.executable,str(ROOT/"lib/generate_progress_semantics_fixtures.py")],cwd="/",check=True,stdout=subprocess.PIPE).stdout
 assert generated==fixture.read_bytes()
 for case in semantics_matrix()["cases"]: assert adapt(case["input"])==case["expected"]
 from generate_installer_result_fixtures import matrix
 from validate_install_contract import validate_result
 import tempfile
 with tempfile.TemporaryDirectory() as name:
  for case in matrix()["cases"]:
   if not case["expected"]["accepted"]: continue
   path=Path(name)/"result.json"; path.write_text(json.dumps(case["document"],sort_keys=True,separators=(",",":"))+"\n")
   result=adapt_result(validate_result(path))
   assert set(result)=={"schemaVersion","kind","state","phase","reason","trust","cleanupComplete"}
   assert_frontend_neutral(result)
   expected={"success":"succeeded","validated":"validated","failed":"failed","cancelled":"cancelled"}[case["expected"]["status"]]
   assert result["state"]==expected
   if expected in {"succeeded","validated"}: assert result["cleanupComplete"]
 schema=json.loads((ROOT/"contracts/schemas/progress-semantics-v1.schema.json").read_text())
 assert schema["additionalProperties"] is False
 assert schema["properties"]["kind"]["const"]=="opemos-progress-semantics"
 emitted={"holo_database","hashing","archive_layout","modules","userspace_packages","gaming_payload_repack","dependency_closure","storage_calculation","pacman_policy","runtime_mounts","userspace_install","userspace_verification","module_install","module_verification","grub_update","depmod","initramfs","installation_state","mount_cleanup"}
 assert emitted==set(PHASES)
 first=adapt({"schemaVersion":1,"attempt":1,"phase":PHASES[0],"indeterminate":False,"completed":0,"total":5,"unit":"items"})
 assert first["overall"]["fractionMillionths"]==0 and first["currentOperation"]["fractionMillionths"]==0
 last=adapt({"schemaVersion":1,"attempt":1,"phase":PHASES[-1],"indeterminate":False,"completed":5,"total":5,"unit":"items"})
 assert last["overall"]["fractionMillionths"]==1_000_000
 heartbeat=adapt({"schemaVersion":1,"attempt":2,"phase":"initramfs","indeterminate":True})
 assert heartbeat["currentOperation"]=={"state":"indeterminate"} and heartbeat["overall"]["state"]=="indeterminate"
 future=adapt({"schemaVersion":1,"attempt":3,"phase":"future_phase","indeterminate":False,"completed":1,"total":3,"unit":"bytes","additive":True})
 assert future["phaseDisposition"]=="future" and future["overall"]=={"state":"indeterminate"}
 assert future["currentOperation"]["fractionMillionths"]==333333
 for document in (schema,json.loads((ROOT/"contracts/schemas/result-semantics-v1.schema.json").read_text())):
  assert_frontend_neutral(document)
 assert set(first)=={"schemaVersion","kind","attempt","phase","phaseDisposition","currentOperation","overall"}
 assert set(first["currentOperation"])=={"state","completed","total","unit","fractionMillionths"}
 assert set(first["overall"])=={"state","fractionMillionths","phaseIndex","phaseCount"}
 assert set(future)==set(first)
 assert set(future["overall"])=={"state"}
 assert_frontend_neutral(first); assert_frontend_neutral(future); assert_frontend_neutral(heartbeat)
 for bad in ({}, {"schemaVersion":2,"attempt":1,"phase":"initramfs","indeterminate":True},{"schemaVersion":1,"attempt":True,"phase":"initramfs","indeterminate":True},{"schemaVersion":1,"attempt":1,"phase":"Bad Phase","indeterminate":True},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":True,"completed":0},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":False,"completed":2,"total":1,"unit":"items"},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":False,"completed":0,"total":0,"unit":"items"}): reject(bad)
 for raw in ('{"a":1,"a":2}', '{', '{"x":NaN}'):
  try: strict(raw)
  except ContractError: pass
  else: raise AssertionError(raw)
if __name__=='__main__': main()
