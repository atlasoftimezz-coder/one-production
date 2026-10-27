"""Isolated app for the browser → ConPTY → stdio MCP → studio integration check."""
import os
import secrets
import socket
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
os.environ['PYTHON_KEYRING_BACKEND']='app.security.memkeyring.MemoryKeyring'
os.environ['ONEPROD_AUDIO_REVIEW_DISABLED']='1'
from app.accounts import Accounts
from app.claude_terminal import spawn_pty
from app.config import Settings
from app.main import create_app
from app.security.memkeyring import MemoryKeyring
from app.storage import Store
import app.main
import keyring
import uvicorn

keyring.set_keyring(MemoryKeyring())
data=Path(sys.argv[1])
data.mkdir(parents=True,exist_ok=True)
cli=Path(__file__).with_name('native_terminal_cli.py')
shim=data/'claude.cmd'
shim.write_text(f'@"{sys.executable}" "{cli}" --assistant claude %*\r\n',encoding='utf-8')
codex_shim=data/'codex.cmd'
codex_shim.write_text(f'@"{sys.executable}" "{cli}" --assistant codex %*\r\n',encoding='utf-8')
exe=data/'node_modules'/'@anthropic-ai'/'claude-code'/'bin'/'claude.exe'
exe.parent.mkdir(parents=True); exe.write_bytes(b'')
codex_exe=data/'node_modules'/'@openai'/'codex'/'vendor'/'x86_64-pc-windows-msvc'/'codex'/'codex.exe'
codex_exe.parent.mkdir(parents=True); codex_exe.write_bytes(b'')
program=sys.argv[sys.argv.index('--program')+1] if '--program' in sys.argv else 'claude'
accounts=Accounts(data)
account=None
if '--empty-accounts' not in sys.argv:
    account=accounts.add(program,'Adiitya · test account' if program=='claude' else 'Codex · test account')
    if program=='claude':
        accounts.set_token(account['id'],'sk-ant-oat01-'+'synthetic-native-test-'*4)
    accounts.record_status(account['id'],{'ok':True,'method':'OAuth token'})
store=Store(data)
project=store.create_project('Claude Code workspace')
files=store.files_dir(project['id'])
(files/'CLAUDE.md').write_text('Use this workspace and keep existing outputs.',encoding='utf-8')
(files/'AGENTS.md').write_text('Use this workspace and keep existing outputs.',encoding='utf-8')
(files/'ep1.md').write_text('## Debt\nCancel the debt today.\n',encoding='utf-8')
if account:
    # A previous account can have been removed. The UI must select an existing
    # enabled account rather than assigning the stale ID to an empty selection.
    import json
    (files.parent/'claude-code.json').write_text(json.dumps({'provider':program,'account_id':program+'-removed',
        'config_dir':account['profile_dir'],'cwd':str(files),'studio':True}),encoding='utf-8')

def factory(argv,**kwargs):
    if kwargs['env'].get('ONEPROD_CLI_PROVIDER')=='shell':
        kwargs['env']['PATH']=str(data)+os.pathsep+kwargs['env']['PATH']
        # Exercise the real shell without loading the owner's personal profile.
        return spawn_pty([*argv,'-NoProfile'],**kwargs)
    return spawn_pty([sys.executable,'-u',str(cli),*argv[1:]],**kwargs)

app.main.image_home=lambda data_dir,chosen,drives:data_dir/'modules'/'imagegen'
token=secrets.token_urlsafe(32)
app=create_app(Settings(data_dir=data,claude_cmd=str(shim),codex_cmd=str(codex_shim),voice_app_url='http://127.0.0.1:1',
                        open_browser=False,login_console=False),token=token,terminal_factory=factory)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
print(f'launch: http://127.0.0.1:{port}/#t={token}',flush=True)
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning',access_log=False)
