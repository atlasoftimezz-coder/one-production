"""Native CLI boundaries: credentials, real TTY I/O, reconnect, native setup and studio bridge.

Every account and credential here is synthetic. No inference or owner profile is used.
"""
import json
import os
import queue
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.claude_terminal import BRIDGE_HEADER, REPLAY_LIMIT, TerminalSession, spawn_pty
from .conftest import claude_account, events, send, wait_ask
from tests.p2.conftest import GOOD_KEY

FAKE_TOKEN = 'sk-ant-oat01-' + 'synthetic-terminal-token-' * 3


class FakePTY:
    pid = 123456789
    exitstatus = None

    def __init__(self, argv, **kwargs):
        self.argv, self.options = argv, kwargs
        self.closed, self.inputs, self.dimensions = False, [], []
        self.output = queue.Queue()
        self.output.put('\x1b[32mClaude Code test terminal\x1b[0m\r\n')

    def read(self, size):
        item = self.output.get(timeout=10)
        if item is None:
            raise EOFError
        return item

    def write(self, data):
        self.inputs.append(data)
        self.output.put('input: ' + data)

    def setwinsize(self, rows, cols):
        self.dimensions.append((rows, cols))

    def isalive(self):
        return False  # no OS process to terminate

    def close(self, force=False):
        self.closed = True
        self.output.put(None)


@pytest.fixture
def native(agent):
    made = []
    def factory(argv, **kwargs):
        process = FakePTY(argv, **kwargs)
        made.append(process)
        return process
    agent.app.state.claude_terminals.factory = factory
    agent.ptys = made
    acc = agent.post('/api/accounts', json={'provider':'claude', 'label':'Synthetic Adiitya',
                                           'oauth_token':FAKE_TOKEN})
    assert acc.status_code == 200, acc.text
    agent.acc = acc.json()
    return agent


def start(c, **kwargs):
    response = c.post(f'/api/projects/{c.pid}/claude-code', json={'account_id':c.acc['id'], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def stop(c, session):
    return c.delete(f"/api/projects/{c.pid}/claude-code/{session['id']}")


def connect(c, session, **kwargs):
    return c.websocket_connect(f"/api/projects/{c.pid}/claude-code/{session['id']}/socket",
                              headers={'host':'127.0.0.1','origin':'http://127.0.0.1', **kwargs})


def test_account_discovery_distinguishes_empty_other_provider_and_disabled(agent):
    c=agent
    url=f'/api/projects/{c.pid}/claude-code'
    empty=c.get(url).json()
    assert empty['accounts']==[] and empty['other_account_count']==0
    assert Path(empty['data_dir'])==c.data_dir
    c.post('/api/accounts',json={'provider':'codex','label':'Other provider'})
    other=c.get(url).json()
    assert other['accounts']==[] and other['other_account_count']==1
    account=c.post('/api/accounts',json={'provider':'claude','label':'Saved Claude'}).json()
    c.patch(f"/api/accounts/{account['id']}",json={'enabled':False})
    off=c.get(url).json()
    assert len(off['accounts'])==1 and off['accounts'][0]['enabled'] is False
    assert off['accounts'][0]['label']=='Saved Claude'
    c.patch(f"/api/accounts/{account['id']}",json={'enabled':True})
    assert c.get(url).json()['accounts'][0]['enabled'] is True


def test_native_preserves_cli_features_and_selected_oauth(native, tmp_path, monkeypatch):
    c = native
    workspace, setup = tmp_path/'real repo', tmp_path/'native setup'
    workspace.mkdir(); setup.mkdir()
    (workspace/'CLAUDE.md').write_text('Owner project instructions', encoding='utf-8')
    monkeypatch.setenv('CLAUDE_CODE_DISABLE_CLAUDE_MDS', '1')
    monkeypatch.setenv('CLAUDE_CODE_DISABLE_AUTO_MEMORY', '1')
    monkeypatch.setenv('CLAUDECODE', 'outer-coding-session')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'wrong-inherited-key')
    session = start(c, cwd=str(workspace), config_dir=str(setup))
    process = c.ptys[-1]
    env, argv = process.options['env'], process.argv
    assert env['CLAUDE_CODE_OAUTH_TOKEN'] == FAKE_TOKEN
    assert env['CLAUDE_CONFIG_DIR'] == str(setup.resolve())
    assert process.options['cwd'] == str(workspace.resolve())
    assert all(k not in env for k in ('CLAUDECODE','ANTHROPIC_API_KEY','CLAUDE_CODE_DISABLE_CLAUDE_MDS','CLAUDE_CODE_DISABLE_AUTO_MEMORY'))
    assert not any(flag in argv for flag in ('-p','--print','--setting-sources','--strict-mcp-config','--disable-slash-commands','--dangerously-skip-permissions'))
    assert '--session-id' in argv and '--mcp-config' in argv
    instructions=Path(argv[argv.index('--append-system-prompt-file')+1]).read_text(encoding='utf-8')
    assert 'normal setup' in instructions and 'production agent inside' not in instructions
    assert 'Plain text, no markdown tables' not in instructions
    mcp = json.loads(argv[argv.index('--mcp-config')+1])
    assert mcp['mcpServers']['oneproduction']['env']['ONEPROD_BRIDGE_KEY'] == '${ONEPROD_BRIDGE_KEY}'
    assert mcp['mcpServers']['oneproduction']['env']['CLAUDE_CODE_OAUTH_TOKEN'] == ''
    saved = (c.files.parent/'terminal.json').read_text(encoding='utf-8')
    assert FAKE_TOKEN not in saved and FAKE_TOKEN not in json.dumps(session) and FAKE_TOKEN not in ' '.join(argv)
    assert (workspace/'CLAUDE.md').read_text(encoding='utf-8') == 'Owner project instructions'
    assert stop(c, session).status_code == 200


def test_websocket_auth_replay_input_resize_and_reconnect(native):
    c=native; session=start(c)
    with connect(c,session) as ws:
        ws.send_json({'token': c.app.state.token})
        assert ws.receive_json()['t'] == 'ready'
        assert 'Claude Code' in ws.receive_json()['data']
        ws.send_json({'t':'input','data':'/help\r'})
        assert ws.receive_json()['data'] == 'input: /help\r'
        ws.send_json({'t':'resize','rows':42,'cols':120})
        ws.send_json({'t':'input','data':'日本語\r'})
        assert '日本語' in ws.receive_json()['data']
    assert not c.ptys[-1].closed
    with connect(c,session) as ws:
        ws.send_json({'token': c.app.state.token})
        assert ws.receive_json()['t'] == 'ready'
        assert 'Claude Code' in ws.receive_json()['data']
        assert '/help' in ws.receive_json()['data']
        assert '日本語' in ws.receive_json()['data']
    assert (42,120) in c.ptys[-1].dimensions
    stop(c,session)
    assert c.ptys[-1].closed


@pytest.mark.parametrize('origin,token', [('https://evil.example','valid'), ('http://localhost','valid'),
                                          ('http://127.0.0.1','wrong')])
def test_foreign_or_unauthenticated_socket_cannot_control_a_process(native,origin,token):
    c=native; session=start(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/api/projects/{c.pid}/claude-code/{session['id']}/socket", headers={'host':'127.0.0.1','origin':origin}) as ws:
            ws.send_json({'token':c.app.state.token if token=='valid' else token})
            ws.receive_json()
    assert not c.ptys[-1].inputs
    stop(c,session)


def test_bridge_is_scoped_and_shares_the_actual_project_tools(native):
    c=native; session=start(c)
    url=f"/claude-code-bridge/{session['id']}"
    assert c.get(url+'/tools').status_code==403
    headers={BRIDGE_HEADER:c.ptys[-1].options['env']['ONEPROD_BRIDGE_KEY']}
    tools=c.get(url+'/tools',headers=headers).json()
    names={t['name'] for t in tools}
    assert {'script_stats','source_status','source_call','drive_browse','discord_read','x_digest','voiceover_studio_status'} <= names
    result=c.post(url+'/call',headers=headers,json={'name':'script_stats','arguments':{'file':'ep1.md'}})
    assert result.status_code==200 and result.json()['error'] is False, result.text
    assert 'Debt' in result.json()['text'] or 'word' in result.json()['text']
    for name in ('voiceover_studio_generate','source_call','drive_import'):
        result=c.post(url+'/policy',headers=headers,json={'tool_name':'mcp__oneproduction__'+name,'tool_input':{}})
        assert result.json()['hookSpecificOutput']['permissionDecision']=='ask'
    assert c.post(url+'/policy',headers=headers,json={'tool_name':'mcp__oneproduction__source_status'}).json()=={}
    assert c.post(url+'/call',headers=headers,json={'name':'source_call','arguments':{'name':'not-approved'}}).status_code==403
    stop(c,session)
    assert c.get(url+'/tools',headers=headers).status_code==403


def test_api_key_account_uses_its_own_key_and_creates_only_its_default_profile(native,monkeypatch):
    c=native
    acc=c.post('/api/accounts',json={'provider':'anthropic_api','label':'Test API','api_key':GOOD_KEY}).json()
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN','wrong-inherited-token')
    c.acc=acc
    session=start(c,studio=False)
    env=c.ptys[-1].options['env']
    assert env['ANTHROPIC_API_KEY']==GOOD_KEY and env['CLAUDE_CODE_OAUTH_TOKEN']==''
    assert '--mcp-config' not in c.ptys[-1].argv and '--settings' not in c.ptys[-1].argv
    assert Path(session['config_dir']).is_dir()
    stop(c,session)


def test_profile_lock_blocks_both_native_socket_and_private_bridge(native):
    c=native; session=start(c)
    url=f"/claude-code-bridge/{session['id']}/tools"
    headers={BRIDGE_HEADER:c.ptys[-1].options['env']['ONEPROD_BRIDGE_KEY']}
    assert c.post('/api/profile/pin',json={'new':'5273'}).status_code==200
    assert c.post('/api/profile/lock').status_code==200
    assert c.get(url,headers=headers).status_code==403
    with pytest.raises(WebSocketDisconnect):
        with connect(c,session):
            pass
    assert c.get(f'/api/projects/{c.pid}/claude-code').status_code==423
    assert c.post('/api/profile/unlock',json={'pin':'5273'}).status_code==200
    assert c.get(url,headers=headers).status_code==200
    stop(c,session)


def test_studio_permission_requests_are_bound_to_arguments_once():
    session=TerminalSession('s','p','a','.','.','c',None)
    arguments={'source':'neon','name':'read_reference','arguments':{'query':'a'}}
    assert not session.studio_request('source_call',arguments)
    session.studio_request('source_call',arguments,approve=True)
    assert not session.studio_request('source_call',{**arguments,'name':'delete_reference'})
    assert session.studio_request('source_call',arguments)
    assert not session.studio_request('source_call',arguments)


@pytest.mark.parametrize('npm_shim',[False,True])
def test_sdk_bundled_cli_is_a_fallback_but_bad_explicit_paths_are_respected(tmp_path,monkeypatch,npm_shim):
    from app.agent import claude_agent
    package=tmp_path/'sdk'; package.mkdir()
    binary=package/'_bundled'/('claude.exe' if os.name=='nt' else 'claude')
    binary.parent.mkdir(); binary.write_bytes(b'')
    shim=tmp_path/'claude.cmd'; shim.write_bytes(b'')
    monkeypatch.setattr(claude_agent,'resolve',lambda *args: str(shim) if npm_shim else None)
    monkeypatch.setattr(claude_agent.shutil,'which',lambda *args: None)
    monkeypatch.setattr(claude_agent,'_sdk',lambda:SimpleNamespace(__file__=str(package/'__init__.py')))
    assert claude_agent.claude_exe(None)==str(binary)
    assert claude_agent.claude_exe(str(shim)) is None


def test_busy_projects_cannot_run_competing_agents_or_be_deleted(native):
    c=native; session=start(c)
    assert c.post(f'/api/projects/{c.pid}/claude-code',json={'account_id':c.acc['id']}).status_code==400
    assert c.post(f'/api/projects/{c.pid}/chat',json={'message':'write','account_id':c.acc['id']}).status_code==409
    assert c.post(f'/api/projects/{c.pid}/team',json={'goal':'write'}).status_code==409
    assert c.delete(f'/api/projects/{c.pid}').status_code==409
    stop(c,session)
    c.script.turns=[[('tool','Bash',{'command':'test'}),('text','done')]]
    future=send(c,c.acc,'shell')
    ask=wait_ask(c)
    assert c.post(f'/api/projects/{c.pid}/claude-code',json={'account_id':c.acc['id']}).status_code==409
    c.post(f'/api/projects/{c.pid}/agent/answer',json={'id':ask['id'],'answer':'deny'})
    events(future)


def test_resume_only_existing_transcripts_fresh_keeps_old_files(native):
    c=native; first=start(c); stop(c,first)
    second=start(c)
    assert second['conversation_id']!=first['conversation_id']  # first never sent a prompt
    stop(c,second)
    transcripts=Path(second['config_dir'])/'projects'/'synthetic-project'
    transcripts.mkdir(parents=True)
    transcript=transcripts/(second['conversation_id']+'.jsonl')
    transcript.write_text('{"type":"user"}\n',encoding='utf-8')
    third=start(c)
    assert third['conversation_id']==second['conversation_id']
    assert '--resume' in c.ptys[-1].argv
    stop(c,third)
    fourth=start(c,fresh=True)
    assert fourth['conversation_id']!=third['conversation_id']
    assert transcript.is_file()
    stop(c,fourth)


def test_browser_login_cannot_accidentally_use_another_profiles_login(native,tmp_path):
    c=native; other=claude_account(c)
    shared=tmp_path/'other setup'; shared.mkdir()
    result=c.post(f'/api/projects/{c.pid}/claude-code',json={'account_id':other['id'],'config_dir':str(shared)})
    assert result.status_code==400 and 'browser-login' in result.text
    assert not c.ptys


def test_paths_static_assets_and_replay_are_bounded(native,tmp_path):
    c=native
    response=c.post(f'/api/projects/{c.pid}/claude-code',json={'account_id':c.acc['id'],'cwd':str(tmp_path/'missing')})
    assert response.status_code==400 and not c.ptys
    for asset in ('/claude-code.js','/claude-code.css','/vendor/xterm/xterm.js','/vendor/xterm/addon-fit.js','/vendor/xterm/xterm.css'):
        assert c.get(asset).status_code==200
    assert c.get('/vendor/xterm/manifest.json').status_code==404
    state=c.get(f'/api/projects/{c.pid}/claude-code').json()
    assert state['accounts'][0]['can_share_setup'] is True
    assert FAKE_TOKEN not in json.dumps(state)
    session=TerminalSession('s','p','a','.','.','c',None)
    for _ in range(12):
        session.emit({'t':'output','data':'x'*(REPLAY_LIMIT//10)})
    assert session.size<=REPLAY_LIMIT


@pytest.mark.skipif(os.name!='nt',reason='Windows ConPTY integration')
def test_real_conpty_input_and_unicode_without_an_account(tmp_path):
    script=tmp_path/'echo.py'
    script.write_text("print('PTY ready', flush=True)\nprint('echo: '+input(), flush=True)\n",encoding='utf-8')
    process=spawn_pty([sys.executable,'-u',str(script)],cwd=str(tmp_path),env=dict(os.environ),dimensions=(24,80))
    try:
        output=''
        while 'PTY ready' not in output:
            output+=process.read(8192)
        process.setwinsize(32,120)
        process.write('hello 日本語\r')
        while 'echo:' not in output:
            output+=process.read(8192)
        assert 'hello 日本語' in output
    finally:
        process.close(force=True)


def test_installed_official_cli_accepts_launch_flags_without_an_account(tmp_path):
    from app.agent.claude_agent import claude_exe
    binary=claude_exe(None)
    if not binary:
        pytest.skip('Official Claude Code CLI not installed')
    cfg=tmp_path/'empty-claude-setup'; cfg.mkdir()
    prompt=tmp_path/'instructions.md'; prompt.write_text('Test instructions',encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith(('CLAUDE','ANTHROPIC'))}
    env.update(CLAUDE_CONFIG_DIR=str(cfg),CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
               DISABLE_TELEMETRY='1',DISABLE_ERROR_REPORTING='1')
    result=subprocess.run([binary,'--session-id',str(uuid.uuid4()),'--settings','{}',
                           '--mcp-config','{"mcpServers":{}}','--append-system-prompt-file',str(prompt),'--help'],
                          cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25)
    assert result.returncode==0, result.stderr[-1000:]
    assert '--resume' in result.stdout and '--mcp-config' in result.stdout
