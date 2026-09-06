#!/usr/bin/env python3
"""Product archive identity, metadata, idempotency, and failure tests."""
import hashlib,json,subprocess,tarfile,tempfile
from pathlib import Path
import jsonschema,publisher
ROOT=Path(__file__).resolve().parent.parent
TOOL=ROOT/"lib/build_driver_product.py"
SCHEMA=json.loads((ROOT/"contracts/schemas/driver-product-manifest-v1.schema.json").read_text())
def run(paths,out):
 return subprocess.run([str(TOOL),"--archive",str(paths[0]),"--checksum",str(paths[1]),"--build-info",str(paths[2]),"--provenance",str(paths[3]),"--output-dir",str(out)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def digest(data):return hashlib.sha256(data).hexdigest()
def main():
 with tempfile.TemporaryDirectory(prefix="driver-product-") as temporary:
  root=Path(temporary);paths=publisher.fixture(root/"inputs")
  one=run(paths,root/"one");two=run(paths,root/"two")
  assert one.returncode==two.returncode==0,(one.stderr,two.stderr)
  first=json.loads(one.stdout);second=json.loads(two.stdout)
  assert first["sha256"]==second["sha256"]
  archive=Path(first["archive"]);sidecar=Path(str(archive)+".sha256")
  assert sidecar.read_text()==f'{digest(archive.read_bytes())}  {archive.name}\n'
  with tarfile.open(archive,"r:gz") as product:
   assert product.getnames()==["DRIVER-MANIFEST.json","payload/nvidia-driver.tar.zst","metadata/BUILD-INFO.txt","metadata/PROVENANCE.json","metadata/VALIDATION-RECEIPT.json"]
   manifest=json.load(product.extractfile("DRIVER-MANIFEST.json"));jsonschema.validate(manifest,SCHEMA)
   receipt=json.load(product.extractfile("metadata/VALIDATION-RECEIPT.json"))
   assert manifest["creator"]["cleanupOwner"]=="core"
   assert manifest["target"]["kernelVersion"]==publisher.KERNEL
   assert manifest["compatibility"]=={"kernel":"exact","architecture":"exact","fallback":False}
   assert manifest["install"]["initramfs"]=={"required":True,"kernelVersion":publisher.KERNEL}
   assert receipt["status"]=="validated" and receipt["payloadSha256"]==digest(paths[0].read_bytes())
   assert product.extractfile("payload/nvidia-driver.tar.zst").read()==paths[0].read_bytes()
   for member in product.getmembers():
    assert (member.uid,member.gid,member.mtime,member.mode)==(0,0,0,0o644)
  repeated=run(paths,root/"one");assert repeated.returncode!=0 and "already exists" in repeated.stderr
  sidecar_only=root/"sidecar-only";sidecar_only.mkdir()
  expected_name=f"opemos-driver-{publisher.TAG}-{"x86_64"}.tar.gz"
  (sidecar_only/(expected_name+".sha256")).write_text("reserved\n")
  collision=run(paths,sidecar_only)
  assert collision.returncode!=0 and "already exists" in collision.stderr
  assert not (sidecar_only/expected_name).exists()
  bad=publisher.fixture(root/"bad");bad[1].write_text("0"*64+"  "+bad[0].name+"\n")
  refused=run(bad,root/"bad-out");assert refused.returncode!=0
  assert not (root/"bad-out").exists()
if __name__=="__main__":main()
