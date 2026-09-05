#!/usr/bin/env python3
"""Canonical Core progress-record to consumer-semantics adapter."""
import argparse, json, re
from pathlib import Path
TOKEN=re.compile(r"[a-z][a-z0-9_]{0,63}")
PHASES=("target_identity","input_snapshot","archive_layout","modules","userspace","dependency_closure","storage_calculation","pacman_policy","runtime_mounts","userspace_install","userspace_verification","module_install","module_verification","grub_update","depmod","initramfs","installation_state","mount_cleanup")
MAX=4096
class ContractError(ValueError): pass
def strict(payload):
 def pairs(items):
  out={}
  for k,v in items:
   if k in out: raise ContractError("duplicate JSON key")
   out[k]=v
  return out
 try: return json.loads(payload,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ContractError("non-finite JSON")))
 except json.JSONDecodeError as e: raise ContractError("malformed JSON") from e
def adapt(record):
 if not isinstance(record,dict) or record.get("schemaVersion") != 1: raise ContractError("unsupported progress schema")
 attempt=record.get("attempt"); phase=record.get("phase"); ind=record.get("indeterminate")
 if not isinstance(attempt,int) or isinstance(attempt,bool) or not 0<=attempt<=1_000_000: raise ContractError("invalid attempt")
 if not isinstance(phase,str) or not TOKEN.fullmatch(phase): raise ContractError("invalid phase")
 if not isinstance(ind,bool): raise ContractError("invalid progress state")
 forbidden=any(k in record for k in ("completed","total","unit"))
 if ind:
  if forbidden: raise ContractError("indeterminate record has counters")
  current={"state":"indeterminate"}; frac=None
 else:
  completed=record.get("completed"); total=record.get("total"); unit=record.get("unit")
  if any(not isinstance(v,int) or isinstance(v,bool) for v in (completed,total)) or not 0<=completed<=total<=2**63-1 or total==0 or unit not in {"bytes","items"}: raise ContractError("invalid counters")
  frac=completed*1_000_000//total
  current={"state":"determinate","completed":completed,"total":total,"unit":unit,"fractionMillionths":frac}
 if phase in PHASES:
  index=PHASES.index(phase)
  overall={"state":"determinate","fractionMillionths":((index*1_000_000)+(frac or 0))//len(PHASES),"phaseIndex":index,"phaseCount":len(PHASES)} if not ind else {"state":"indeterminate","phaseIndex":index,"phaseCount":len(PHASES)}
  disposition="known"
 else:
  overall={"state":"indeterminate"}; disposition="future"
 return {"schemaVersion":1,"kind":"opemos-progress-semantics","attempt":attempt,"phase":phase,"phaseDisposition":disposition,"currentOperation":current,"overall":overall}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--record",type=Path,required=True); a=ap.parse_args()
 try:
  data=a.record.read_bytes()
  if not 1<=len(data)<=MAX: raise ContractError("record size invalid")
  doc=strict(data.decode("utf-8")); out=adapt(doc)
 except (OSError,UnicodeError,ContractError) as e: raise SystemExit(f"progress semantics rejected: {e}")
 print(json.dumps(out,sort_keys=True,separators=(",",":")))
if __name__=="__main__": main()
