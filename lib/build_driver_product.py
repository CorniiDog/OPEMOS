#!/usr/bin/env python3
"""Build one deterministic OPEMOS compiled-driver product archive."""
import argparse,gzip,hashlib,io,json,os,subprocess,tarfile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def fail(message): raise SystemExit(message)
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
 return h.hexdigest()
def canonical(value):return (json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()
def add(tar,name,data,mode=0o644):
 info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;info.uid=info.gid=0;info.uname=info.gname="";info.mtime=0
 tar.addfile(info,io.BytesIO(data))
def main():
 p=argparse.ArgumentParser()
 for name in ("archive","checksum","build-info","provenance"):p.add_argument("--"+name,required=True,type=Path)
 p.add_argument("--repository",default="CorniiDog/OPEMOS");p.add_argument("--output-dir",required=True,type=Path)
 a=p.parse_args()
 command=[str(ROOT/"lib/validate_publish_inputs.py"),"--archive",str(a.archive),"--checksum",str(a.checksum),
          "--build-info",str(a.build_info),"--provenance",str(a.provenance),"--repository",a.repository]
 checked=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if checked.returncode:fail("Compiled driver inputs failed canonical validation: "+checked.stderr.strip())
 plan=json.loads(checked.stdout)
 provenance=json.loads(a.provenance.read_text())
 target=provenance["target"];support=provenance["support"];source=provenance["source"]
 payload_hash=sha(a.archive);provenance_hash=sha(a.provenance);payload_path="payload/nvidia-driver.tar.zst"
 receipt={"schemaVersion":1,"status":"validated","target":target,
          "payloadSha256":payload_hash,"provenanceSha256":provenance_hash,
          "validator":{"repository":support["repository"],"commit":support["commit"]}}
 receipt_bytes=canonical(receipt)
 product_name=f'opemos-driver-{plan["tag"]}-{target["architecture"]}.tar.gz'
 manifest={"schemaVersion":1,"kind":"opemos-compiled-driver","creator":{"component":"OPEMOS Core","repository":support["repository"],"commit":support["commit"],"cleanupOwner":"core"},
  "target":target,"compatibility":{"kernel":"exact","architecture":"exact","fallback":False},
  "capabilities":["open-kernel-modules","offline-install","initramfs-integration"],
  "payload":{"path":payload_path,"sourceName":a.archive.name,"bytes":a.archive.stat().st_size,"sha256":payload_hash},
  "metadata":[
   {"role":"build-info","path":"metadata/BUILD-INFO.txt","bytes":a.build_info.stat().st_size,"sha256":sha(a.build_info)},
   {"role":"provenance","path":"metadata/PROVENANCE.json","bytes":a.provenance.stat().st_size,"sha256":provenance_hash},
   {"role":"validation-receipt","path":"metadata/VALIDATION-RECEIPT.json","bytes":len(receipt_bytes),"sha256":hashlib.sha256(receipt_bytes).hexdigest()}],
  "install":{"moduleDestination":f'/usr/lib/modules/{target["kernelVersion"]}/updates/nvidia',"runDepmod":True,"initramfs":{"required":True,"kernelVersion":target["kernelVersion"]}},
  "source":{"repository":source["repository"],"commit":source["commit"]},"releaseTag":plan["tag"]}
 manifest_bytes=canonical(manifest)
 a.output_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
 output=a.output_dir/product_name
 if output.exists() or (a.output_dir/(product_name+".sha256")).exists():fail("Product output already exists.")
 fd,temp=tempfile.mkstemp(prefix=".driver-product.",dir=a.output_dir);os.close(fd)
 published=False
 try:
  with open(temp,"wb") as raw:
   with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0,compresslevel=9) as zipped:
    with tarfile.open(fileobj=zipped,mode="w",format=tarfile.USTAR_FORMAT) as tar:
     add(tar,"DRIVER-MANIFEST.json",manifest_bytes)
     add(tar,payload_path,a.archive.read_bytes())
     add(tar,"metadata/BUILD-INFO.txt",a.build_info.read_bytes())
     add(tar,"metadata/PROVENANCE.json",a.provenance.read_bytes())
     add(tar,"metadata/VALIDATION-RECEIPT.json",receipt_bytes)
   raw.flush();os.fsync(raw.fileno())
  os.chmod(temp,0o644);os.link(temp,output);published=True;os.unlink(temp)
  digest=sha(output);sidecar=a.output_dir/(product_name+".sha256")
  flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW;fd=os.open(sidecar,flags,0o644)
  with os.fdopen(fd,"w") as f:f.write(f"{digest}  {product_name}\n");f.flush();os.fsync(f.fileno())
 except Exception:
  try:os.unlink(temp)
  except FileNotFoundError:pass
  if published:
   try:os.unlink(output)
   except FileNotFoundError:pass
  raise
 print(json.dumps({"schemaVersion":1,"status":"created","archive":str(output),"sha256":digest,"manifestSha256":hashlib.sha256(manifest_bytes).hexdigest()},sort_keys=True,separators=(",",":")))
if __name__=="__main__":main()
