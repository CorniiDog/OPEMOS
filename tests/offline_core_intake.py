#!/usr/bin/env python3
"""Actual private CLI/Git bundle intake to the native OS refusal, no fake Git.

This Source CI transaction does NOT install or prove SteamOS guest acceptance.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
ENTRY=ROOT/'bootstrap/online_install.sh'

def run(argv,**kwargs):
    return subprocess.run(argv,capture_output=True,text=True,timeout=45,**kwargs)

with tempfile.TemporaryDirectory(prefix='offline-core-intake-') as tmp:
    base=Path(tmp);repo=base/'producer';repo.mkdir();(repo/'lib').mkdir()
    # Actual unchanged OS consumer, committed as real Git objects; no mocked
    # checkout, caller-supplied shell function or overridden method under test.
    shutil.copyfile(ROOT/'lib/common.sh',repo/'lib/common.sh')
    for argv in [['git','init',str(repo)],['git','-C',str(repo),'add','.'],
                 ['git','-C',str(repo),'-c','user.name=Source CI','-c','user.email=source-ci@example.invalid','commit','-m','actual common source']]:
        result=run(argv);assert result.returncode==0,(result.stdout,result.stderr)
    revision=run(['git','-C',str(repo),'rev-parse','HEAD']).stdout.strip()
    bundle=base/'core.bundle';result=run(['git','-C',str(repo),'bundle','create',str(bundle),'HEAD'])
    assert result.returncode==0,result.stderr
    bundle.chmod(0o600);payload=bundle.read_bytes();pin=hashlib.sha256(payload).hexdigest()
    home=base/'home';home.mkdir()
    env={**os.environ,'HOME':str(home),'SUPPORT_REPO':'CorniiDog/OPEMOS','SUPPORT_REVISION':revision,
         'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1',
         'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'protocol.allow','GIT_CONFIG_VALUE_0':'never'}
    for key in ['PROJECT_TEST_MODE','PROJECT_TEST_ROOT','OPEMOS_RECOVERY_PLAN_FILE','PENDING_CORE_PIN']:
        env.pop(key,None)
    args=['bash',str(ENTRY),'--local',str(base/'unused-raw.tar.gz'),'--local-sha256','0'*64,
          '--offline-core-bundle',str(bundle),'--offline-core-sha256',pin]
    def invoke(label,argv=None,extra_env=None,expected=None):
        result=run(argv or args,env={**env,**(extra_env or {})})
        assert result.returncode!=0,(label,result.stdout,result.stderr)
        if expected:assert expected in result.stderr,(label,result.stdout,result.stderr)
        cache=home/'.cache/open-gpu-kernel-modules-steamos-support'
        assert not cache.exists() or not list(cache.glob('online-install.*')),'owned temp not cleaned'
        return result
    if 'ID=steamos' not in Path('/etc/os-release').read_text():
        result=invoke('ordinary-no-network',expected='This operation is intended for SteamOS.')
        assert 'Restored the explicitly pinned Core checkout' in result.stderr
        assert 'github.com' not in result.stderr,'offline route attempted a network clone'
    else:
        print('Native SteamOS install is separately admitted; no Source CI install invoked.')
    wrong=args.copy();wrong[-1]='0'*64
    invoke('wrong-digest',wrong,expected='Offline Core bundle digest differs')
    invoke('wrong-revision',extra_env={'SUPPORT_REVISION':'f'*40},expected='reference is not a tree')
    invoke('missing-explicit-revision',extra_env={'SUPPORT_REVISION':''},expected='Offline Core requires')
    invoke('wrong-repository',extra_env={'SUPPORT_REPO':'unapproved/example'},expected='Offline Core requires')
    invoke('without-private-mode',['bash',str(ENTRY),*args[6:]],expected='Offline Core requires')
    bundle.write_bytes(payload+b'changed');invoke('tampered',expected='Offline Core bundle digest differs');bundle.write_bytes(payload)
    link=base/'second-link';os.link(bundle,link);invoke('hardlink',expected='Core checkout bundle is unsafe');link.unlink()
    saved=base/'saved';bundle.rename(saved);bundle.symlink_to(saved)
    invoke('symlink');bundle.unlink();saved.rename(bundle)
    bundle.chmod(0o666);invoke('writable',expected='Core checkout bundle is unsafe');bundle.chmod(0o600)
    bundle.write_bytes(b'not Git objects');bad=args.copy();bad[-1]=hashlib.sha256(bundle.read_bytes()).hexdigest()
    invoke('invalid-git',bad)
    assert not (home/'pending-install.json').exists()
print('Actual offline private Core Git intake and refusals passed; no native install acceptance.')
