#!/usr/bin/env python3
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'lib'))
from progress_semantics import ContractError, PHASES, adapt, adapt_result, strict

def reject(value):
 try: adapt(value)
 except ContractError: return
 raise AssertionError(value)

def main():
 from generate_installer_result_fixtures import matrix
 from validate_install_contract import validate_result
 import tempfile
 with tempfile.TemporaryDirectory() as name:
  for case in matrix()["cases"]:
   if not case["expected"]["accepted"]: continue
   path=Path(name)/"result.json"; path.write_text(json.dumps(case["document"],sort_keys=True,separators=(",",":"))+"\n")
   result=adapt_result(validate_result(path))
   expected={"success":"succeeded","validated":"validated","failed":"failed","cancelled":"cancelled"}[case["expected"]["status"]]
   assert result["state"]==expected
   if expected in {"succeeded","validated"}: assert result["cleanupComplete"]
 schema=json.loads((ROOT/"contracts/schemas/progress-semantics-v1.schema.json").read_text())
 assert schema["additionalProperties"] is False
 assert schema["properties"]["kind"]["const"]=="opemos-progress-semantics"
 first=adapt({"schemaVersion":1,"attempt":1,"phase":PHASES[0],"indeterminate":False,"completed":0,"total":5,"unit":"items"})
 assert first["overall"]["fractionMillionths"]==0 and first["currentOperation"]["fractionMillionths"]==0
 last=adapt({"schemaVersion":1,"attempt":1,"phase":PHASES[-1],"indeterminate":False,"completed":5,"total":5,"unit":"items"})
 assert last["overall"]["fractionMillionths"]==1_000_000
 heartbeat=adapt({"schemaVersion":1,"attempt":2,"phase":"initramfs","indeterminate":True})
 assert heartbeat["currentOperation"]=={"state":"indeterminate"} and heartbeat["overall"]["state"]=="indeterminate"
 future=adapt({"schemaVersion":1,"attempt":3,"phase":"future_phase","indeterminate":False,"completed":1,"total":3,"unit":"bytes","additive":True})
 assert future["phaseDisposition"]=="future" and future["overall"]=={"state":"indeterminate"}
 assert future["currentOperation"]["fractionMillionths"]==333333
 for bad in ({}, {"schemaVersion":2,"attempt":1,"phase":"initramfs","indeterminate":True},{"schemaVersion":1,"attempt":True,"phase":"initramfs","indeterminate":True},{"schemaVersion":1,"attempt":1,"phase":"Bad Phase","indeterminate":True},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":True,"completed":0},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":False,"completed":2,"total":1,"unit":"items"},{"schemaVersion":1,"attempt":1,"phase":"initramfs","indeterminate":False,"completed":0,"total":0,"unit":"items"}): reject(bad)
 for raw in ('{"a":1,"a":2}', '{', '{"x":NaN}'):
  try: strict(raw)
  except ContractError: pass
  else: raise AssertionError(raw)
if __name__=='__main__': main()
