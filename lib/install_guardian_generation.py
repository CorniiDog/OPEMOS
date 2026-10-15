#!/usr/bin/env python3
"""Compose guardian source/unit files with the installed artifact transaction.

A retained, root-only recovery engine and independent boot entry are installed
before replacing any mutable guardian code. No OS slot or trust key is selected.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import uuid
import installed_update_transaction as tx

BOOT = Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery-bootstrap')
DEST = Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')
EXTRA = ('bootstrap/run_repair_with_interstitial.sh','lib/install_guardian_generation.py',
         'lib/installed_boot_guard.py')


def private_directory(path):
    if not path.parent.exists():
        # Create only the fixed Core installation ancestry after validating the
        # nearest existing root-owned ancestor. Never follow a parent alias.
        missing=[]; parent=path.parent
        while not parent.exists():missing.append(parent);parent=parent.parent
        info=parent.lstat()
        tx.need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'unsafe bootstrap ancestor')
        for entry in reversed(missing):entry.mkdir(mode=0o755);tx.fsync_dir(entry.parent)
    tx.need(path.parent.resolve()==path.parent,'bootstrap parent alias')
    if not path.exists():
        path.mkdir(mode=0o700)
        tx.fsync_dir(path.parent)
    info = path.lstat()
    tx.need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'private bootstrap directory required')


def registered():
    pointer = BOOT / 'current.json'
    if not pointer.exists():
        tx.need(not (BOOT/'boot-guard.py').exists() and not list(BOOT.glob('app-*')),
                'bootstrap registry missing; preserve retained operations')
        return {'schemaVersion': 1, 'app': None, 'driver': None}
    doc = tx.parse(tx.read(pointer, 16384))
    tx.need(set(doc) == {'schemaVersion','app','driver'} and doc['schemaVersion'] == 1,
            'invalid bootstrap registry')
    return doc


TEMPLATES=tuple('support/recovery/'+name for name in (
    'opemos-nvidia-guardian.service.in','opemos-interstitial.service.in',
    'opemos-nvidia-repair.service.in','opemos-nvidia-health.service.in',
    'opemos-nvidia-repair.timer','opemos-nvidia-health.timer',
    '90-opemos-nvidia-repair','90-opemos-nvidia-guardian.conf'))


def freeze_source(source,revision):
    import subprocess
    from recovery_cached_product import read_regular
    actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],timeout=30).decode().strip()
    tx.need(actual==revision,'guardian checkout commit differs')
    frozen={}
    for relative in dict.fromkeys((*tx.GUARDIAN_FILES,*EXTRA,*TEMPLATES)):
        data=read_regular(source/relative,8*1024**2,'immutable guardian source')
        committed=subprocess.check_output(['git','-C',str(source),'show',revision+':'+relative],timeout=30)
        tx.need(data==committed,'guardian source/template differs from immutable Git input: '+relative)
        frozen[relative]=data
    return frozen


def retained_driver_source():
    # Driver admission requires an acknowledged app. Consume its private sealed
    # candidate bytes, not another read from the live checkout after validation.
    import installed_boot_guard as guard
    doc=registered();tx.need(doc['app'] is not None,'no permanent app generation')
    engine,store=guard.engine(doc['app']);intent=engine.load_intent(Path('/'),store)
    tx.need(intent['phase']=='healthy','permanent app must be acknowledged')
    engine.verify_set(Path('/'),intent,'newSHA256')
    result={}
    for row in intent['files']:
        if row['path'].startswith(tx.GUARDIAN_HOME):
            name=row['path'][len(tx.GUARDIAN_HOME):]
            if row['candidate'] is not None:
                data=engine.read(store/row['candidate'],engine.MAX_FILE)
                tx.need(engine.digest(data)==row['newSHA256'],'retained source changed')
                result[name]=data
    return result


def register(kind, store, frozen):
    private_directory(BOOT)
    doc = registered()
    if doc[kind] is not None:
        import installed_boot_guard as guard
        old_engine,old_store=guard.engine(doc[kind])
        old_intent=old_engine.load_intent(Path('/'),old_store)
        tx.need(old_intent['phase'] in ('healthy','restored'),
                'registered operation is still pending; preserve it')
    private_directory(store / 'engine')
    hashes = {}
    for name in ('installed_update_transaction.py','atomic_output.py'):
        data = frozen['lib/'+name]
        tx.need(0 < len(data) <= 256 * 1024, 'recovery engine size')
        tx.replace_durable(store / 'engine' / name, data, 0o500)
        hashes[name] = tx.digest(data)
    data = frozen['lib/installed_boot_guard.py']
    boot_script = BOOT / 'boot-guard.py'
    if boot_script.exists() and tx.read(boot_script, 256 * 1024) != data:
        # Never rewrite the bootstrap while an earlier transaction needs it.
        import installed_boot_guard as guard
        for entry in registered().values():
            if type(entry) is dict:
                engine, previous = guard.engine(entry)
                prior = engine.load_intent(Path('/'), previous)
                tx.need(prior['phase'] in ('healthy','restored'), 'pending bootstrap version; preserve it')
    tx.replace_durable(boot_script, data, 0o500)
    doc[kind] = {'store':str(store),'engine':hashes}
    tx.durable(BOOT / 'current.json', doc)


def acknowledge_app():
    doc = registered()
    tx.need(doc['app'] is not None,'no installed app generation to acknowledge')
    import installed_boot_guard as guard
    engine, store = guard.engine(doc['app'])
    lock = engine.guarded_store(store)
    try:
        intent = engine.load_intent(Path('/'), store)
        engine.need(intent['action']=='guardian-update','not an app generation')
        engine.need(engine.read(BOOT/'boot-guard.py',256*1024)==
                    engine.read(DEST/'lib/installed_boot_guard.py',256*1024),'bootstrap/source mismatch')
        unit=engine.read(Path('/etc/systemd/system/opemos-nvidia-guardian.service'),65536)
        engine.need(b'ExecStart=/usr/bin/python3 '+str(BOOT).encode()+b'/boot-guard.py' in unit,
                    'independent recovery entry unavailable')
        engine.need(os.readlink('/etc/systemd/system/multi-user.target.wants/opemos-nvidia-guardian.service')==
                    '../opemos-nvidia-guardian.service','recovery service not enabled')
        if intent['phase']=='healthy':
            engine.verify_set(Path('/'),intent,'newSHA256')
            return {'state':'healthy','appHealthOnly':True}
        before=intent['authority'].get('stockBootBefore')
        if before is not None:
            from installed_driver_candidate import observe_stock_boot
            observed=observe_stock_boot(Path('/'),intent['target'])
            engine.need(intent['attemptBootId']==engine.boot_id() and intent['preparedBootId']!=engine.boot_id(),
                        'app stock qualification requires actual new registered boot')
            engine.need(before['images']==observed['images'] and before['kernelSHA256']==observed['kernelSHA256']
                        and before['rootIdentity']==observed['rootIdentity'],'stock boot assets changed across app boot')
            intent['authority']['stockBootAcknowledgement']=observed
            engine.durable(store/'intent.json',intent)
        result=engine.acknowledge(Path('/'),store)
        return {**result,'appHealthOnly':True}
    finally:
        os.close(lock)


def build(args):
    tx.need(os.geteuid() == 0 and re.fullmatch('[0-9a-f]{40}',args.revision)
            and re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',args.nvidia), 'exact installed authority required')
    tx.need(Path('/etc/systemd/system').is_dir(),'existing systemd entry directory required')
    private_directory(BOOT)
    # Source comes from the existing authenticated immutable Git checkout.
    # Bind every copied managed byte to that exact commit, including engines.
    import subprocess
    frozen=freeze_source(args.source,args.revision)
    existing=registered()
    if not (BOOT/'current.json').exists():tx.durable(BOOT/'current.json',existing)
    old=existing['app']
    if old:
        import installed_boot_guard as guard
        engine,prior=guard.engine(old)
        previous=engine.load_intent(Path('/'),prior)
        if previous['phase'] in ('prepared','activating','rolling-back'):
            from installed_driver_candidate import readonly_enabled,command
            readonly=previous['readonlyBefore'] or readonly_enabled()
            try:
                if readonly:command(['/usr/bin/steamos-readonly','disable'],30)
                engine.recover(Path('/'),prior)
            finally:
                if readonly:command(['/usr/bin/steamos-readonly','enable'],30)
            previous=engine.load_intent(Path('/'),prior)
        if (previous['authority']['policy']=={'supportRevision':args.revision,'nvidiaVersion':args.nvidia}
                and previous['phase'] in ('healthy','pending-health')):
            engine.verify_set(Path('/'),previous,'newSHA256')
            unchanged=True
            if (previous['phase']=='healthy' and existing['driver'] is None
                    and not Path('/sys/module/nvidia').exists() and previous['authority'].get('stockBootBefore') is not None):
                from installed_driver_candidate import observe_stock_boot
                current=observe_stock_boot(Path('/'),previous['target'])
                acknowledged=previous['authority'].get('stockBootAcknowledgement',{})
                unchanged=(current['images']==acknowledged.get('images') and
                           current['kernelSHA256']==acknowledged.get('kernelSHA256'))
            if unchanged:return {'state':previous['phase'],'sourceUnchanged':True}
            # Real changed stock assets need a new actual app boot observation,
            # even when source bytes are unchanged; never relabel old proof.

        tx.need(previous['phase'] in ('healthy','restored'),'guardian generation awaits reboot/health')
        engine.verify_set(Path('/'),previous,'newSHA256' if previous['phase']=='healthy' else 'oldSHA256')
    if existing['driver'] is not None:
        import installed_boot_guard as guard
        engine,driver=guard.engine(existing['driver'])
        record=engine.parse(engine.read(driver/'intent.json',engine.MAX_STATE))
        tx.need(record['phase'] in ('healthy','restored'),'driver update pending; guardian authority must stay fixed')
    store = BOOT / ('app-' + uuid.uuid4().hex)
    tx.need(len(list(BOOT.iterdir())) < 24, 'retained source generation bound; preserve originals')
    private_directory(store)
    rows = []
    def add(relative, data, mode):
        source = store / ('artifact-' + str(len(rows)))
        tx.replace_durable(source,data,mode)
        rows.append({'path':relative,'source':str(source),'sha256':tx.digest(data)})
    for relative in (*tx.GUARDIAN_FILES,*EXTRA):
        if relative in {r['path'].removeprefix(tx.GUARDIAN_HOME) for r in rows}:
            continue
        data=frozen[relative]
        original = DEST / relative
        if relative.startswith('trust/') and original.exists():
            tx.need(tx.read(original, 1024**2) == data, 'trust input change requires separate approval')
        add(tx.GUARDIAN_HOME + relative,data,0o644 if relative.startswith('trust/') or relative.endswith('common.sh') else 0o755)
    add(tx.GUARDIAN_HOME+'support-revision',(args.revision+'\n').encode(),0o644)
    add(tx.GUARDIAN_HOME+'nvidia-version',(args.nvidia+'\n').encode(),0o644)
    if args.interstitial is not None:
        from recovery_cached_product import read_regular
        data=read_regular(args.interstitial,32*1024**2,'interstitial')
        tx.need(tx.digest(data)==args.interstitial_sha256,'interstitial bytes changed')
        add(tx.GUARDIAN_HOME+'bin/opemos-interstitial',data,0o755)
        add(tx.GUARDIAN_HOME+'interstitial.sha256',(args.interstitial_sha256+'\n').encode(),0o644)
    else:
        binary=tx.read(DEST/'bin/opemos-interstitial',32*1024**2)
        expected=tx.read(DEST/'interstitial.sha256',128).decode('ascii').strip()
        tx.need(re.fullmatch('[0-9a-f]{64}',expected) and tx.digest(binary)==expected,
                'first guardian requires authenticated interstitial; existing binary must retain exact receipt')
    for name in ('opemos-nvidia-guardian.service','opemos-interstitial.service',
                 'opemos-nvidia-repair.service','opemos-nvidia-health.service'):
        data=frozen['support/recovery/'+name+'.in'].replace(b'@DEST@',str(DEST).encode())
        if name=='opemos-nvidia-guardian.service':
            data=data.replace(b'ExecStart='+str(DEST).encode()+b'/bootstrap/run_guardian_with_interstitial.sh',
                              b'ExecStart=/usr/bin/python3 '+str(BOOT).encode()+b'/boot-guard.py')
        add('etc/systemd/system/'+name,data,0o644)
    for name in ('opemos-nvidia-repair.timer','opemos-nvidia-health.timer'):
        add('etc/systemd/system/'+name,frozen['support/recovery/'+name],0o644)
    for relative,source in [('etc/NetworkManager/dispatcher.d/90-opemos-nvidia-repair','90-opemos-nvidia-repair'),
                            ('etc/atomic-update.conf.d/90-opemos-nvidia-guardian.conf','90-opemos-nvidia-guardian.conf')]:
        add(relative,frozen['support/recovery/'+source],0o755 if 'dispatcher.d' in relative else 0o644)
    for relative,targets in tx.LINK_TARGETS.items():
        if relative != 'etc/systemd/system/default.target':
            add(relative,b'OP_DEFAULT_TARGET_LINK\0'+targets[0].encode(),0o777)
    validator=store/'validate-paths.py'
    tx.replace_durable(validator,frozen['lib/validate_recovery_install_path.py'],0o500)
    check=['/usr/bin/python3',str(validator),'--root','/']
    for row in rows:
        if row['path'] in tx.LINK_TARGETS:
            check += ['--expected-symlink', row['path']+'='+tx.LINK_TARGETS[row['path']][0]]
        else:check += ['--path',row['path']]
    subprocess.run(check,check=True,timeout=30)
    tx.durable(store/'plan.json',{'schemaVersion':1,'kernel':os.uname().release,'rootIdentity':tx.root_identity(Path('/')),
                               'completeWriterSet':True,'files':rows})
    from installed_driver_candidate import readonly_enabled,command
    readonly=readonly_enabled()
    authority={'policy':{'supportRevision':args.revision,'nvidiaVersion':args.nvidia}}
    directory=Path('/usr/lib/modules')/os.uname().release/'updates/open-gpu-kernel-modules-steamos'
    if not Path('/sys/module/nvidia').exists() and (not directory.exists() or not any(directory.iterdir())):
        from installed_driver_candidate import observe_stock_boot
        authority['stockBootBefore']=observe_stock_boot(Path('/'),{'kernelVersion':os.uname().release,'nvidiaVersion':args.nvidia,
            'steamosVersion':dict(line.split('=',1) for line in tx.read(Path('/usr/lib/os-release'),65536).decode().splitlines()
                                 if '=' in line and not line.startswith('#'))['VERSION_ID'].strip('"')})
    tx.prepare(Path('/'),store,store/'plan.json',context={
        'bootImages':[],'target':{'kernelVersion':os.uname().release,'nvidiaVersion':args.nvidia,
                   'steamosVersion':(authority.get('stockBootBefore') or {}).get('steamosVersion')},
        'authority':authority,
        'readonlyBefore':readonly,'action':'guardian-update','profile':'console'})
    # Publishing engine registration is after durable intent and before files.
    register('app',store,frozen)
    try:
        if readonly: command(['/usr/bin/steamos-readonly','disable'],30)
        return tx.commit_prepared(Path('/'),store)
    except BaseException:
        tx.recover(Path('/'),store)
        raise
    finally:
        if readonly: command(['/usr/bin/steamos-readonly','enable'],30)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--revision',required=True)
    p.add_argument('--nvidia',required=True)
    p.add_argument('--interstitial',type=Path)
    p.add_argument('--interstitial-sha256')
    args=p.parse_args()
    print(json.dumps(build(args),sort_keys=True))


if __name__=='__main__':main()
