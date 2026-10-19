#!/usr/bin/env python3
"""Stage and validate one exact materialized driver product for recovery."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path


MAX_DOCUMENT = 64 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,254}")
ROLES = ("archive", "checksum", "provenance", "buildInfo")


def fail(message):
    raise SystemExit(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            fail("materialization result contains a duplicate key")
        result[key] = value
    return result


def read_document(path):
    payload = read_regular(path, MAX_DOCUMENT, "materialization result")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                              parse_constant=lambda _value: fail("invalid JSON number"))
    except (UnicodeError, json.JSONDecodeError):
        fail("materialization result is not canonical JSON")
    if payload != canonical(document):
        fail("materialization result is not canonical JSON")
    return document


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_gid, info.st_nlink,
            stat.S_IMODE(info.st_mode))


def read_regular(path, maximum, label):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail(f"{label} is missing or unsafe")
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= maximum):
            fail(f"{label} is missing or unsafe")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (len(payload) != before.st_size or len(payload) > maximum
                or file_identity(before) != file_identity(after)
                or file_identity(after) != file_identity(current)):
            fail(f"{label} changed while it was read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def validate(document, expected):
    if not isinstance(document, dict) or set(document) != {
            "schemaVersion", "status", "target", "core", "source", "release",
            "representation", "outputs"}:
        fail("materialization result fields are invalid")
    if document["schemaVersion"] != 1 or document["status"] != "materialized":
        fail("materialization result identity is unsupported")
    if document["target"] != {
            "steamosVersion": expected["steamos"],
            "kernelVersion": expected["kernel"],
            "nvidiaVersion": expected["nvidia"],
            "architecture": "x86_64"}:
        fail("cached driver product does not match the exact recovery target")
    core = document["core"]
    if core != {"repository": "CorniiDog/OPEMOS", "commit": expected["revision"]}:
        fail("cached driver product does not match the installed Core revision")
    source = document["source"]
    if (not isinstance(source, dict) or set(source) != {"repository", "commit"}
            or not isinstance(source["repository"], str)
            or COMMIT.fullmatch(source["commit"] or "") is None):
        fail("cached driver product source identity is invalid")
    release = document["release"]
    if (not isinstance(release, dict) or set(release) != {"repository", "tag"}
            or release["repository"] != "CorniiDog/OPEMOS"
            or not isinstance(release["tag"], str) or not release["tag"]):
        fail("cached driver product release identity is invalid")
    if document["representation"] != {
            "productMember": "payload/nvidia-driver.tar.zst",
            "installerContainer": "tar+gzip", "modules": "ko.zst",
            "conversion": "none-byte-identical"}:
        fail("cached driver product representation is unsupported")
    outputs = document["outputs"]
    if not isinstance(outputs, dict) or set(outputs) != set(ROLES):
        fail("cached driver product output inventory is invalid")
    names = set()
    for role in ROLES:
        record = outputs[role]
        if (not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}
                or SAFE_NAME.fullmatch(record.get("name", "")) is None
                or not isinstance(record.get("bytes"), int) or record["bytes"] <= 0
                or HEX.fullmatch(record.get("sha256", "")) is None
                or record["name"] in names):
            fail("cached driver product output inventory is invalid")
        names.add(record["name"])
    archive = outputs["archive"]["name"]
    if (not archive.endswith(".tar.gz")
            or outputs["checksum"]["name"] != archive + ".sha256"):
        fail("cached driver product archive names are invalid")
    return outputs


def validate_files(directory, outputs):
    directory = Path(directory)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        fail("cached driver product directory is unsafe")
    allowed = {record["name"] for record in outputs.values()} | {"materialization.json"}
    if {entry.name for entry in directory.iterdir()} != allowed:
        fail("cached driver product directory contains an unexpected entry")
    for name in allowed:
        info = (directory / name).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            fail("cached driver product file ownership or mode is unsafe")
    payloads = {}
    for role, record in outputs.items():
        maximum = MAX_ARCHIVE if role == "archive" else MAX_METADATA
        payload = read_regular(directory / record["name"], maximum, f"cached {role}")
        if len(payload) != record["bytes"] or digest(payload) != record["sha256"]:
            fail(f"cached {role} does not match the materialization result")
        payloads[role] = payload
    checksum = payloads["checksum"].decode("ascii", errors="strict")
    expected = f'{outputs["archive"]["sha256"]}  {outputs["archive"]["name"]}\n'
    if checksum != expected:
        fail("cached checksum is not canonical")


def load(directory, expected):
    directory = Path(os.path.abspath(os.fspath(directory)))
    if directory.is_symlink():
        fail("cached driver product directory is unsafe")
    directory = directory.resolve(strict=True)
    document = read_document(directory / "materialization.json")
    outputs = validate(document, expected)
    validate_files(directory, outputs)
    return document, outputs, directory


def copy_file(source, destination, maximum):
    payload = read_regular(source, maximum, source.name)
    with destination.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(destination, 0o600)


def command_stage(args):
    source_document = read_document(args.materialization)
    expected = {"steamos": args.steamos, "kernel": args.kernel,
                "nvidia": args.nvidia, "revision": args.support_revision}
    outputs = validate(source_document, expected)
    destination = args.destination.absolute()
    parent = destination.parent
    try:
        parent_info = parent.lstat()
    except OSError:
        fail("cached driver product parent is unavailable")
    if (not stat.S_ISDIR(parent_info.st_mode) or parent.is_symlink()
            or parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022):
        fail("cached driver product parent is unsafe")
    if destination.exists() or destination.is_symlink():
        fail("cached driver product destination already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    os.chmod(temporary, 0o700)
    try:
        for role, record in outputs.items():
            maximum = MAX_ARCHIVE if role == "archive" else MAX_METADATA
            copy_file(args.input_dir / record["name"], temporary / record["name"], maximum)
        result = temporary / "materialization.json"
        result.write_bytes(canonical(source_document))
        os.chmod(result, 0o600)
        validate_files(temporary, outputs)
        os.replace(temporary, destination)
        temporary = None
        descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    print(canonical(source_document).decode(), end="")


def command_show(args):
    expected = {"steamos": args.steamos, "kernel": args.kernel,
                "nvidia": args.nvidia, "revision": args.support_revision}
    document, outputs, directory = load(args.directory, expected)
    result = dict(document)
    result["paths"] = {role: str(directory / record["name"])
                       for role, record in outputs.items()}
    print(canonical(result).decode(), end="")


# Private install custody is deliberately separate from the public generation
# contract. The lifecycle lock held by guardian/driver/health serializes its
# consumers; the short cache lock serializes independent online producers.
HOME = Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')


def private(path):
    info=path.lstat()
    if (path.resolve()!=path or not stat.S_ISDIR(info.st_mode) or info.st_uid!=0
            or stat.S_IMODE(info.st_mode)!=0o700):
        fail('unsafe private install custody directory')


def private_record(path):
    info=path.lstat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600:
        fail('unsafe install custody record')
    return read_document(path)


def userspace_versions():
    import subprocess
    versions={}
    for name in ('nvidia-utils','lib32-nvidia-utils'):
        observed=subprocess.run(['/usr/bin/pacman','-Q',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if observed.returncode==1 and name=='lib32-nvidia-utils':
            versions[name]=None  # Preserve a real pre-existing absence, not an invented package requirement.
            continue
        if observed.returncode!=0:fail('installed userspace query failed')
        row=observed.stdout.decode().strip().split()
        if len(row)!=2 or row[0]!=name:fail('exact installed userspace identity unavailable')
        versions[name]=row[1]
    return versions


def retained_install(home, expected=None):
    if not home.exists():return None
    private(home)
    path=home/'pending-install.json'
    if not os.path.lexists(path):return None
    record=private_record(path)
    fields={'schemaVersion','product','expected','interstitialSHA256','userspace','userspaceInput'}
    if (set(record) not in (fields,fields|{'userspaceIntegrityRequired'})
            or record['schemaVersion']!=1 or HEX.fullmatch(record.get('product','')) is None
            or HEX.fullmatch(record.get('interstitialSHA256','')) is None):
        fail('invalid pending install custody')
    if expected is not None and record['expected']!=expected:
        fail('pending install target/revision differs; original custody preserved')
    private(home/'cached-products')
    cache=home/'cached-products'/record['product']
    doc,_,_=load(cache,record['expected'])
    if digest(canonical(doc))!=record['product']:fail('pending product identity changed')
    custody=home/'install-inputs'/record['product'];private(custody)
    binary=custody/'opemos-interstitial'
    info=binary.lstat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600:
        fail('unsafe retained interstitial')
    if digest(read_regular(binary,32*1024**2,'retained interstitial'))!=record['interstitialSHA256']:
        fail('retained interstitial changed')
    if userspace_versions()!=record['userspace']:fail('pending installed userspace changed')
    if 'userspaceIntegrityRequired' in record and record['userspaceIntegrityRequired'] is not True:
        fail('invalid private userspace integrity policy')
    if record.get('userspaceIntegrityRequired'):
        import subprocess
        checked=subprocess.run(['/usr/bin/pacman','-Qkk','nvidia-utils','lib32-nvidia-utils'],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        if checked.returncode!=0:
            import sys
            if len(checked.stdout)+len(checked.stderr)>MAX_METADATA:
                fail('private native userspace diagnostics exceed bound; hold health/completion')
            sys.stderr.buffer.write(checked.stdout+checked.stderr)
            fail('private installed userspace file integrity failed; hold health/completion')
    if record['userspaceInput'] is not None:
        verify_userspace_inputs(home,record['userspaceInput'],record['expected']['nvidia'])
    return record


def verify_userspace_inputs(home, identity, nvidia):
    if HEX.fullmatch(identity or '') is None:fail('invalid userspace input identity')
    directory=home/'userspace-inputs'/identity;private(directory)
    manifest=private_record(directory/'inputs.json')
    if digest(canonical(manifest))!=identity or manifest['nvidia']!=nvidia:
        fail('retained userspace identity changed')
    if len(manifest['files']) not in (2,3,4):fail('userspace inventory bound')
    if {p.name for p in directory.iterdir()}!={'inputs.json',*manifest['files']}:
        fail('unexpected userspace input')
    for name,row in manifest['files'].items():
        if SAFE_NAME.fullmatch(name) is None:fail('invalid package name')
        info=(directory/name).lstat()
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600:fail('unsafe retained package')
        data=read_regular(directory/name,MAX_ARCHIVE,'retained userspace package')
        if len(data)!=row['bytes'] or digest(data)!=row['sha256']:fail('retained userspace package changed')
    return manifest


def retain_userspace_inputs(home, nvidia, paths):
    # Called by existing setup producer BEFORE pacman -U and temporary cleanup.
    # Retention is custody, never a new signature/trust approval or install.
    import installed_update_transaction as tx
    if os.geteuid()!=0 or home!=HOME:fail('userspace custody requires fixed owning root')
    private(home)
    parent=home/'userspace-inputs'
    if not parent.exists():parent.mkdir(mode=0o700);tx.fsync_dir(home)
    private(parent)
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',nvidia):fail('invalid NVIDIA package target')
    names={p.name for p in paths}
    packages={name for name in names if not name.endswith('.sig')}
    patterns=[re.compile(re.escape(package+'-'+nvidia)+r'-[^/]+-x86_64\.pkg\.tar\.(?:zst|xz)')
              for package in ('nvidia-utils','lib32-nvidia-utils')]
    if (len(names)!=len(paths) or len(packages)!=2 or not 2<=len(names)<=4
            or any(sum(bool(pattern.fullmatch(name)) for name in packages)!=1 for pattern in patterns)
            or any(name.endswith('.sig') and name[:-4] not in packages for name in names)):
        fail('userspace producer inventory is not the exact two existing packages and optional signatures')
    files={}
    for path in paths:
        if SAFE_NAME.fullmatch(path.name) is None or path.name in files:fail('invalid package inventory')
        data=read_regular(path,MAX_ARCHIVE,'userspace producer input')
        files[path.name]={'bytes':len(data),'sha256':digest(data)}
    manifest={'schemaVersion':1,'nvidia':nvidia,'files':files,'trust':'custody-only-pacman-remains-authority'}
    identity=digest(canonical(manifest));destination=parent/identity
    if not destination.exists():
        if len(list(parent.iterdir()))>=10:fail('userspace custody bound; preserve inputs')
        temporary=Path(tempfile.mkdtemp(prefix='.input-',dir=parent));os.chmod(temporary,0o700)
        # Retain incomplete staging on failure: no broad cleanup of evidence.
        for path in paths:copy_file(path,temporary/path.name,MAX_ARCHIVE)
        tx.durable(temporary/'inputs.json',manifest);tx.fsync_dir(temporary)
        os.rename(temporary,destination);tx.fsync_dir(parent)
    verify_userspace_inputs(home,identity,nvidia)
    tx.durable(home/'userspace-input-current.json',{'input':identity})


def command_retain_install(args):
    import fcntl
    import installed_update_transaction as tx
    if os.geteuid()!=0 or args.home!=HOME:fail('install custody requires fixed owning root')
    home=args.home;private(home)
    lock=home/'install-custody.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid!=0 or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600:
            fail('unsafe install custody lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        expected={'steamos':args.steamos,'kernel':args.kernel,'nvidia':args.nvidia,'revision':args.support_revision}
        doc,_,directory=load(args.directory,expected);identity=digest(canonical(doc))
        if directory!=home/'cached-products'/identity:fail('install product not durably content addressed')
        existing=retained_install(home,expected)
        if existing:
            if existing['product']!=identity:fail('different first-install candidate pending; preserve it')
            if args.verify_userspace_files and existing.get('userspaceIntegrityRequired') is not True:
                fail('existing pending custody has another userspace policy; preserve it')
            record=existing
        else:
            binary=args.interstitial or home/'bin/opemos-interstitial'
            sha=args.interstitial_sha256 or read_regular(home/'interstitial.sha256',128,'interstitial receipt').decode().strip()
            if HEX.fullmatch(sha or '') is None:fail('explicit existing interstitial SHA required')
            data=read_regular(binary,32*1024**2,'interstitial input')
            if digest(data)!=sha:fail('interstitial input changed')
            # Existing binary validator is executed against the retained snapshot
            # below, not an unchecked mutable caller path.
            versions=userspace_versions()
            if any(v is not None and v.rsplit('-',1)[0]!=args.nvidia for v in versions.values()):fail('userspace target mismatch')
            inputs=None
            if (home/'userspace-input-current.json').exists():
                inputs=private_record(home/'userspace-input-current.json')['input']
                verify_userspace_inputs(home,inputs,args.nvidia)
            parent=home/'install-inputs'
            if not parent.exists():parent.mkdir(mode=0o700);tx.fsync_dir(home)
            private(parent)
            custody=parent/identity
            if not custody.exists():custody.mkdir(mode=0o700);tx.fsync_dir(parent)
            private(custody)
            saved=custody/'opemos-interstitial'
            if saved.exists():
                if read_regular(saved,32*1024**2,'retained interstitial')!=data:fail('conflicting retained interstitial; preserved')
            else:tx.replace_durable(saved,data,0o600)
            # CLI validation avoids importing an adapter with assumed ABI.
            import subprocess
            subprocess.run(['/usr/bin/python3',str(Path(__file__).with_name('validate_interstitial_binary.py')),
                            '--binary',str(saved),'--sha256',sha],check=True,timeout=30,stdout=subprocess.DEVNULL)
            record={'schemaVersion':1,'product':identity,'expected':expected,'interstitialSHA256':sha,
                    'userspace':versions,'userspaceInput':inputs}
            if args.verify_userspace_files:record['userspaceIntegrityRequired']=True
            from recovery_public_status import invalidate
            invalidate()
            tx.durable(home/'pending-install.json',record)
            retained_install(home,expected)
            tx.checkpoint('product-retained')
        print(canonical({'interstitial':str(home/'install-inputs'/identity/'opemos-interstitial'),
                         'sha256':record['interstitialSHA256']}).decode(),end='')
    finally:os.close(fd)


def command_pending(args):
    expected={'steamos':args.steamos,'kernel':args.kernel,'nvidia':args.nvidia,'revision':args.support_revision}
    record=retained_install(args.home,expected)
    if record:print(args.home/'cached-products'/record['product'])


def complete_install(home, intent):
    # Called only after actual driver boot health, never app-only acknowledgement
    # or an installer return code. Preserve completed custody; no input deletion.
    import installed_update_transaction as tx
    import fcntl
    private(home)
    fd=os.open(home/'install-custody.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            fail('unsafe install completion lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=retained_install(home)
        if record is None:return
        if (intent['phase']!='healthy' or intent['action']!='update'
                or intent['authority'].get('installProduct')!=record['product']):
            fail('pending install completion lacks exact healthy driver generation')
        destination=home/('completed-install-'+record['product']+'.json')
        if destination.exists():
            if private_record(destination)!=record:fail('conflicting completed install evidence')
        else:tx.durable(destination,record)
        os.unlink(home/'pending-install.json');tx.fsync_dir(home)

    finally:os.close(fd)


def command_select(args):
    home=args.home
    if (home/'pending-install.json').exists():
        command_pending(args);return
    pointer=home/'cached-current.json'
    if not pointer.exists():
        legacy=home/'cached-repair'
        if not legacy.exists():return
        document=read_document(legacy/'materialization.json')
        if document.get('target')!={'steamosVersion':args.steamos,'kernelVersion':args.kernel,
                                  'nvidiaVersion':args.nvidia,'architecture':'x86_64'}:
            return
        load(legacy,{'steamos':args.steamos,'kernel':args.kernel,'nvidia':args.nvidia,'revision':args.support_revision})
        print(legacy)
        return
    record=read_document(pointer)
    if set(record)!={'product'} or HEX.fullmatch(record['product'] or '') is None:
        fail('invalid retained product pointer')
    cache=home/'cached-products'/record['product']
    metadata=read_regular(cache/'materialization.json',1024*1024,'retained materialization')
    if digest(metadata)!=record['product']:fail('retained product identity changed')
    document=read_document(cache/'materialization.json')
    if (document.get('target')!={'steamosVersion':args.steamos,'kernelVersion':args.kernel,
                               'nvidiaVersion':args.nvidia,'architecture':'x86_64'} or
        document.get('core')!={'repository':'CorniiDog/OPEMOS','commit':args.support_revision}):
        return  # A valid different-target product is retained, never reused.
    load(cache,{'steamos':args.steamos,'kernel':args.kernel,'nvidia':args.nvidia,'revision':args.support_revision})
    print(cache)


def command_authenticate_local(args):
    """Snapshot the existing four-input format and validate before selection.

    This produces diagnostic selection data, not a trust receipt. Consumers
    pass the immutable snapshot through the existing canonical producer again.
    No package, rootfs, keyring or installed generation is changed here.
    """
    import subprocess
    import sys
    archive=args.archive.absolute()
    if (SAFE_NAME.fullmatch(archive.name) is None or not archive.name.startswith('nvidia-open-')
            or not archive.name.endswith('.tar.gz')):
        fail('private input must use the existing nvidia-open four-file archive format')
    checksum=args.checksum.absolute()
    if checksum.name!=archive.name+'.sha256':fail('private checksum filename differs')
    stem=archive.name.removesuffix('.tar.gz')
    sources={'archive':archive,'checksum':checksum,'buildInfo':archive.with_name(stem+'.build-info.txt'),
             'provenance':archive.with_name(stem+'.provenance.json')}
    destination=args.destination.absolute()
    if destination.exists() or destination.is_symlink():fail('private snapshot already exists; preserve it')
    parent=destination.parent
    info=parent.lstat()
    if (parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
            or stat.S_IMODE(info.st_mode)&0o077):fail('private snapshot parent must be exact and private')
    destination.mkdir(mode=0o700)
    for role,path in sources.items():
        copy_file(path,destination/path.name,MAX_ARCHIVE if role=='archive' else MAX_METADATA)
    before={role:digest(read_regular(destination/path.name,MAX_ARCHIVE if role=='archive' else MAX_METADATA,'private snapshot'))
            for role,path in sources.items()}
    if args.expected_archive_sha256 is not None and before['archive']!=args.expected_archive_sha256:
        fail('private archive differs from the selected producer input')
    argv=[sys.executable,str(Path(__file__).with_name('validate_publish_inputs.py')),
          '--archive',str(destination/archive.name),'--checksum',str(destination/checksum.name),
          '--build-info',str(destination/sources['buildInfo'].name),
          '--provenance',str(destination/sources['provenance'].name),'--repository','CorniiDog/OPEMOS']
    result=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    if len(result.stdout)>MAX_DOCUMENT or len(result.stderr)>MAX_METADATA:
        fail('private validator output exceeds bound')
    if result.returncode:
        sys.stderr.buffer.write(result.stderr)
        fail('private product authentication failed; no userspace selection authorized')
    after={role:digest(read_regular(destination/path.name,MAX_ARCHIVE if role=='archive' else MAX_METADATA,'private snapshot'))
           for role,path in sources.items()}
    if before!=after:fail('private input changed across actual validator')
    validated=json.loads(result.stdout)
    provenance=json.loads(read_regular(destination/sources['provenance'].name,MAX_METADATA,'private provenance'))
    target=provenance['target']
    if (validated.get('status')!='ready' or target!={
            'steamosVersion':args.steamos,'kernelVersion':args.kernel,
            'nvidiaVersion':target.get('nvidiaVersion'),'architecture':'x86_64'}):
        fail('private product must match the actual exact SteamOS/kernel target')
    if COMMIT.fullmatch(args.support_revision or '') is None:fail('exact consumer Core revision required')
    if (provenance['artifact'].get('representation')=='ko.zst'
            and provenance['support']['commit']!=args.support_revision):
        fail('private compressed input belongs to another Core; supply its original authenticated raw four-file input, never relabel it')
    # Complete snapshot durability before the caller may select userspace.
    fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    print(canonical({'schemaVersion':1,'target':target,'producerCore':provenance['support'],
                     'source':provenance['source'],'archiveSHA256':before['archive'],
                     'paths':{role:str(destination/path.name) for role,path in sources.items()}}).decode(),end='')


def command_snapshot_userspace(args):
    """Snapshot pinned transport bytes, NOT a signature/trust receipt."""
    payload=read_regular(args.manifest,MAX_DOCUMENT,'userspace input manifest')
    if HEX.fullmatch(args.manifest_sha256) is None or digest(payload)!=args.manifest_sha256:
        fail('userspace input manifest digest differs')
    try:
        document=json.loads(payload,object_pairs_hook=pairs,
                            parse_constant=lambda _value:fail('invalid JSON number'))
    except (UnicodeError,json.JSONDecodeError):
        fail('userspace input manifest is invalid')
    if (payload!=canonical(document) or type(document) is not dict or
            set(document)!={'schemaVersion','nvidiaVersion','packages'} or
            type(document['schemaVersion']) is not int or document['schemaVersion']!=1 or
            document['nvidiaVersion']!=args.nvidia or
            re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',args.nvidia) is None or
            type(document['packages']) is not list or len(document['packages'])!=2):
        fail('userspace input manifest fields/target differ')
    frozen={};names={}
    for row in document['packages']:
        fields={'name','filename','bytes','sha256','signatureFilename','signatureBytes','signatureSha256'}
        if type(row) is not dict or set(row)!=fields:
            fail('userspace package input fields differ')
        name=row['name']
        if type(name) is not str or name not in ('nvidia-utils','lib32-nvidia-utils') or name in names:
            fail('userspace package roles differ')
        filename=row['filename']
        pattern=re.escape(name+'-'+args.nvidia)+r'-[1-9][0-9]*(?:\.[0-9]+)*-x86_64\.pkg\.tar\.(?:zst|xz)'
        if type(filename) is not str or re.fullmatch(pattern,filename) is None or row['signatureFilename']!=filename+'.sig':
            fail('userspace package filename/target differs')
        names[name]=filename
        for filekey,sizekey,hashkey,cap in [('filename','bytes','sha256',768*1024**2),
                                          ('signatureFilename','signatureBytes','signatureSha256',1024**2)]:
            if (type(row[sizekey]) is not int or not 0<row[sizekey]<=cap or
                    type(row[hashkey]) is not str or HEX.fullmatch(row[hashkey]) is None):
                fail('userspace package input bounds differ')
            data=read_regular(args.manifest.parent/row[filekey],cap,'userspace package input')
            if len(data)!=row[sizekey] or digest(data)!=row[hashkey]:
                fail('userspace package input digest differs')
            frozen[row[filekey]]=data
    destination=args.destination.absolute();parent=destination.parent
    info=parent.lstat()
    if (parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode) or
            info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077):
        fail('userspace snapshot parent is not private')
    destination.mkdir(mode=0o700)  # create-only; never replace another attempt
    for filename,data in frozen.items():
        with (destination/filename).open('xb') as output:
            os.chmod(destination/filename,0o600)
            output.write(data);output.flush();os.fsync(output.fileno())
    descriptor=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    print(canonical({'status':'snapshotted-unverified','packages':names,
                     'manifestSHA256':args.manifest_sha256}).decode(),end='')


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot=subparsers.add_parser('snapshot-userspace')
    snapshot.add_argument('--manifest',type=Path,required=True)
    snapshot.add_argument('--manifest-sha256',required=True)
    snapshot.add_argument('--nvidia',required=True)
    snapshot.add_argument('--destination',type=Path,required=True)
    for name in ("stage", "show", "select", "pending", "retain-install", "authenticate-local"):
        command = subparsers.add_parser(name)
        command.add_argument("--steamos", required=True)
        command.add_argument("--kernel", required=True)
        if name != "authenticate-local":command.add_argument("--nvidia", required=True)
        command.add_argument("--support-revision", required=True)
        if name == "authenticate-local":
            command.add_argument("--archive", required=True, type=Path)
            command.add_argument("--checksum", required=True, type=Path)
            command.add_argument("--destination", required=True, type=Path)
            command.add_argument("--expected-archive-sha256")
        elif name == "stage":
            command.add_argument("--materialization", required=True, type=Path)
            command.add_argument("--input-dir", required=True, type=Path)
            command.add_argument("--destination", required=True, type=Path)
        elif name in ("select", "pending", "retain-install"):
            command.add_argument("--home", required=True, type=Path)
            if name == "retain-install":
                command.add_argument("--directory", required=True, type=Path)
                command.add_argument("--interstitial", type=Path)
                command.add_argument("--interstitial-sha256")
                command.add_argument("--verify-userspace-files", action="store_true")
        else:
            command.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    ({"snapshot-userspace":command_snapshot_userspace,"stage":command_stage,"show":command_show,"select":command_select,"pending":command_pending,"retain-install":command_retain_install,"authenticate-local":command_authenticate_local}[args.command])(args)


if __name__ == "__main__":
    main()
