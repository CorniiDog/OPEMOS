#!/usr/bin/env python3
"""Closed compatibility, ambiguity, additive, and hostile metadata tests."""
import copy,hashlib,json,sys
from pathlib import Path
import jsonschema
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"lib"))
from driver_release_metadata import MetadataError,canonical,select,validate
FIXTURE=ROOT/"contracts/fixtures/driver-release-metadata-v1.json"
SCHEMA=ROOT/"contracts/schemas/driver-release-metadata-v1.schema.json"
def identify(document):
 identity={key:value for key,value in document.items() if key not in {"artifactId","extensions"}}
 document["artifactId"]=hashlib.sha256(canonical(identity)).hexdigest();return document
def mutate(base,changes):
 document=copy.deepcopy(base)
 for path,value in changes.items():
  owner=document;fields=path.split(".")
  for field in fields[:-1]:owner=owner[field]
  owner[fields[-1]]=value
 return identify(document)
def main():
 matrix=json.loads(FIXTURE.read_text());schema=json.loads(SCHEMA.read_text())
 assert set(matrix)=={"schemaVersion","kind","supportedRequiredCapabilities","target","base","cases"}
 assert matrix["schemaVersion"]==1 and matrix["kind"]=="opemos-driver-release-hostile-fixtures"
 names=[]
 for case in matrix["cases"]:
  assert set(case)=={"name","mutation","expected"};names.append(case["name"])
  candidate=mutate(matrix["base"],case["mutation"])
  if case["name"]!="malformed-hash":jsonschema.validate(candidate,schema)
  result=select([candidate],matrix["target"],matrix["supportedRequiredCapabilities"])
  assert result["status"]==case["expected"],(case["name"],result)
  if case["name"] in {"valid","unknown-optional-capability"}:assert result["artifact"]==candidate
 assert len(names)==len(set(names))
 base=matrix["base"]
 assert select([base,copy.deepcopy(base)],matrix["target"],matrix["supportedRequiredCapabilities"])["status"]=="selected"
 second=copy.deepcopy(base);second["archive"]["name"]="opemos-driver-second-x86_64.tar.gz";second["archive"]["sha256"]="e"*64;identify(second)
 forward=select([base,second],matrix["target"],matrix["supportedRequiredCapabilities"])
 reverse=select([second,base],matrix["target"],matrix["supportedRequiredCapabilities"])
 assert forward==reverse and forward["status"]=="ambiguous"
 malformed=mutate(base,{"archive.sha256":"BAD"})
 assert select([base,malformed],matrix["target"],matrix["supportedRequiredCapabilities"])==select([malformed,base],matrix["target"],matrix["supportedRequiredCapabilities"])
 conflicting=copy.deepcopy(base);conflicting["extensions"]={"different":True}
 assert select([base,conflicting],matrix["target"],matrix["supportedRequiredCapabilities"])["status"]=="ambiguous"
 additive=copy.deepcopy(base);additive["extensions"]["future"]={"unknown":[1,2,3]};assert validate(additive)==additive
 hostile=[{**base,"unknownTopLevel":True},mutate(base,{"capabilities.optional":base["capabilities"]["required"]}),mutate(base,{"lifecycle.revocation":{"reason":"x","reference":"y"}})]
 for item in hostile:
  try:validate(item)
  except MetadataError:pass
  else:raise AssertionError("hostile metadata was accepted")
if __name__=="__main__":main()
