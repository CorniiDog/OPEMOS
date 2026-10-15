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
    assert set(m)=={'schemaVersion','runId','guestUUID','coreCommit','kernel','steamos','nvidia',
                    'profile','producer','installerUID','driverSourceCommit','interstitial','interstitialSHA256','output'}
    assert m['schemaVersion']==1 and re.fullmatch('[0-9a-f]{32}',m['runId'])
    assert os.geteuid()==0 and os.uname().release==m['kernel'] and m['kernel'] in TARGETS
    assert m['nvidia']=='575.64.05' and m['driverSourceCommit']=='4a616372ce3c1f1d0a10c04960107a1db51b6fc0'
    assert type(m['installerUID']) is int and m['installerUID'] in (0,1000)
    assert m['profile'] in ('fresh','loaded') and m['producer'] in ('built','published')
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('virtual machine','kvm','qemu','standard pc'))
    assert Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower()==m['guestUUID'].lower()
    assert subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],timeout=10).decode().strip()==m['coreCommit']
    assert re.fullmatch('[0-9a-f]{40}',m['coreCommit'])
    subprocess.run(['git','-C',str(REPO),'diff','--quiet','HEAD','--'],check=True,timeout=10)
    assert file_digest(Path(m['interstitial']))==m['interstitialSHA256']
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
    if m['producer']=='built':assert provenance['trust']=='locally-built-verified'
    else:assert provenance['trust']=='certified-published','do not substitute a local build for published-path acceptance'
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
                                    'refuse-custody-schema','refuse-custody-size','refuse-custody-target','ignore-latest-pointer'))
    p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args();m,out=context(a.manifest)
    argv,env=online(m)
    if a.action=='start':
        assert not (HOME/'pending-install.json').exists(),'do not rearm an existing job'
        if m['profile']=='fresh':
            assert not Path('/sys/module/nvidia').exists()
            assert not (HOME/'installed-updates/current.json').exists()
        else:
            from installed_driver_candidate import reconcile_controller
            old=reconcile_controller(Path('/'),HOME,inspect_only=True)
            assert old.get('state')=='healthy' and old.get('currentRuntimeMatches') is True
        # Actual online release discovery/build + guardian pending75 + EXIT trap.
        # No caller-supplied materialization/cache shortcut.
        command(out,'ordinary-producer',argv,env,expected=(75,))
        record,document,directory=custody(m)
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
