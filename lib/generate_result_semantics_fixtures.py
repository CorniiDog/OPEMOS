#!/usr/bin/env python3
"""Emit deterministic terminal-result semantics linked to result fixtures."""
import json, sys
from generate_installer_result_fixtures import matrix as result_matrix
from progress_semantics import adapt_result

def matrix():
 cases=[]
 for source in result_matrix()["cases"]:
  if source["expected"]["accepted"]:
   cases.append({"name":source["name"],"installerResultCase":source["name"],"expected":adapt_result(source["document"])})
 return {"schemaVersion":1,"kind":"opemos-result-semantics-compatibility-fixtures","sourceKind":"opemos-installer-result-compatibility-fixtures","cases":cases}
def main():
 payload=(json.dumps(matrix(),sort_keys=True,separators=(",",":"))+"\n").encode()
 if not 1<=len(payload)<=128*1024: raise SystemExit("result semantics fixture matrix size invalid")
 sys.stdout.buffer.write(payload)
if __name__=="__main__": main()
