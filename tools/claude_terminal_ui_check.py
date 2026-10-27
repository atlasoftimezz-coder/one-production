"""Drive the native Claude Code pane in Chrome against an isolated fake CLI app."""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

sys.stdout.reconfigure(encoding='utf-8')
launch,out=sys.argv[1],Path(sys.argv[2])
out.mkdir(parents=True,exist_ok=True)
preview=os.environ.get('ONEPROD_UI_PREVIEW_DIR')
chrome=next((Path(p) for p in [os.path.join(os.environ.get('PROGRAMFILES',''),'Google/Chrome/Application/chrome.exe'),
                             os.path.join(os.environ.get('PROGRAMFILES(X86)',''),'Google/Chrome/Application/chrome.exe')]
             if Path(p).is_file()),None)
if not chrome:
    raise SystemExit(3)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
browser=subprocess.Popen([str(chrome),'--headless=new',f'--remote-debugging-port={port}',
                          '--remote-allow-origins=*','--no-first-run','--no-default-browser-check',
                          '--disable-gpu',f'--user-data-dir={out/"profile"}','about:blank'],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
results=[]; errors=[]; mid=0
program=os.environ.get('UI_CLI_PROGRAM','claude')
account_label='Adiitya' if program=='claude' else 'Codex'
try:
    for _ in range(60):
        try:
            tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=1)); break
        except OSError:
            time.sleep(.15)
    ws=connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),max_size=16*1024*1024)
    def send(method,**params):
        global mid
        mid+=1; current=mid; ws.send(json.dumps({'id':current,'method':method,'params':params}))
        while True:
            message=json.loads(ws.recv())
            if message.get('method')=='Runtime.exceptionThrown':
                errors.append(message['params']['exceptionDetails'].get('exception',{}).get('description','JS error'))
            if message.get('id')==current:
                if 'error' in message: raise RuntimeError(message['error'])
                return message.get('result',{})
    def js(expression):
        result=send('Runtime.evaluate',expression=expression,awaitPromise=True,returnByValue=True)
        if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')
    def wait(expression,timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                if js(expression): return True
            except RuntimeError:
                pass
            time.sleep(.12)
        return False
    def check(name,condition):
        results.append(bool(condition)); print(('PASS ' if condition else 'FAIL ')+name,flush=True)
        if not condition:
            print('Terminal: '+str(js('window.claudeCode?.outputText()'))[-3500:],flush=True)
            print('UI error: '+str(js("document.querySelector('#ccerror').textContent")),flush=True)
            print('JS errors: '+str(errors)[-1000:],flush=True)
            raise AssertionError(name)
    def shot(name):
        data=base64.b64decode(send('Page.captureScreenshot',format='png')['data'])
        (out/(name+'.png')).write_bytes(data)
        if preview:
            target=Path(preview); target.mkdir(parents=True,exist_ok=True); (target/(name+'.png')).write_bytes(data)
    def command(text):
        js("document.querySelector('#ccterminal .xterm-helper-textarea').focus()")
        send('Input.insertText',text=text)
        send('Input.dispatchKeyEvent',type='keyDown',key='Enter',code='Enter',windowsVirtualKeyCode=13)
        send('Input.dispatchKeyEvent',type='keyUp',key='Enter',code='Enter',windowsVirtualKeyCode=13)
    send('Runtime.enable'); send('Page.enable')
    send('Emulation.setDeviceMetricsOverride',width=1500,height=1000,deviceScaleFactor=1,mobile=False)
    send('Page.navigate',url=launch)
    check('App shell loads',wait("getComputedStyle(document.querySelector('#app')).display === 'grid'"))
    if wait("document.querySelector('#setup').classList.contains('open')",2): js("document.querySelector('#wlater').click()")
    check('Existing project is available',wait("!!document.querySelector('#projects li[data-id]')"))
    js("document.querySelector('#projects li[data-id]').click()")
    js("document.querySelector('#claudecodetab').click()")
    check('Shared CLI offers a shell and both assistants',wait("document.querySelector('#ccprogram').options.length===3"))
    js("document.querySelector('#ccprogram').value="+json.dumps(program)+"; document.querySelector('#ccprogram').dispatchEvent(new Event('change'))")
    if os.environ.get('UI_CLAUDE_EMPTY_ACCOUNTS')=='1':
        check('Empty account list explains the missing connection',wait("document.querySelector('#ccaccountnotice').textContent.includes('no saved Claude Code account') && document.querySelector('#ccstart').disabled"))
        check('Empty dropdown has a meaningful placeholder',js("document.querySelector('#ccaccount').textContent.includes('No Claude Code accounts connected')"))
        shot('claude-code-empty-accounts')
        js("document.querySelector('#ccaccounts').click()")
        check('Add Claude account opens Accounts',wait("document.querySelector('#accmodal').classList.contains('open')"))
        token='sk-ant-oat01-'+'synthetic-native-test-'*4
        js("document.querySelector('.tile[data-prov=claude_token]').click(); document.querySelector('#addlabel').value='Adiitya · test account'; document.querySelector('#addtoken').value="+json.dumps(token)+"; document.querySelector('#addform').requestSubmit()")
        check('A newly saved account appears without reopening the project',wait("document.querySelector('#ccaccount').textContent.includes('Adiitya') && !document.querySelector('#ccstart').disabled"))
        js("document.querySelector('#accclose').click()")
    check('CLI setup loads the matching saved account',wait("document.querySelector('#ccaccount').textContent.includes("+json.dumps(account_label)+")"))
    check('An existing account is selected despite a stale saved preference',js("!!document.querySelector('#ccaccount').value && !document.querySelector('#ccstart').disabled"))
    js("window.savedNativeSetup=document.querySelector('#ccconfig').value; window.savedNativeCwd=document.querySelector('#cccwd').value; document.querySelector('#ccconfig').value='custom-setup-draft'; document.querySelector('#ccaccounts').click()")
    js("document.querySelector('#acclist [data-act=toggle]').click()")
    check('Disabled accounts are visible and explained',wait("document.querySelector('#ccaccount').textContent.includes('(turned off)') && document.querySelector('#ccaccountnotice').textContent.includes('turned off') && document.querySelector('#ccstart').disabled"))
    check('Account refresh preserves the draft setup',js("document.querySelector('#ccconfig').value==='custom-setup-draft' && document.querySelector('#cccwd').value===window.savedNativeCwd"))
    js("document.querySelector('#acclist [data-act=toggle]').click()")
    check('Turning an account on updates the CLI dropdown immediately',wait("!!document.querySelector('#ccaccount').value && !document.querySelector('#ccstart').disabled && document.querySelector('#ccaccountnotice').hidden"))
    js("document.querySelector('#accclose').click(); document.querySelector('#ccconfig').value=window.savedNativeSetup")
    check('CLI pane replaces Studio chat',js("!document.querySelector('#claudecode').hidden && getComputedStyle(document.querySelector('#messages')).display==='none'"))
    check('Native setup retains editable working and config folders',js("!!document.querySelector('#cccwd').value && !!document.querySelector('#ccconfig').value"))
    shot('claude-code-setup')
    js("document.querySelector('#ccform').requestSubmit()")
    check('Real browser connects through WebSocket to ConPTY',wait("document.querySelector('#ccstatus').textContent.startsWith('Connected')"))
    instructions='AGENTS.md' if program=='codex' else 'CLAUDE.md'
    check('CLI renders workspace instructions and ANSI output',wait("window.claudeCode.outputText().includes("+json.dumps(instructions+': Use this workspace')+")"))
    command('/help')
    check('Keyboard input reaches the native CLI',wait("window.claudeCode.outputText().includes('Native commands:')"))
    command('/mcp')
    check('CLI stdio MCP reaches actual studio tools',wait("window.claudeCode.outputText().includes('MCP connected: oneproduction; script_stats')",25))
    if program=='codex':
        check('Codex MCP approvals allow decline and accept',wait("window.claudeCode.outputText().includes('Native MCP approvals verified')"))
    else:
        check('Native studio permission hook works',js("window.claudeCode.outputText().includes('Native permission hook: ask')"))
    js("document.querySelector('#ccaccounts').click(); document.querySelector('#acclist [data-act=toggle]').click()")
    check('Refreshing accounts preserves the running CLI and output',wait("document.querySelector('#ccaccount').textContent.includes('(turned off)') && !document.querySelector('#ccend').hidden && window.claudeCode.outputText().includes('MCP connected:')"))
    js("document.querySelector('#acclist [data-act=toggle]').click()")
    check('Running CLI retains its selected account after re-enabling',wait("!document.querySelector('#ccaccount').textContent.includes('(turned off)') && !!document.querySelector('#ccaccount').value"))
    js("document.querySelector('#accclose').click()")
    command('/write')
    check('CLI can create a real project file',wait("window.claudeCode.outputText().includes('Wrote native-result.md')"))
    shot('claude-code-connected')
    js("document.querySelector('#ccreconnect').click()")
    check('Reconnect restores terminal output',wait("document.querySelector('#ccstatus').textContent.startsWith('Connected') && window.claudeCode.outputText().includes('MCP connected:')"))
    send('Emulation.setDeviceMetricsOverride',width=1000,height=850,deviceScaleFactor=1,mobile=False)
    command('Resized 日本語')
    check('Resized terminal preserves Unicode input',wait("window.claudeCode.outputText().includes('Received: Resized 日本語')"))
    project_query=js('location.search')
    send('Page.reload')
    check('Refresh preserves the app authentication lock',wait("getComputedStyle(document.querySelector('#locked')).display==='grid'"))
    reopen=launch.replace('/#','/'+project_query+'#')
    send('Page.navigate',url=reopen)
    check('Authenticated reopen reattaches to the same CLI',wait("document.querySelector('#ccstatus').textContent.startsWith('Connected') && window.claudeCode.outputText().includes('MCP connected:')"))
    js("document.querySelector('#ccend').click()")
    check('End session returns to setup',wait("!document.querySelector('#ccstart').disabled && document.querySelector('#ccsetup').open"))
    js("document.querySelector('#ccprogram').value='shell'; document.querySelector('#ccprogram').dispatchEvent(new Event('change'))")
    check('Plain shell needs no saved account',js("document.querySelector('#ccaccountfield').hidden && !document.querySelector('#ccstart').disabled"))
    js("document.querySelector('#ccform').requestSubmit()")
    check('The same CLI pane opens a real PowerShell session',wait("document.querySelector('#ccstatus').textContent.startsWith('Connected')"))
    command("Write-Output 'shared-shell-ready'")
    check('Plain shell executes keyboard commands',wait("window.cliTerminal.outputText().includes('shared-shell-ready')"))
    command('claude --version')
    check('A user can start Claude commands from the shell',wait("window.cliTerminal.outputText().includes('claude test CLI version')"))
    command('codex --version')
    check('A user can start Codex commands from the shell',wait("window.cliTerminal.outputText().includes('codex test CLI version')"))
    shot('shared-cli-shell')
    js("document.querySelector('#ccend').click()")
    check('Shell session closes without closing the app',wait("!document.querySelector('#ccstart').disabled && document.querySelector('#ccsetup').open"))
    js("document.querySelector('#ccprogram').value="+json.dumps(program)+"; document.querySelector('#ccprogram').dispatchEvent(new Event('change'))")
    check('Returning to an assistant restores its selected account',js("!!document.querySelector('#ccaccount').value && !document.querySelector('#ccstart').disabled"))
    js("document.querySelector('#studiochattab').click()")
    check('Original Studio chat remains available',js("getComputedStyle(document.querySelector('#messages')).display!=='none'"))
    check('CLI-written file appears in One Production',wait("document.querySelector('#files').textContent.includes('native-result.md')"))
    check('No browser JavaScript errors',not errors)
    if errors: print(str(errors)[:1000])
    ws.close()
finally:
    browser.terminate(); browser.wait(10)
print(f'{sum(results)}/{len(results)} passed')
raise SystemExit(0 if results and all(results) else 1)
