#!/usr/bin/env python3
"""Durable, network-free execution/status boundary for immutable releases."""
import argparse, fcntl, json, os, re, tempfile
from pathlib import Path
from release_operation import operation, strict_object

FIELDS = {"schemaVersion","operationId","repository","tag","targetCommit","attempt","lifecycle","decision","progress","assets","message"}
TERMINAL = {"succeeded","failed","cancelled"}

def fail(message): raise SystemExit(message)

def validate_state(value):
    if not isinstance(value,dict) or set(value)!=FIELDS or value.get("schemaVersion")!=1:
        fail("Release operation state is malformed.")
    if (not re.fullmatch(r"[0-9a-f]{64}",value.get("operationId","")) or
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",value.get("repository","")) or
        not isinstance(value.get("tag"),str) or not 0<len(value["tag"])<=200 or
        not re.fullmatch(r"[0-9a-f]{40}",value.get("targetCommit","")) or
        not isinstance(value.get("attempt"),int) or isinstance(value.get("attempt"),bool) or
        not 1<=value["attempt"]<=1000):
        fail("Release operation identity is malformed.")
    lifecycle=value.get("lifecycle");decision=value.get("decision")
    allowed={"planned":"create","reconciling":"retry-missing","succeeded":"already-complete",
             "failed":"conflict","cancelled":"cancelled"}
    if lifecycle not in allowed or decision!=allowed[lifecycle]:
        fail("Release operation lifecycle is malformed.")
    progress=value.get("progress")
    if not isinstance(progress,dict) or set(progress)!={"phase","completedAssets","totalAssets","indeterminate"}:
        fail("Release operation progress is malformed.")
    assets=value.get("assets")
    if not isinstance(assets,list) or not 4<=len(assets)<=16:
        fail("Release operation asset inventory is malformed.")
    names=set()
    for asset in assets:
        if (not isinstance(asset,dict) or set(asset)!={"name","sha256","bytes","state"} or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}",asset.get("name","")) or
            asset["name"] in names or not re.fullmatch(r"[0-9a-f]{64}",asset.get("sha256","")) or
            not isinstance(asset.get("bytes"),int) or isinstance(asset.get("bytes"),bool) or
            not 1<=asset["bytes"]<=2*1024*1024*1024 or
            asset.get("state") not in {"pending","present","missing","conflict"}):
            fail("Release operation asset inventory is malformed.")
        names.add(asset["name"])
    completed=sum(asset["state"]=="present" for asset in assets)
    phase={"planned":"planning","reconciling":"reconciling","succeeded":"complete",
           "failed":"failed","cancelled":"cancelled"}[lifecycle]
    if (progress.get("phase")!=phase or progress.get("completedAssets")!=completed or
        progress.get("totalAssets")!=len(assets) or
        progress.get("indeterminate") is not (lifecycle in {"planned","cancelled"})):
        fail("Release operation progress does not match lifecycle and asset state.")
    if not isinstance(value.get("message"),str) or len(value["message"])>500:
        fail("Release operation message is malformed.")
    return value

def read_state(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024*1024:
            fail("Release operation state is not a bounded regular file.")
        return validate_state(json.loads(path.read_text(), object_pairs_hook=no_duplicates))
    except (OSError,UnicodeError,json.JSONDecodeError):
        fail("Release operation state is unreadable.")

def no_duplicates(pairs):
    value={}
    for key,item in pairs:
        if key in value: fail("Release operation state contains a duplicate field.")
        value[key]=item
    return value

def write_state(path,value,create=False):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.parent.is_symlink(): fail("Release operation state directory is unsafe.")
    data=(json.dumps(validate_state(value),sort_keys=True,separators=(",",":"))+"\n").encode()
    if create:
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError: return False
        with os.fdopen(fd,"wb") as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        return True
    fd,name=tempfile.mkstemp(prefix=".release-operation.",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass
    return True

def emit(value): print(json.dumps(value,sort_keys=True,separators=(",",":")))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["execute","status","reconcile","cancel"])
    parser.add_argument("--state",required=True,type=Path)
    parser.add_argument("--plan",type=Path)
    parser.add_argument("--observed",type=Path)
    parser.add_argument("--attempt",type=int,default=1)
    args=parser.parse_args()
    lock=Path(str(args.state)+".lock")
    lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    descriptor=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        os.fchmod(descriptor,0o600);fcntl.flock(descriptor,fcntl.LOCK_EX)
        if args.command=="status":
            if args.plan or args.observed or args.attempt!=1: fail("Status accepts only --state.")
            emit(read_state(args.state));return
        if args.command=="execute":
            if not args.plan or args.observed: fail("Execute requires --plan and forbids --observed.")
            candidate=operation(strict_object(args.plan),args.attempt)
            if write_state(args.state,candidate,create=True): emit(candidate);return
            current=read_state(args.state)
            if current["operationId"]!=candidate["operationId"]: fail("Existing state belongs to a different release operation.")
            emit(current);return
        current=read_state(args.state)
        if args.command=="cancel":
            if args.plan or args.observed or args.attempt!=1: fail("Cancel accepts only --state.")
            if current["lifecycle"] not in TERMINAL:
                current["lifecycle"]="cancelled";current["decision"]="cancelled"
                current["progress"]={"phase":"cancelled","completedAssets":sum(a["state"]=="present" for a in current["assets"]),"totalAssets":len(current["assets"]),"indeterminate":True}
                current["message"]="Operation was cancelled; no additional remote completion is claimed."
                write_state(args.state,current)
            emit(current);return
        if not args.plan or not args.observed: fail("Reconcile requires --plan and --observed.")
        candidate=operation(strict_object(args.plan),args.attempt,strict_object(args.observed))
        if candidate["operationId"]!=current["operationId"]: fail("Reconcile plan differs from durable operation identity.")
        if current["lifecycle"]=="cancelled": fail("Cancelled release operations cannot be reconciled.")
        write_state(args.state,candidate);emit(candidate)
    finally:
        os.close(descriptor)

if __name__=="__main__": main()
