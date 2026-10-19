#!/usr/bin/env python3
"""Ordinary online producer -> retained inputs -> two actual boots, OMEN ONLY.

No fake adapters, preseeded product cache, replaced methods or process-kill boot
substitutes. The separate owning controller admits the VM/network/build resources,
performs real reboot/abrupt-off on its same disposable overlay and records closure.
This file neither grants that authority nor executes on the physical host.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'lib'))
import installed_update_transaction as tx
from recovery_cached_product import HOME,retained_install,read_document,load,digest,canonical
from installed_update_acceptance import command,write,digest as file_digest,TARGETS


def context(path):
    m=json.loads(path.read_bytes())
    required={'schemaVersion','runId','guestUUID','coreCommit','kernel','steamos','nvidia',
                    'profile','producer','installerUID','driverSourceCommit','interstitial','interstitialSHA256','output'}
    if m.get('producer')=='private':
        required|={'privateInputs'}
        if 'userspaceInputs' in m:required|={'userspaceInputs'}
        if 'offlineCore' in m:required|={'offlineCore'}
    assert set(m)==required
    assert m['schemaVersion']==1 and re.fullmatch('[0-9a-f]{32}',m['runId'])
    assert os.geteuid()==0 and os.uname().release==m['kernel'] and m['kernel'] in TARGETS
    assert m['nvidia']=='575.64.05' and m['driverSourceCommit']=='4a616372ce3c1f1d0a10c04960107a1db51b6fc0'
    assert type(m['installerUID']) is int and m['installerUID'] in (0,1000)
    assert m['profile'] in ('fresh','loaded') and m['producer'] in ('built','published','private')
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('virtual machine','kvm','qemu','standard pc'))
    assert Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower()==m['guestUUID'].lower()
    assert subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],timeout=10).decode().strip()==m['coreCommit']
    assert re.fullmatch('[0-9a-f]{40}',m['coreCommit'])
    subprocess.run(['git','-C',str(REPO),'diff','--quiet','HEAD','--'],check=True,timeout=10)
    assert file_digest(Path(m['interstitial']))==m['interstitialSHA256']
    if m['producer']=='private':
        if 'offlineCore' in m:
            offline=m['offlineCore']
            assert set(offline)=={'path','sha256'}
            assert re.fullmatch('[0-9a-f]{64}',offline['sha256'])
            assert file_digest(Path(offline['path']))==offline['sha256']
        if 'userspaceInputs' in m:
            userspace=m['userspaceInputs']
            assert set(userspace)=={'path','sha256'}
            assert re.fullmatch('[0-9a-f]{64}',userspace['sha256'])
            assert file_digest(Path(userspace['path']))==userspace['sha256']
            # This input pin grants no package trust; the ordinary installer
            # still runs the guest keyring and native package checks.
        entries=m['privateInputs']
        assert set(entries)=={'archive','checksum','provenance','buildInfo'}
        for row in entries.values():
            assert set(row)=={'path','bytes','sha256'}
            path=Path(row['path']);assert path.is_file() and not path.is_symlink()
            assert path.stat().st_size==row['bytes'] and file_digest(path)==row['sha256']
        archive=entries['archive']['path']
        assert entries['checksum']['path']==archive+'.sha256'
        assert entries['provenance']['path']==archive.removesuffix('.tar.gz')+'.provenance.json'
        assert entries['buildInfo']['path']==archive.removesuffix('.tar.gz')+'.build-info.txt'
        provenance=json.loads(Path(entries['provenance']['path']).read_bytes())
        assert provenance['source']['commit']==m['driverSourceCommit']
        assert provenance['target']=={'steamosVersion':m['steamos'],'kernelVersion':m['kernel'],
                                      'nvidiaVersion':m['nvidia'],'architecture':'x86_64'}
        assert provenance['artifact'].get('representation','raw')!='ko.zst','use authenticated original raw producer input, no old-Core relabel'

    out=Path(m['output']);assert out.is_absolute() and out.resolve()==out and m['runId'] in out.name
    out.mkdir(mode=0o700,exist_ok=True)
    assert out.stat().st_uid==0 and out.stat().st_mode&0o077==0
    return m,out


def expected(m):
    return {'steamos':m['steamos'],'kernel':m['kernel'],'nvidia':m['nvidia'],'revision':m['coreCommit']}


def custody(m):
    record=retained_install(HOME,expected(m));assert record is not None
    directory=HOME/'cached-products'/record['product']
    document,outputs,_=load(directory,expected(m))
    provenance=json.loads((directory/outputs['provenance']['name']).read_bytes())
    assert provenance['source']['commit']==m['driverSourceCommit']
    if m['producer'] in ('built','private'):assert provenance['trust']=='locally-built-verified'
    if m['producer']=='private':
        assert provenance['repack']['sourceArchiveSha256']==m['privateInputs']['archive']['sha256']
    if m['producer']=='published':assert provenance['trust']=='certified-published','do not substitute a local build for published-path acceptance'
    return record,document,directory


def app_intent():
    from install_guardian_generation import registered
    from installed_boot_guard import engine
    tool,store=engine(registered()['app'])
    return tool.load_intent(Path('/'),store)


def online(m):
    argv=['/bin/bash',str(REPO/'bootstrap/online_install.sh'),'-y',
          '--interstitial-binary',m['interstitial'],'--interstitial-sha256',m['interstitialSHA256']]
    if m['producer']=='built':argv+=['--build-as-fallback']
    if m['producer']=='private':
        argv+=['--local',m['privateInputs']['archive']['path'],'--local-sha256',m['privateInputs']['archive']['sha256']]
        if 'offlineCore' in m:
            argv+=['--offline-core-bundle',m['offlineCore']['path'],
                   '--offline-core-sha256',m['offlineCore']['sha256']]
        if 'userspaceInputs' in m:
            argv+=['--userspace-inputs',m['userspaceInputs']['path'],
                   '--userspace-inputs-sha256',m['userspaceInputs']['sha256']]
    if m['installerUID']!=0:
        import pwd
        owner=pwd.getpwuid(m['installerUID'])
        # Existing admitted guest account/sudo route only; no credential or
        # policy mutation. A real permission failure is retained, not mocked.
        argv=['/usr/bin/sudo','-n','-H','-u',owner.pw_name,'--',*argv]
    # The exact GitHub source must first become available under its own source
    # push/review policy; the test does not invent a local clone transport.
    env={**os.environ,'SUPPORT_REPO':'CorniiDog/OPEMOS','SUPPORT_REVISION':m['coreCommit'],
         'OPEMOS_PINNED_NVIDIA_VERSION':m['nvidia']}
    for key in ('PENDING_CORE_PIN','SUPPORT_BRANCH','BUILD_AS_FALLBACK','PROJECT_TEST_MODE','PROJECT_TEST_ROOT','OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE',
                'PROJECT_TEST_CACHED_PRODUCT','OPEMOS_RECOVERY_PLAN_FILE','BUILD_OUT'):
        env.pop(key,None)
    return argv,env


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('start','repeat-pending','app-health','resume-online','resume-auto',
                                    'driver-health','repeat-healthy','refuse-other-uid','refuse-custody-mode',
                                    'refuse-custody-schema','refuse-custody-size','refuse-custody-target','ignore-latest-pointer','private-refuse-checksum','private-refuse-zip-entry','private-refuse-target','private-refuse-signature'))
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--wrong-target-archive',type=Path)
    p.add_argument('--wrong-target-sha256')
    a=p.parse_args();m,out=context(a.manifest)
    argv,env=online(m)
    if a.action in ('private-refuse-checksum','private-refuse-zip-entry','private-refuse-target'):
        assert m['producer']=='private' and m['installerUID']==0,'controlled private fault operands are Root-private'
        assert not (HOME/'pending-install.json').exists()
        before=subprocess.check_output(['pacman','-Q'])
        registry=HOME.parent/'recovery-bootstrap/current.json'
        registry_before=registry.read_bytes() if registry.exists() else None
        root=out/a.action;root.mkdir(mode=0o700)
        import shutil
        for row in m['privateInputs'].values():
            shutil.copyfile(row['path'],root/Path(row['path']).name)
        archive=root/Path(m['privateInputs']['archive']['path']).name
        test_argv=list(argv)
        if a.action=='private-refuse-checksum':
            (root/(archive.name+'.sha256')).write_text('0'*64+'  '+archive.name+'\n')
            test_argv[test_argv.index('--local')+1]=str(archive)
        elif a.action=='private-refuse-zip-entry':
            import zipfile
            path=root/'unsafe.zip'
            outside=out/'zip-must-not-change';outside.write_text('preserve')
            with zipfile.ZipFile(path,'x') as z:
                for row in m['privateInputs'].values():z.write(row['path'],Path(row['path']).name)
                z.writestr('../zip-must-not-change','unexpected')
            test_argv[test_argv.index('--local')+1]=str(path)
            test_argv[test_argv.index('--local-sha256')+1]=file_digest(path)
        else:
            # A real separately authenticated other-target artifact through
            # the ordinary entry. Never override uname/get_kernel/validator.
            assert a.wrong_target_archive is not None and a.wrong_target_sha256 is not None
            assert file_digest(a.wrong_target_archive)==a.wrong_target_sha256
            provenance=json.loads(a.wrong_target_archive.with_name(a.wrong_target_archive.name.removesuffix('.tar.gz')+'.provenance.json').read_bytes())
            assert provenance['target']['kernelVersion']!=m['kernel']
            test_argv[test_argv.index('--local')+1]=str(a.wrong_target_archive)
            test_argv[test_argv.index('--local-sha256')+1]=a.wrong_target_sha256
        command(out,a.action,test_argv,env,expected=(1,))
        log=(out/(a.action+'.raw.log')).read_text()
        expected_reason={'private-refuse-checksum':'private product authentication failed',
                         'private-refuse-zip-entry':'Private zip must contain exactly four',
                         'private-refuse-target':'private product must match the actual exact'}[a.action]
        assert expected_reason in log,'do not count an unrelated permission/transport failure as target refusal'
        assert subprocess.check_output(['pacman','-Q'])==before,'private refusal must precede package effects'
        assert (registry.read_bytes() if registry.exists() else None)==registry_before
        assert not (HOME/'pending-install.json').exists()
        if a.action=='private-refuse-zip-entry':assert outside.read_text()=='preserve'
    elif a.action=='private-refuse-signature':
        assert m['producer']=='private'
        record,_,_=custody(m);assert record['userspaceInput'] is not None
        from recovery_cached_product import verify_userspace_inputs
        manifest=verify_userspace_inputs(HOME,record['userspaceInput'],m['nvidia'])
        src=HOME/'userspace-inputs'/record['userspaceInput']
        name=next(n for n in manifest['files'] if n.endswith('.pkg.tar.zst'))
        import shutil
        bad=out/'invalid-signature';bad.mkdir(mode=0o700)
        shutil.copyfile(src/name,bad/name)
        data=bytearray((src/(name+'.sig')).read_bytes());data[-1]^=1
        (bad/(name+'.sig')).write_bytes(data)
        before=subprocess.check_output(['pacman','-Q'])
        command(out,'private-good-signature-control',['env','LC_ALL=C','pacman-key','--verify',str(src/(name+'.sig')),str(src/name)])
        command(out,a.action,['env','LC_ALL=C','pacman-key','--verify',str(bad/(name+'.sig')),str(bad/name)],expected=(1,))
        assert subprocess.check_output(['pacman','-Q'])==before
        assert custody(m)[0]==record
    elif a.action=='start':
        assert not (HOME/'pending-install.json').exists(),'do not rearm an existing job'
        if m['profile']=='fresh':
            assert not Path('/sys/module/nvidia').exists()
            assert not (HOME/'installed-updates/current.json').exists()
            if m['producer']=='private':
                for package in ('nvidia-utils','lib32-nvidia-utils'):
                    absent=subprocess.run(['pacman','-Q',package],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    assert absent.returncode==1,'no preinstalled-userspace shortcut'
                write(out/'private-absent-prestate.json',{'bootId':tx.boot_id(),'packageQueryExit':1,
                    'noNvidiaModuleLoaded':True,'noDriverGeneration':True})
        else:
            from installed_driver_candidate import reconcile_controller
            old=reconcile_controller(Path('/'),HOME,inspect_only=True)
            assert old.get('state')=='healthy' and old.get('currentRuntimeMatches') is True
        # Actual online release discovery/build + guardian pending75 + EXIT trap.
        # No caller-supplied materialization/cache shortcut.
        command(out,'ordinary-producer',argv,env,expected=(75,))
        record,document,directory=custody(m)
        if m['producer']=='private':
            assert record['userspaceIntegrityRequired'] is True
            if m['profile']=='fresh':assert record['userspaceInput'] is not None
            assert all(v is not None and v.rsplit('-',1)[0]==m['nvidia'] for v in record['userspace'].values())
            from recovery_cached_product import verify_userspace_inputs
            inputs=verify_userspace_inputs(HOME,record['userspaceInput'],m['nvidia']) if record['userspaceInput'] is not None else None
            if inputs is not None:assert sum(n.endswith('.sig') for n in inputs['files'])==2
            write(out/'private-userspace-retained.json',{'bootId':tx.boot_id(),'userspace':record['userspace'],
                  'inputIdentity':record['userspaceInput'],'manifest':inputs,'product':record['product']})
        app=app_intent()
        assert app['phase']=='pending-health' and app['authority']['installProduct']==record['product']
        assert app['preparedBootId']==tx.boot_id()
        if m['profile']=='fresh':
            assert app['authority']['stockBootBefore']['NVIDIAHealthVerified'] is False
        write(out/'producer-retained.json',{'bootId':tx.boot_id(),'record':record,'materialization':document,
              'productDirectory':str(directory),'originalTemporaryProducerHasExited':True,
              'manifestSHA256':file_digest(a.manifest),'runtimeAuthorizedByThisProgram':False})
    elif a.action=='repeat-pending':
        previous=json.loads((out/'producer-retained.json').read_bytes())
        assert tx.boot_id()==previous['bootId']
        before=canonical(custody(m)[0])
        command(out,'same-boot-ordinary-repeat',argv,env,expected=(75,))
        assert canonical(custody(m)[0])==before
        assert app_intent()['phase']=='pending-health'
        command(out,'same-boot-health-refusal',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'],expected=(1,))
        assert canonical(custody(m)[0])==before
    elif a.action=='app-health':
        previous=json.loads((out/'producer-retained.json').read_bytes())
        assert tx.boot_id()!=previous['bootId'],'actual app boot required'
        command(out,'actual-app-startup',['systemctl','start','opemos-nvidia-guardian.service'])
        # Loaded prior driver is still healthy, not completion of pending product.
        exits=(0,) if m['profile']=='fresh' else (75,)
        command(out,'actual-app-ack',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'],expected=exits)
        record,_,_=custody(m)
        assert app_intent()['phase']=='healthy' and app_intent()['authority']['installProduct']==record['product']
        assert record==previous['record'],'boot acknowledgement must not change selected inputs'
        write(out/'app-ack.json',{'bootId':tx.boot_id(),'product':record['product']})
    elif a.action in ('resume-online','resume-auto'):
        previous=json.loads((out/'app-ack.json').read_bytes());record,_,_=custody(m)
        assert record['product']==previous['product']
        # Run ONE selected ordinary resume route on each independent admitted
        # overlay, not both against the same pending activation.
        if a.action=='resume-online':command(out,'ordinary-resume',argv,env)
        else:command(out,'ordinary-resume',[str(HOME/'bootstrap/recoveryctl.sh'),'repair-auto','--json'],expected=(75,))
        from install_guardian_generation import registered
        from installed_boot_guard import engine
        tool,store=engine(registered()['driver']);intent=tool.load_intent(Path('/'),store)
        assert intent['phase']=='pending-health' and intent['authority']['installProduct']==previous['product']
        assert custody(m)[0]==record
        write(out/'driver-activation.json',{'bootId':tx.boot_id(),'product':record['product'],'generation':str(store)})
    elif a.action=='driver-health':
        previous=json.loads((out/'driver-activation.json').read_bytes())
        assert tx.boot_id()!=previous['bootId'],'actual second new boot required'
        command(out,'actual-driver-startup',['systemctl','start','opemos-nvidia-guardian.service'])
        command(out,'actual-driver-health',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'])
        assert not (HOME/'pending-install.json').exists()
        completed=read_document(HOME/('completed-install-'+previous['product']+'.json'))
        assert completed==json.loads((out/'producer-retained.json').read_bytes())['record']
        from installed_driver_candidate import reconcile_controller
        observed=reconcile_controller(Path('/'),HOME,inspect_only=True)
        assert observed['state']=='healthy' and observed['currentRuntimeMatches'] is True
        tx.require_loaded_identity(Path('/'),m['kernel'],m['nvidia'])
        write(out/'driver-health.json',{'bootId':tx.boot_id(),'actualFiveModuleHealth':True,
                                       'GPUFunctionalityVerified':False,'product':previous['product']})
    elif a.action=='repeat-healthy':
        previous=json.loads((out/'driver-health.json').read_bytes())
        # Same ordinary command may discover a DIFFERENT published product; use
        # exact pending/completed cache identity check to distinguish a new real
        # update from idempotence. Do not label a changed release a repeat pass.
        command(out,'ordinary-healthy-repeat',argv,env)
        assert not (HOME/'pending-install.json').exists(),'a changed product is a new transaction, not repeat acceptance'
        assert (HOME/('completed-install-'+previous['product']+'.json')).exists()
    elif a.action=='refuse-other-uid':
        record,_,directory=custody(m);before=canonical(record)
        command(out,'actual-other-uid',['setpriv','--reuid=65534','--regid=65534','--clear-groups',
                '/usr/bin/python3',str(REPO/'lib/recovery_cached_product.py'),'pending','--home',str(HOME),
                '--steamos',m['steamos'],'--kernel',m['kernel'],'--nvidia',m['nvidia'],
                '--support-revision',m['coreCommit']],expected=(1,))
        assert canonical(custody(m)[0])==before
    elif a.action in ('refuse-custody-schema','refuse-custody-size','refuse-custody-target','ignore-latest-pointer'):
        record,_,_=custody(m)
        path=HOME/('cached-current.json' if a.action=='ignore-latest-pointer' else 'pending-install.json')
        original=path.read_bytes()
        changed=json.loads(original)
        if a.action=='refuse-custody-schema':changed['unexpected']=True
        elif a.action=='refuse-custody-target':changed['expected']['kernel']='wrong-kernel'
        elif a.action=='ignore-latest-pointer':changed={'product':'f'*64}
        else:changed['excessive']='x'*(128*1024)
        payload=canonical(changed)
        failure=out/(a.action+'.injected-input.json');assert not failure.exists()
        tx.replace_durable(failure,payload,0o600)
        try:
            tx.replace_durable(path,payload,0o600)
            injected=path.stat()
            command(out,a.action,['/usr/bin/python3',str(REPO/'lib/recovery_cached_product.py'),
                    'pending','--home',str(HOME),'--steamos',m['steamos'],'--kernel',m['kernel'],
                    '--nvidia',m['nvidia'],'--support-revision',m['coreCommit']],
                    expected=(0,) if a.action=='ignore-latest-pointer' else (1,))
            if a.action=='ignore-latest-pointer':assert custody(m)[0]==record
        finally:
            assert path.stat().st_ino==injected.st_ino and path.read_bytes()==payload,'unknown concurrent change; no restore authority'
            tx.replace_durable(path,original,0o600)
        assert custody(m)[0]==record
    elif a.action=='refuse-custody-mode':
        record,_,_=custody(m);path=HOME/'install-inputs'/record['product']/'opemos-interstitial'
        original=path.stat();assert original.st_mode&0o777==0o600
        try:
            path.chmod(0o644)
            command(out,'actual-custody-mode-refusal',['/usr/bin/python3',str(REPO/'lib/recovery_cached_product.py'),
                    'pending','--home',str(HOME),'--steamos',m['steamos'],'--kernel',m['kernel'],
                    '--nvidia',m['nvidia'],'--support-revision',m['coreCommit']],expected=(1,))
        finally:
            assert path.stat().st_ino==original.st_ino,'only exact test-owned inode may be restored'
            path.chmod(0o600)
        assert custody(m)[0]==record


if __name__=='__main__':main()
