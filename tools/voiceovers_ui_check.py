"""Disposable real-browser walkthrough of Voiceovers; fake cloud/voice engines only."""
import base64
import io
import json
import os
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = Path(os.environ.get('VOICEOVERS_UI_WORK') or ROOT / 'build' / 'voiceovers-browser-check' / f'run-{time.time_ns()}')
WORK.mkdir(parents=True, exist_ok=True)
os.environ['VOICEOVERS_UI_WORK'] = str(WORK)


def server(port):
    os.environ['PYTHON_KEYRING_BACKEND'] = 'app.security.memkeyring.MemoryKeyring'
    os.environ['ONEPROD_SETTINGS_FILE'] = str(WORK / 'settings.json')
    import httpx
    import keyring
    import uvicorn
    from app.security.memkeyring import MemoryKeyring
    from app.config import Settings
    from app.main import create_app
    from tests.p12.test_voice_studio import wav_bytes
    keyring.set_keyring(MemoryKeyring())
    def cloud(request):
        if request.url.path == '/v1/speech-to-text':
            return httpx.Response(200, json={'language_code':'en','words':[
                {'type':'word','text':'Hello','start':.1,'end':.5,'speaker_id':'speaker_0'},
                {'type':'word','text':'Miles.','start':.6,'end':1,'speaker_id':'speaker_0'},
                {'type':'word','text':'Hi','start':1.5,'end':2,'speaker_id':'speaker_1'},
                {'type':'word','text':'Peter.','start':2.1,'end':2.5,'speaker_id':'speaker_1'}]})
        return httpx.Response(500)
    def local(request):
        if request.url.path == '/api/meta':
            return httpx.Response(200,json={'voices':['Peter_ref.wav','Miles_ref.wav'],'presets':{'clone_exact':{}}})
        if request.url.path == '/api/voices':
            return httpx.Response(200,json={'name':'Peter_ref.wav' if b'Peter' in request.content else 'Miles_ref.wav'})
        if request.url.path == '/api/compare-voice':
            return httpx.Response(200,json={'speaker_cosine':.84,'model':'synthetic-browser-scorer',
                'status':'needs_listening_review','automatically_accepted':False})
        if request.url.path == '/api/jobs':
            return httpx.Response(200,json={'id':'test-voice'})
        if request.url.path == '/api/jobs/test-voice':
            return httpx.Response(200,json={'status':'done','result':{'seconds':2},'sections':[]})
        if request.url.path == '/api/preview/test-voice/wav':
            return httpx.Response(200,content=wav_bytes(2),headers={'content-type':'audio/wav'})
        return httpx.Response(404)
    app = create_app(Settings(data_dir=WORK/'data',open_browser=False),token='voiceovers-browser-test-only-token',
                     gpu_probe=lambda:None,voice_studio_transport=httpx.MockTransport(cloud),
                     voice_transport=httpx.MockTransport(local))
    if not app.state.voice_studio.store.list_projects():
        app.state.voice_studio.store.create_project('Peter & Miles · channel')
    app.state.voice_studio.configure('elevenlabs','browser-fake-key-only',None)
    uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')


if len(sys.argv)>1 and sys.argv[1]=='serve':
    server(int(sys.argv[2]));sys.exit()

from websockets.sync.client import connect
import urllib.request
from tests.p12.test_voice_studio import wav_bytes
from app import media
for name in ('peter-clean.wav','miles-clean.wav','dialogue.wav'):
    (WORK/name).write_bytes(wav_bytes(12,140 if name.startswith('peter') else 220))
radio=WORK/'miles-radio.wav'
r=media.run([media.find_ffmpeg(),'-y','-v','error','-i',str(WORK/'miles-clean.wav'),'-af','highpass=f=300,lowpass=f=3400',str(radio)])
assert r.returncode==0
ports=[]
for _ in range(2):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));ports.append(sock.getsockname()[1])
http_port,cdp_port=ports
log=(WORK/'server.log').open('w',encoding='utf-8')
proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'serve',str(http_port)],cwd=ROOT,stdout=log,stderr=log)
chrome_path = shutil.which('chrome') or next((str(Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
    for base in (os.environ.get('PROGRAMFILES', ''), os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('LOCALAPPDATA', ''))
    if base and (Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe').is_file()), None)
if not chrome_path:
    proc.terminate()
    raise RuntimeError('Chrome is required for this developer UI check.')
chrome=subprocess.Popen([chrome_path,'--headless=new','--disable-gpu',
    '--no-first-run','--no-default-browser-check',f'--remote-debugging-port={cdp_port}',
    f'--user-data-dir={WORK / "chrome-profile"}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with opener.open(f'http://127.0.0.1:{http_port}/health',timeout=1) as response:json.load(response)
            with opener.open(f'http://127.0.0.1:{cdp_port}/json',timeout=1) as response:pages=json.load(response)
            page=next(p for p in pages if p['type']=='page');break
        except Exception:time.sleep(.1)
    else:raise RuntimeError('Disposable app/browser did not start')
    with connect(page['webSocketDebuggerUrl'],max_size=20_000_000) as ws:
        mid=0;errors=[];checks=[]
        def send(method,**params):
            global mid
            mid+=1;expected=mid;ws.send(json.dumps({'id':expected,'method':method,'params':params}))
            while True:
                msg=json.loads(ws.recv(timeout=20))
                if msg.get('method')=='Runtime.exceptionThrown':errors.append(msg['params']['exceptionDetails'])
                if msg.get('id')==expected:
                    if 'error' in msg:raise RuntimeError(msg['error'])
                    return msg.get('result',{})
        def js(code):
            result=send('Runtime.evaluate',expression=code,awaitPromise=True,returnByValue=True)
            assert 'exceptionDetails' not in result,result
            return result['result'].get('value')
        def wait(code,timeout=35):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                try:
                    if js(code):return
                except AssertionError:pass
                time.sleep(.15)
            raise AssertionError('Timed out: '+code+'\n'+str(js('document.querySelector("#message")?.innerText')))
        def check(name,code):
            assert js(code),name;checks.append(name)
        def click(selector):js(f'document.querySelector({json.dumps(selector)}).click()')
        def fill(selector,value,event='input'):
            js(f'{{const n=document.querySelector({json.dumps(selector)});n.value={json.dumps(value)};n.dispatchEvent(new Event({json.dumps(event)},{{bubbles:true}}));}}')
        def upload(selector,path):
            doc=send('DOM.getDocument')['root']['nodeId']
            node=send('DOM.querySelector',nodeId=doc,selector=selector)['nodeId']
            send('DOM.setFileInputFiles',nodeId=node,files=[str(path)])
        def shot(name):
            png=send('Page.captureScreenshot',format='png')['data'];(WORK/name).write_bytes(base64.b64decode(png))
        send('Runtime.enable');send('Page.enable');send('DOM.enable')
        send('Emulation.setDeviceMetricsOverride',width=1512,height=1200,deviceScaleFactor=1,mobile=False)
        send('Page.navigate',url=f'http://127.0.0.1:{http_port}/#t=voiceovers-browser-test-only-token')
        wait('!!document.querySelector("#voicepagebtn") && document.querySelector("#app").style.display === "grid"')
        click('#voicepagebtn');wait('document.querySelector("#project")?.options.length > 1')
        check('Dedicated page and scrubbed token','location.pathname === "/voiceovers" && location.hash === ""')
        for topic, title in [('create','One script.'),('references','Set up your voices'),('library','Find and hand off'),('connections','Choose the voice engine')]:
            click(f'[data-tab={topic}]');click('#page-guide')
            check(f'Page guide: {topic}',f'document.querySelector("#guide-dialog").open && document.querySelector("#guide-title").textContent.startsWith({json.dumps(title)})')
            click('#close-guide')
        click('#agent-help');fill('#guide-topic','review','change')
        check('Review guide explains current listening limits','document.querySelector("#guide-body").textContent.includes("not connected yet")')
        shot('user-guide.png');click('#close-guide')
        click('[data-tab=references]')
        for name,file in [('Peter','peter-clean.wav'),('Miles','miles-clean.wav')]:
            fill('#channel-voice-name',name);upload('#channel-voice-file',WORK/file)
            click('#channel-voice-consent');js('document.querySelector("#channel-voice-form").requestSubmit()')
            wait(f'[...document.querySelectorAll(".saved-voice h3")].some(n=>n.textContent==={json.dumps(name)})')
        check('Two named clean identities saved','document.querySelectorAll(".saved-voice").length===2')
        miles=js('[...document.querySelectorAll(".saved-voice")].find(n=>n.querySelector("h3").textContent==="Miles").dataset.channelVoice')
        upload(f'[data-channel-voice="{miles}"] [data-processed]',radio)
        wait(f'document.querySelector("[data-channel-voice=\\"{miles}\\"]").innerText.includes("Clean identity + separate radio sample")')
        for name in ('Peter','Miles'):
            vid=js(f'[...document.querySelectorAll(".saved-voice")].find(n=>n.querySelector("h3").textContent==={json.dumps(name)}).dataset.channelVoice')
            click(f'[data-channel-voice="{vid}"] [data-prepare-approve]');click(f'[data-channel-voice="{vid}"] [data-prepare]')
            wait(f'document.querySelector("[data-channel-voice=\\"{vid}\\"]").innerText.includes("chatterbox · ready")')
        check('Paired effect is separate from identity','document.querySelectorAll("[data-listen]").length===3')
        click(f'[data-channel-voice="{miles}"] .clone-check-controls summary')
        check('Automatic refinement has an explicit default cap',f'document.querySelector("[data-channel-voice=\\"{miles}\\"] [data-clone-auto]").checked && document.querySelector("[data-channel-voice=\\"{miles}\\"] [data-clone-attempts]").value==="5"')
        fill(f'[data-channel-voice="{miles}"] [data-clone-attempts]','1')
        check('Clone comparison requires approval',f'document.querySelector("[data-channel-voice=\\"{miles}\\"] [data-clone-start]").disabled')
        click(f'[data-channel-voice="{miles}"] [data-clone-approve]');click(f'[data-channel-voice="{miles}"] [data-clone-start]')
        wait(f'!!document.querySelector("[data-channel-voice=\\"{miles}\\"] [data-load-comparison]")')
        click(f'[data-channel-voice="{miles}"] .clone-check-result summary')
        click(f'[data-channel-voice="{miles}"] [data-load-comparison]')
        wait('document.querySelectorAll("[data-comparison-file]").length===2 && [...document.querySelectorAll("[data-comparison-file]")].every(n=>n.src.startsWith("blob:"))')
        js('document.querySelectorAll("[data-comparison-file]").forEach(n=>n.load())')
        wait('[...document.querySelectorAll("[data-comparison-file]")].every(n=>n.readyState>=2 && n.duration>0)')
        check('Reference and cloned take are playable with honest score','document.querySelector(".clone-check-result").innerText.includes("0.840") && document.querySelector(".clone-check-result").innerText.includes("Needs listening review")')
        shot('voices-and-radio.png')
        upload('#reference-file',WORK/'dialogue.wav');js('document.querySelector("#reference-form").requestSubmit()')
        wait('!document.querySelector("#reference-detail").hidden')
        check('Analysis needs a fresh approval','document.querySelector("#analyze").disabled')
        click('#approve-analyze');click('#analyze');wait('document.querySelectorAll("[data-profile-speaker]").length===2')
        check('Speaker analysis shown with timing','document.querySelector("#analysis-result").innerText.includes("TURN GAP")')
        click('[data-tab=create]');click('#example');fill('#episode-number','19');click('#preview');click('#plan')
        wait('!document.querySelector("#plan-summary").hidden')
        check('Generate disabled until approved','document.querySelector("#generate").disabled')
        shot('voiceovers-create.png')
        click('#approve-generate');click('#generate')
        wait('document.querySelector("#job-list audio") !== null')
        wait('document.querySelector("#main-player").src.startsWith("blob:")')
        wait('document.querySelector("#main-player").readyState >= 2 && document.querySelector("#main-player").duration > 0')
        check('Audio exports and review flags shown','document.querySelector("#job-list audio").closest(".job-card").querySelectorAll("[data-download]").length===7 && document.querySelector(".job-flags") !== null')
        check('Numbered final in bottom player','document.querySelector("#playback-title").textContent==="#19 VO Final" && !document.querySelector("#playback-dock").hidden')
        check('Mastered output meets the delivery targets','document.querySelector("#playback-meta").textContent.includes("Levels checked")')
        click('#history-tab');wait('document.querySelector("[data-history]") !== null');click('[data-history]')
        check('History has separate text and settings restoration','!!document.querySelector("#restore-history-text") && !!document.querySelector("#restore-history-settings")')
        fill('#script','Changed text');click('#restore-history-text')
        check('Restore text recovers the script','document.querySelector("#script").value.includes("Ever wondered")')
        shot('history-detail.png')
        click('#settings-tab');fill('.speaker:not([hidden]) [data-field=speed]','1.25')
        click('#history-tab');click('[data-history]');click('#restore-history-settings');click('#settings-tab')
        check('Restore settings recovers provider speed','document.querySelector(".speaker:not([hidden]) [data-field=speed]").value==="1"')
        click('[data-cast="1"]')
        check('Miles has the saved distortion preset','document.querySelector(".speaker:not([hidden]) [data-field=effect]").value==="miles_radio"')
        check('Best clone settings are selected for narration','document.querySelector(".speaker:not([hidden]) [data-field=use_clone_settings]").checked && !document.querySelector(".speaker:not([hidden]) .clone-defaults").hidden')
        fill('.speaker:not([hidden]) [data-field=speed]','1.15')
        check('Manual controls override clone defaults','!document.querySelector(".speaker:not([hidden]) [data-field=use_clone_settings]").checked')
        click('.speaker:not([hidden]) [data-field=use_clone_settings]')
        check('Re-enabling clone defaults restores selected controls','document.querySelector(".speaker:not([hidden]) [data-field=use_clone_settings]").checked && document.querySelector(".speaker:not([hidden]) [data-field=speed]").value==="1"')
        shot('generated-preview.png')
        click('#history-tab');click('[data-history]')
        check('Passage repair requires selection and fresh approval','document.querySelector("[data-repair-submit]").disabled')
        click('[data-repair-section="2"]');fill('[data-repair-reason]','Test selected passage repair');click('[data-repair-approve]');click('[data-repair-submit]')
        wait('document.querySelector("#history-count").textContent==="2" && [...document.querySelectorAll("#job-list .job-status")].every(n=>n.textContent==="done")')
        click('[data-history]')
        check('Repaired take retains original history','document.querySelectorAll("[data-repair-section]").length===3 && document.querySelector("#history-count").textContent==="2"')
        shot('passage-repair.png')
        click('#plan');wait('!document.querySelector("#continue-agent").hidden');click('#continue-agent')
        wait('document.querySelector("#input")?.value.includes("use_reviewed_plan=true")')
        check('Assistant handoff prefills without sending','location.pathname==="/" && !location.search.includes("voiceover=") && document.querySelector("#input").value.includes("last reviewed Voiceovers plan")')
        shot('assistant-handoff.png');click('#voicepagebtn');wait('document.querySelector("#project")?.options.length > 1')
        send('Emulation.setDeviceMetricsOverride',width=390,height=844,deviceScaleFactor=1,mobile=True)
        click('[data-tab=library]')
        check('Mobile library does not overflow','document.documentElement.scrollWidth<=window.innerWidth')
        click('[data-tab=create]');check('Mobile creation does not overflow','document.documentElement.scrollWidth<=window.innerWidth')
        shot('voiceovers-mobile.png')
        assert not errors,errors
        print(json.dumps({'status':'PASS','checks':checks,'browser_errors':errors,'screenshots':str(WORK)}))
finally:
    for running in (chrome,proc):
        running.terminate()
        try:running.wait(timeout=10)
        except subprocess.TimeoutExpired:running.kill()
    log.close()
