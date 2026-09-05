#!/usr/bin/env python3
"""Emit deterministic cross-consumer progress-semantics fixtures."""
import json, sys
from progress_semantics import PHASES, adapt

def rec(phase, indeterminate=False, completed=None, total=None, unit=None, **extra):
 value={"schemaVersion":1,"attempt":7,"phase":phase,"indeterminate":indeterminate,**extra}
 if not indeterminate: value.update(completed=completed,total=total,unit=unit)
 return value
def matrix():
 inputs=[
  ("known-start",rec(PHASES[0],completed=0,total=5,unit="items")),
  ("known-complete",rec(PHASES[-1],completed=5,total=5,unit="items")),
  ("known-heartbeat",rec("initramfs",indeterminate=True)),
  ("future-determinate",rec("future_phase",completed=1,total=3,unit="bytes",additive=True)),
  ("maximum-counter",rec("modules",completed=2**63-1,total=2**63-1,unit="bytes")),
 ]
 return {"schemaVersion":1,"kind":"opemos-progress-semantics-compatibility-fixtures","cases":[{"name":name,"input":value,"expected":adapt(value)} for name,value in inputs]}
def main():
 payload=(json.dumps(matrix(),sort_keys=True,separators=(",",":"))+"\n").encode()
 if len(payload)>128*1024: raise SystemExit("fixture matrix excessive")
 sys.stdout.buffer.write(payload)
if __name__=="__main__": main()
