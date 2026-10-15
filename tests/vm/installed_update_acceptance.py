#!/usr/bin/env python3
"""Actual installed updater transaction; separately admitted OMEN VM ONLY.

The external owning controller performs VM off/restart on the SAME disposable
image. This guest program never signals a process/VM, controls hardware, mocks
sysfs, substitutes producer functions, or selects a replacement trusted input.
Manifest/logs and the actual hypervisor grant/closure are separate evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
LIB=REPO/'lib'
sys.path.insert(0,str(LIB))
import installed_update_transaction as tx
from payload_receipt import verify_receipt_evidence
from installed_driver_candidate import reconcile_controller

TARGETS={
 '6.16.12-valve24.4-1-neptune-616-gfe145653a794',
 '6.16.12-valve24.5-1-neptune-616-gb2f7cfe85e45',
 '6.18.50-valve2-1-neptune-618-gc7289a96b14d'}
HOME=Path('/home/.steamos/open-gpu-kernel-modules-steamos-support/recovery')


def digest(path):
    digest=hashlib.sha256();total=0
    with path.open('rb') as stream:
        while True:
            part=stream.read(1024**2)
            if not part:break
            total+=len(part)
            assert total<=512*1024**2,'bounded test artifact input'
            digest.update(part)
    return digest.hexdigest()


def context(path):
    m=json.loads(path.read_bytes())
    required={'schemaVersion','runId','guestUUID','coreCommit','kernel','nvidia','archive',
              'archiveSHA256','checksum','checksumSHA256','provenance','provenanceSHA256','driverSourceCommit','cache','moduleSHA256','installedCoreSHA256','output'}
    assert set(m)==required and m['schemaVersion']==1
    assert re.fullmatch('[0-9a-f]{32}',m['runId'])
    assert m['kernel'] in TARGETS and os.uname().release==m['kernel']
    assert m['nvidia']=='575.64.05' and os.geteuid()==0
    # This catches accidental physical-host use; it is NOT an OMEN admission
    # receipt. The independent controller must still supply its actual grant.
    product=Path('/sys/class/dmi/id/product_name').read_text().lower()
    assert any(x in product for x in ('virtual machine','kvm','qemu','standard pc'))
    assert Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower()==m['guestUUID'].lower()
    actual=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],timeout=10).decode().strip()
    assert actual==m['coreCommit'] and re.fullmatch('[0-9a-f]{40}',actual)
    subprocess.run(['git','-C',str(REPO),'diff','--quiet','HEAD','--'],check=True,timeout=10)
    assert digest(Path(m['archive']))==m['archiveSHA256']
    assert digest(Path(m['checksum']))==m['checksumSHA256']
    assert digest(Path(m['provenance']))==m['provenanceSHA256']
    assert m['driverSourceCommit']=='4a616372ce3c1f1d0a10c04960107a1db51b6fc0'
    provenance=json.loads(Path(m['provenance']).read_bytes())
    assert provenance['source']['commit']==m['driverSourceCommit']
    assert provenance['target']['kernelVersion']==m['kernel'] and provenance['target']['nvidiaVersion']==m['nvidia']
    expected_sources={name for name in tx.GUARDIAN_FILES if name.startswith(('bootstrap/','lib/'))}
    assert set(m['installedCoreSHA256'])==expected_sources,'complete managed source inventory required'
    assert all(digest(REPO/name)==value for name,value in m['installedCoreSHA256'].items()),'source manifest differs from reviewed commit'
    assert set(m['moduleSHA256'])==tx.MODULES
    assert all(re.fullmatch('[0-9a-f]{64}',x) for x in m['moduleSHA256'].values())
    return m


def output(m):
    p=Path(m['output'])
    assert p.is_absolute() and p.resolve()==p and m['runId'] in p.name
    p.mkdir(mode=0o700,exist_ok=True)
    assert p.stat().st_uid==0 and p.stat().st_mode&0o077==0
    return p


def write(p,value):
    assert not p.exists(),'preserve prior evidence; never replay a completed action under its old label'
    tx.durable(p,value)


def observed_modules(m):
    prefix=Path('/usr/lib/modules')/m['kernel']/'updates/open-gpu-kernel-modules-steamos'
    return {name:digest(prefix/(name+'.ko.zst')) for name in m['moduleSHA256']}


def command(out,label,argv,env=None):
    # The actual command, PID/start, numeric exit and full raw output are kept.
    # Actual complete descendant/cgroup and QEMU closure comes from the owner.
    record=out/(label+'.command.json')
    log=out/(label+'.raw.log')
    assert not record.exists() and not log.exists()
    with log.open('xb') as stream:
        process=subprocess.Popen(argv,stdout=stream,stderr=subprocess.STDOUT,
                                 env=env,start_new_session=True)
        pid=process.pid
        try:start=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
        except FileNotFoundError:start=None  # A fast actual command may already exit.
        tx.durable(record,{'argv':argv,'pid':pid,'startTicks':start,'exit':None,
                           'bootId':tx.boot_id(),'environment':'admitted-OMEN-guest',
                           'hypervisorClosureVerifiedByThisProgram':False})
        try:
            numeric=process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            stream.flush();os.fsync(stream.fileno())
            tx.durable(record,{'argv':argv,'pid':pid,'startTicks':start,'exit':None,
                'timedOut':True,'processStillRunning':process.poll() is None,
                'rawLog':str(log),'bootId':tx.boot_id(),
                'disposition':'preserve failure; exact owning VM controller must inspect/close this job'})
            raise
        stream.flush();os.fsync(stream.fileno())
    tx.durable(record,{'argv':argv,'pid':pid,'startTicks':start,'exit':numeric,
                       'bootId':tx.boot_id(),'rawLog':str(log),'rawSHA256':digest(log),
                       'environment':'admitted-OMEN-guest','hypervisorClosureVerifiedByThisProgram':False})
    assert numeric==0,(argv,numeric,str(log))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('qualify','update','after-boot','verify-restored','corrupt-owned-module','verify-healed','arm-recovery-pause','remove-recovery-pause','refuse-bad-checksum','refuse-corrupt-cache','enable-console-fallback','verify-console-boot','disable-console-fallback','verify-readonly-status','verify-disabled-fallback-boot'))
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--repeat-observation',action='store_true')
    p.add_argument('--fault-component',choices=('app','driver'),default='driver')
    p.add_argument('--pause',choices=('prepared','activated:0','pending-health','restore:0','heal:0'))
    a=p.parse_args();m=context(a.manifest);out=output(m)
    if a.action=='qualify':
        receipt,docs=verify_receipt_evidence(Path('/'),allow_live_root=True)
        assert receipt['target']['kernelVersion']==m['kernel']
        loaded=tx.require_loaded_identity(Path('/'),m['kernel'],m['nvidia'])
        modules=observed_modules(m)
        assert modules!=m['moduleSHA256'],'positive update must exercise a real changed driver product'
        boot={x['filename']:digest(Path('/boot')/x['filename']) for x in docs['initramfsVerification']['images']}
        assert all(boot[x['filename']]==x['sha256'] for x in docs['initramfsVerification']['images']), 'qualified initial boot assets required'
        write(out/'baseline.json',{'bootId':tx.boot_id(),'loadedIdentity':loaded,'modules':modules,
            'bootImages':boot,'receipt':receipt,'manifestSHA256':digest(a.manifest),
            'GPUDisplayAcceptance':False})
    elif a.action=='update':
        baseline=json.loads((out/'baseline.json').read_bytes())
        assert baseline['manifestSHA256']==digest(a.manifest)
        env={**os.environ,'OPEMOS_PINNED_NVIDIA_VERSION':m['nvidia']}
        if a.pause:
            assert a.pause in ('prepared','activated:0','pending-health')
            env.update(PROJECT_TEST_MODE='1',OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE=a.pause)
        command(out,'guardian-install',['/bin/bash',str(REPO/'bootstrap/install_recovery_guardian.sh')],
                env if a.fault_component=='app' else None)
        argv=['/bin/bash',str(REPO/'bootstrap/install.sh'),'--archive',m['archive'],'--checksum',m['checksum'],'-y']
        if m['cache'] is not None:argv+=['--cache',m['cache']]
        command(out,'actual-update',argv,env if a.fault_component=='driver' else None)
        assert observed_modules(m)==m['moduleSHA256']
        state=reconcile_controller(Path('/'),HOME,inspect_only=True)
        assert state['state']=='pending-health'
        write(out/'update-result.json',{'state':state,'bootId':tx.boot_id(),'requiresExternalOwnedVMReboot':True,
              'manifestSHA256':digest(a.manifest),'compiledOrGPUVerifiedByThisProgram':False})
    elif a.action in ('after-boot','verify-disabled-fallback-boot'):
        baseline=json.loads((out/('baseline.json' if a.action=='after-boot' else 'disable-console-fallback.json')).read_bytes())
        assert tx.boot_id()!=baseline['bootId'],'actual new boot required'
        if a.action!='after-boot':out=out/'after-fallback';out.mkdir(mode=0o700)
        command(out,'ordinary-startup',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        command(out,'ordinary-health',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'])
        assert observed_modules(m)==m['moduleSHA256']
        assert reconcile_controller(Path('/'),HOME,inspect_only=True)['state']=='healthy'
        tx.require_loaded_identity(Path('/'),m['kernel'],m['nvidia'])
        for relative,expected in m['installedCoreSHA256'].items():
            assert relative.startswith(('bootstrap/','lib/')) and '..' not in Path(relative).parts
            assert digest(HOME/relative)==expected
        command(out,'idempotent-health',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'])
        write(out/'boot-health.json',{'bootId':tx.boot_id(),'kernel':os.uname().release,
              'filesAndLoadedIdentityVerified':True,'GPUDisplayAcceptance':False})
    elif a.action=='verify-restored':
        baseline=json.loads((out/'baseline.json').read_bytes())
        if a.repeat_observation:out=out/'repeat-restored';out.mkdir(mode=0o700)
        assert tx.boot_id()!=baseline['bootId']
        command(out,'restart-recovery',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        assert reconcile_controller(Path('/'),HOME,inspect_only=True)['state'] in ('restored','healthy','no-pending-intent')
        assert observed_modules(m)==baseline['modules']
        assert {name:digest(Path('/boot')/name) for name in baseline['bootImages']}==baseline['bootImages']
        command(out,'repeated-recovery',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        assert observed_modules(m)==baseline['modules']
        write(out/'restored.json',{'bootId':tx.boot_id(),'priorModuleAndBootHashesRestored':True,
                                 'GPUDisplayAcceptance':False})
    elif a.action=='corrupt-owned-module':
        assert (out/'boot-health.json').exists()
        path=Path('/usr/lib/modules')/m['kernel']/'updates/open-gpu-kernel-modules-steamos/nvidia-drm.ko.zst'
        assert digest(path)==m['moduleSHA256']['nvidia-drm']
        data=bytearray(path.read_bytes());data[len(data)//2]^=1
        from installed_driver_candidate import readonly_enabled,command as bounded_command
        readonly=readonly_enabled()
        try:
            if readonly:bounded_command(['/usr/bin/steamos-readonly','disable'],30)
            tx.replace_durable(path,bytes(data),0o644)
        finally:
            if readonly:bounded_command(['/usr/bin/steamos-readonly','enable'],30)
        write(out/'corruption.json',{'path':str(path),'corruptSHA256':digest(path),'bootId':tx.boot_id(),
              'nextAction':'external exclusive VM off/restart; do not kill updater process'})
    elif a.action=='verify-healed':
        old=json.loads((out/'corruption.json').read_bytes())
        if a.repeat_observation:out=out/'repeat-healed';out.mkdir(mode=0o700)
        assert tx.boot_id()!=old['bootId']
        command(out,'ordinary-self-heal',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        assert observed_modules(m)==m['moduleSHA256']
        pointer=json.loads((HOME/'installed-updates/current.json').read_bytes())
        store=HOME/'installed-updates'/pointer['generation']
        assert any(digest(file)==old['corruptSHA256'] for file in store.glob('foreign-*'))
        command(out,'repeated-self-heal',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        write(out/'healed.json',{'bootId':tx.boot_id(),'corruptionEvidenceRetained':True,
                               'exactOwnedGenerationRestored':True,'GPUDisplayAcceptance':False})
    elif a.action=='verify-readonly-status':
        # Real other-UID process consumes the ordinary installed status entry;
        # no sudo credentials, private intent access or fake adapter.
        command(out,'publish-root-status',[str(HOME/'bootstrap/recoveryctl.sh'),'status','--json'])
        command(out,'actual-unprivileged-status',['/usr/sbin/runuser','-u','nobody','--',
            str(HOME/'bootstrap/recoveryctl.sh'),'status','--json'])
        value=json.loads((out/'actual-unprivileged-status.raw.log').read_bytes())
        assert value['target']['kernelVersion']==m['kernel']
        assert value['moduleVerification']['status']=='verified'
        write(out/'readonly-status.json',{'actualOtherUID':True,'credentialGrantCreated':False})
    elif a.action in ('enable-console-fallback','disable-console-fallback'):
        assert (out/'boot-health.json').exists(),'qualify the exact installed generation first'
        argv=[str(HOME/'bootstrap/recoveryctl.sh'),
              'enable-fallback' if a.action=='enable-console-fallback' else 'disable-fallback','-y']
        if a.action=='enable-console-fallback':argv+=['--profile','console']
        command(out,a.action,argv)
        assert reconcile_controller(Path('/'),HOME,inspect_only=True)['state']=='pending-health'
        write(out/(a.action+'.json'),{'bootId':tx.boot_id(),'externalSameOverlayRebootRequired':True})
    elif a.action=='verify-console-boot':
        prior=json.loads((out/'enable-console-fallback.json').read_bytes())
        assert tx.boot_id()!=prior['bootId'] and not Path('/sys/module/nvidia').exists()
        command(out,'fallback-startup',['/usr/bin/systemctl','start','opemos-nvidia-guardian.service'])
        command(out,'fallback-health',[str(HOME/'bootstrap/recoveryctl.sh'),'installed-health','--json'])
        state=reconcile_controller(Path('/'),HOME,inspect_only=True)
        assert state['state']=='healthy' and state['currentRuntimeMatches']
        write(out/'console-boot.json',{'bootId':tx.boot_id(),'actualConsoleProfileHealth':True,
                                     'GPUDisplayAcceptance':False})
    elif a.action in ('refuse-bad-checksum','refuse-corrupt-cache','enable-console-fallback','verify-console-boot','disable-console-fallback','verify-readonly-status'):
        # Ordinary real producer refusal; only exact test-created inputs change.
        before=observed_modules(m)
        argv=['/bin/bash',str(REPO/'bootstrap/install.sh'),'--archive',m['archive'],'-y']
        if a.action=='refuse-bad-checksum':
            checksum=out/'bad-checksum.sha256'
            assert not checksum.exists()
            tx.replace_durable(checksum,('0'*64+'  archive.tar.gz\n').encode())
            argv+=['--checksum',str(checksum)]
        else:
            assert m['cache'] is not None
            from recovery_cached_product import MAX_METADATA
            import shutil
            cache=out/'corrupt-cache'
            assert not cache.exists()
            assert os.statvfs(out).f_bavail*os.statvfs(out).f_frsize>1024**3
            shutil.copytree(m['cache'],cache,symlinks=False)
            metadata=cache/'materialization.json'
            metadata.write_bytes(b' '* (MAX_METADATA+1))
            argv+=['--checksum',m['checksum'],'--cache',str(cache)]
        raw=out/(a.action+'.raw.log')
        assert not raw.exists()
        with raw.open('xb') as log:
            result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,timeout=300)
            log.flush();os.fsync(log.fileno())
        assert result.returncode!=0 and observed_modules(m)==before
        write(out/(a.action+'.json'),{'argv':argv,'exit':result.returncode,'rawLog':str(raw),
            'rawSHA256':digest(raw),'actualModuleFilesUnchanged':True})
    else:
        drop=Path('/etc/systemd/system/opemos-nvidia-guardian.service.d/90-opemos-acceptance-'+m['runId']+'.conf')
        if a.action=='arm-recovery-pause':
            assert a.pause in ('restore:0','heal:0')
            from installed_driver_candidate import readonly_enabled,command as bounded_command
            readonly=readonly_enabled()
            try:
                if readonly:bounded_command(['/usr/bin/steamos-readonly','disable'],30)
                drop.parent.mkdir(mode=0o755,exist_ok=True)
                assert not drop.exists()
                data=('[Service]\nEnvironment=PROJECT_TEST_MODE=1\nEnvironment=OPEMOS_INSTALLED_TRANSACTION_TEST_PAUSE='+a.pause+'\n').encode()
                tx.replace_durable(drop,data,0o644)
            finally:
                if readonly:bounded_command(['/usr/bin/steamos-readonly','enable'],30)
            write(out/'pause-ownership.json',{'path':str(drop),'sha256':digest(drop),'runId':m['runId']})
        else:
            proof=json.loads((out/'pause-ownership.json').read_bytes())
            assert str(drop)==proof['path'] and digest(drop)==proof['sha256']
            from installed_driver_candidate import readonly_enabled,command as bounded_command
            readonly=readonly_enabled()
            try:
                if readonly:bounded_command(['/usr/bin/steamos-readonly','disable'],30)
                drop.unlink();tx.fsync_dir(drop.parent)
            finally:
                if readonly:bounded_command(['/usr/bin/steamos-readonly','enable'],30)  # exact test-created file only
        subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=30)


if __name__=='__main__':main()
