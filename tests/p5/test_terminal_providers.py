"""A shared CLI must launch the right program, profile, native MCP and permission path."""
import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from app import terminal_providers
from app.terminal import BRIDGE_HEADER
from .test_claude_terminal import native, stop


def codex_account(c):
    acc=c.post('/api/accounts',json={'provider':'codex','label':'Test Codex'}).json()
    binary=c.data_dir/'fake-codex.exe'; binary.write_bytes(b'')
    c.app.state.terminals.commands['codex']=str(binary)
    return acc


def launch(c,provider,account=None,**kwargs):
    response=c.post(f'/api/projects/{c.pid}/terminal',json={
        'provider':provider,'account_id':account['id'] if account else None,**kwargs})
    assert response.status_code==200,response.text
    return response.json()


def test_codex_uses_its_own_account_and_native_mcp_config(native,monkeypatch):
    c=native; acc=codex_account(c)
    cfg=c.data_dir/'profiles'/acc['id']
    original='model = "native-user-choice"\n'
    (cfg/'config.toml').write_text(original,encoding='utf-8')
    monkeypatch.setenv('OPENAI_API_KEY','unrelated-api-key')
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN','unrelated-oauth')
    session=launch(c,'codex',acc,fresh=True)
    pty=c.ptys[-1]; env=pty.options['env']; argv=pty.argv
    assert session['provider']=='codex' and session['label']=='Codex'
    assert env['CODEX_HOME']==str(cfg) and 'OPENAI_API_KEY' not in env and 'CLAUDE_CODE_OAUTH_TOKEN' not in env
    assert '--no-daemon' in argv and '--session-id' not in argv and '--settings' not in argv
    assert 'exec' not in argv and 'app-server' not in argv
    assert not any('bypass' in arg for arg in argv)
    override=tomllib.loads(argv[argv.index('-c')+1])['mcp_servers']['oneproduction']
    assert override['env_vars']==['ONEPROD_BRIDGE_KEY']
    assert env['ONEPROD_BRIDGE_KEY'] not in ' '.join(argv)
    assert override['env']['OPENAI_API_KEY']==''
    assert (cfg/'config.toml').read_text(encoding='utf-8')==original
    stop(c,session)


def test_codex_resume_uses_its_native_picker_and_provider_preferences_are_separate(native):
    c=native; acc=codex_account(c)
    claude=launch(c,'claude',c.acc)
    stop(c,claude)
    codex=launch(c,'codex',acc,fresh=False)
    assert 'resume' in c.ptys[-1].argv
    assert not codex['conversation_id']
    stop(c,codex)
    saved=json.loads((c.files.parent/'terminal.json').read_text(encoding='utf-8'))
    assert saved['providers']['claude']['account_id']==c.acc['id']
    assert saved['providers']['codex']['account_id']==acc['id']
    assert all(key.startswith('claude:') for key in saved['conversations'])


def test_codex_cannot_use_a_claude_account_or_another_profiles_setup(native,tmp_path):
    c=native; acc=codex_account(c)
    url=f'/api/projects/{c.pid}/terminal'
    assert c.post(url,json={'provider':'codex','account_id':c.acc['id']}).status_code==400
    other=tmp_path/'other-profile'; other.mkdir()
    assert c.post(url,json={'provider':'codex','account_id':acc['id'],'config_dir':str(other)}).status_code==400
    assert c.post(url,json={'provider':'unknown'}).status_code==400
    assert c.ptys==[]


def test_plain_shell_needs_no_account_and_has_no_injected_studio_credentials(native):
    c=native
    session=launch(c,'shell')
    env=c.ptys[-1].options['env']
    assert session['provider']=='shell' and session['account_id'] is None
    assert 'ONEPROD_BRIDGE_KEY' not in env
    assert '--mcp-config' not in c.ptys[-1].argv and '-NoProfile' not in c.ptys[-1].argv
    assert c.ptys[-1].options['cwd']==str(c.files)
    stop(c,session)


def test_neutral_endpoint_defaults_to_a_plain_cli(native):
    c=native
    response=c.post(f'/api/projects/{c.pid}/terminal',json={})
    assert response.status_code==200,response.text
    assert response.json()['provider']=='shell'
    stop(c,response.json())


def test_neutral_discovery_lists_matching_accounts_and_legacy_route_still_works(native):
    c=native; acc=codex_account(c)
    state=c.get(f'/api/projects/{c.pid}/terminal').json()
    assert {p['id'] for p in state['providers']}=={'shell','claude','codex'}
    assert {a['engine'] for a in state['accounts']}=={'claude','codex'}
    legacy=c.get(f'/api/projects/{c.pid}/claude-code').json()
    assert {a['engine'] for a in legacy['accounts']}=={'claude'}
    assert legacy['other_account_count']==1
    for path in ('/terminal.js','/terminal.css','/claude-code.js'):
        assert c.get(path).status_code==200


def test_codex_bridge_approval_is_once_per_exact_operation(native):
    c=native; acc=codex_account(c); session=launch(c,'codex',acc,fresh=True)
    url=f"/claude-code-bridge/{session['id']}"
    headers={BRIDGE_HEADER:c.ptys[-1].options['env']['ONEPROD_BRIDGE_KEY']}
    payload={'name':'source_call','arguments':{'source':'test','tool':'read'}}
    permission=c.post(url+'/permission',json=payload,headers=headers).json()
    assert permission['required'] and permission['mode']=='elicitation'
    assert c.post(url+'/call',json=payload,headers=headers).status_code==403
    assert c.post(url+'/policy',json={'tool_name':'source_call'},headers=headers).status_code==403
    assert c.post(url+'/approval',json=payload,headers=headers).status_code==200
    run=c.app.state.terminals.live[c.pid]
    assert not run.studio_request('source_call',{'different':'operation'})
    assert run.studio_request('source_call',payload['arguments'])
    assert not run.studio_request('source_call',payload['arguments'])
    stop(c,session)


def test_legacy_settings_migrate_without_overwriting_the_old_file(native):
    c=native
    old=c.files.parent/'claude-code.json'
    original=json.dumps({'account_id':c.acc['id'],'cwd':str(c.files),'custom':'keep'})
    old.write_text(original,encoding='utf-8')
    assert c.app.state.terminals.preferences(c.pid)['custom']=='keep'
    session=launch(c,'claude',c.acc)
    assert (c.files.parent/'terminal.json').is_file()
    assert old.read_text(encoding='utf-8')==original
    stop(c,session)


def test_installed_codex_accepts_the_mcp_override_without_a_login(tmp_path):
    argv=terminal_providers.command('codex')
    if not argv:
        pytest.skip('Codex not installed')
    cfg=tmp_path/'codex-home'; cfg.mkdir()
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX','OPENAI'))}
    env['CODEX_HOME']=str(cfg)
    entry={'command':'python','args':['bridge.py'],'env':{'X':'local'},'env_vars':['ONEPROD_BRIDGE_KEY']}
    result=subprocess.run([*argv,'--no-daemon','-c','mcp_servers.oneproduction='+terminal_providers.toml_value(entry),'--help'],
                          cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert result.returncode==0,result.stderr
    assert '--no-daemon' in result.stdout
    config=subprocess.run([*argv,'-c','mcp_servers.oneproduction='+terminal_providers.toml_value(entry),
                           'mcp','get','oneproduction','--json'],cwd=tmp_path,env=env,capture_output=True,
                          text=True,encoding='utf-8',errors='replace',timeout=20)
    assert config.returncode==0,config.stderr
    assert 'ONEPROD_BRIDGE_KEY' in config.stdout and 'bridge.py' in config.stdout


def test_npm_codex_launcher_selects_the_native_architecture(tmp_path,monkeypatch):
    shim=tmp_path/'codex.cmd'; shim.write_bytes(b'')
    vendor=tmp_path/'node_modules'/'@openai'/'codex'/'vendor'
    for architecture in ('aarch64','x86_64'):
        exe=vendor/(architecture+'-pc-windows-msvc')/'codex'/'codex.exe'
        exe.parent.mkdir(parents=True); exe.write_bytes(b'')
    monkeypatch.setattr(terminal_providers.platform,'machine',lambda:'AMD64')
    argv=terminal_providers.command('codex',str(shim))
    assert 'x86_64-pc-windows-msvc' in argv[0]
