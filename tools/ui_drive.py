"""Drive a headless Chrome through the DevTools protocol and walk every UI feature.

    ui_drive.py <launch-url> <output-dir>

Exits 0 only if every check passes. Screenshots go to <output-dir>. Uses the Chrome the
user already has (no extra install); exits 3 if Chrome is not found.
"""
import base64, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path
from websockets.sync.client import connect

LAUNCH = sys.argv[1]
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
SCR = OUT
CHROME = next((p for p in (
    os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
) if os.path.isfile(p)), None)
if not CHROME:
    print("Chrome not found"); sys.exit(3)
W, H = int(os.environ.get("W", 1600)), int(os.environ.get("H", 1000))

import socket
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]
prof = SCR / "chrome-prof"
chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                           f"--user-data-dir={prof}", f"--window-size={W},{H}",
                           "--no-first-run", "--no-default-browser-check",
                           "--autoplay-policy=no-user-gesture-required", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            page = next(t for t in targets if t["type"] == "page"); break
        except Exception:
            time.sleep(0.2)
    ws = connect(page["webSocketDebuggerUrl"], max_size=50_000_000)  # noqa: closed below
    mid = 0; errors = []; results = []

    def send(method, **params):
        global mid
        mid += 1; my = mid
        ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("method") == "Runtime.exceptionThrown":
                errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "")[:300])
            if m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                errors.append("console.error: " + str([a.get("value") for a in m["params"]["args"]])[:300])
            if m.get("id") == my:
                if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def js(expr):
        r = send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r: raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def shot(name):
        data = send("Page.captureScreenshot", format="png")["data"]
        (OUT / f"{name}.png").write_bytes(base64.b64decode(data)); return name

    def wait_for(expr, timeout=8):
        # An expression may throw while the page is still loading (element not there yet) -
        # that means "not yet", not "fail". Only the timeout decides failure.
        end = time.time() + timeout
        while time.time() < end:
            try:
                if js(expr): return True
            except RuntimeError:
                pass
            time.sleep(0.15)
        return False

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail)); print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))

    send("Runtime.enable"); send("Page.enable"); send("DOM.enable")
    send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
    send("Page.navigate", url=LAUNCH)
    check("app shell visible", wait_for("getComputedStyle(document.querySelector('#app')).display==='grid'"))
    # P7: a fresh data folder opens the setup wizard by itself; the other checks run without it
    setup_opened = wait_for("document.querySelector('#setup').classList.contains('open')", 4)
    if os.environ.get("UI_DRIVE_SETUP") == "1":
        check("setup wizard opens by itself on a new install", setup_opened)
        check("welcome step offers drives with their free space", wait_for(
            "document.querySelectorAll('#wdrives .wcard').length>=1 && document.querySelector('#wdrives').innerText.includes('GB free')", 8))
        shot("30-setup-welcome")
    if setup_opened:
        js("document.querySelector('#wlater').click()")
        wait_for("!document.querySelector('#setup').classList.contains('open')", 3)
    check("token scrubbed from address bar", js("location.hash")=="", js("location.href"))
    check("no garbled characters", not js("/Ã|Â/.test(document.body.innerText)"))
    wait_for("document.querySelector('.hero')")
    shot("01-welcome")

    # new project: empty name refused, real name works
    js("document.querySelector('#newbtn').click()")
    check("new-project form opens", js("document.querySelector('#newform').classList.contains('open')"))
    js("document.querySelector('#newform').requestSubmit()")
    time.sleep(0.4)
    check("empty name refused", js("document.querySelectorAll('#projects li[data-id]').length")==0)
    js("document.querySelector('#projname').value='Ghost Towns Episode 4'; document.querySelector('#newform').requestSubmit()")
    check("project created + selected", wait_for("!!document.querySelector('#projects li.active')"))
    check("breadcrumb shows project", "Ghost Towns" in (js("document.querySelector('#crumb').innerText") or ""))
    shot("02-new-project")

    # chat streaming
    js("document.querySelector('#input').value='Plan a 10-minute documentary about Pripyat'; document.querySelector('#composer').requestSubmit()")
    saw_typing = wait_for("!!document.querySelector('.typing')", 2)
    time.sleep(0.25)
    partial = js("(document.querySelectorAll('.row.assistant .bubble')[0]||{}).innerText||''")
    wait_for("!document.querySelector('.typing') && !document.querySelector('#send').disabled", 8)
    full = js("document.querySelectorAll('.row.assistant .bubble')[0].innerText")
    check("typing indicator shown", saw_typing)
    check("reply streams (partial before full)", 0 < len(partial) < len(full), f"{len(partial)} -> {len(full)} chars")
    check("reply echoes message", "Pripyat" in full)
    shot("03-chat")

    # uploads through the real file input
    files = []
    samples = {"script.docx": b"PK\x03\x04" + os.urandom(3000), "voice.mp3": b"ID3" + os.urandom(80000),
               "thumb.png": b"\x89PNG\r\n\x1a\n" + os.urandom(20000), "notes.txt": "Pripyat · Hashima — ✓".encode()}
    up = SCR / "up"; up.mkdir(exist_ok=True)
    for n, b in samples.items():
        (up / n).write_bytes(b); files.append(str(up / n))
    doc = send("DOM.getDocument")["root"]["nodeId"]
    node = send("DOM.querySelector", nodeId=doc, selector="#fileinput")["nodeId"]
    send("DOM.setFileInputFiles", nodeId=node, files=files)
    check("4 files listed after upload", wait_for("document.querySelectorAll('#files li').length===4", 10),
          str(js("document.querySelectorAll('#files li').length")))
    labels = js("[...document.querySelectorAll('#files .ftype')].map(e=>e.innerText).join(',')")
    check("file type badges", all(x in labels for x in ("DOCX", "MP3", "PNG", "TXT")), labels)
    shot("04-files")

    # duplicate name kept
    send("DOM.setFileInputFiles", nodeId=node, files=[files[3]])
    check("duplicate gets (1) copy", wait_for("[...document.querySelectorAll('#files .fname')].some(e=>e.innerText==='notes (1).txt')", 8))

    # download round trip byte-equal, done the way the page does it (token header)
    eq = js("""(async () => {
        const name='notes.txt';
        const pid=document.querySelector('#projects li.active').dataset.id;
        const r = await api(`/api/projects/${pid}/files/${encodeURIComponent(name)}`);
        return (await r.text());
      })()""")
    check("download returns identical bytes", eq == samples["notes.txt"].decode(), repr(eq)[:60])

    # chips + welcome card in an empty project
    js("document.querySelector('#newbtn').click(); document.querySelector('#projname').value='Empty one'; document.querySelector('#newform').requestSubmit()")
    check("empty project shows suggestions", wait_for("document.querySelectorAll('.chip').length===3"))
    js("document.querySelector('.chip').click()")
    check("chip fills the message box", "documentary" in (js("document.querySelector('#input').value") or ""))
    shot("05-empty-project")

    # history after switching back
    js("document.querySelector('#projects li[data-id]:nth-child(2)').click()")
    check("history restored on switch", wait_for("document.querySelectorAll('.row').length>=2"))

    # narrow window
    send("Emulation.setDeviceMetricsOverride", width=1100, height=800, deviceScaleFactor=1, mobile=False)
    time.sleep(0.3); shot("06-narrow-1100")
    send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    if os.environ.get("UI_DRIVE_KNOWLEDGE") == "1":
        js("document.querySelector('#knowledgebtn').click()")
        check("knowledge panel opens", wait_for("document.querySelector('#knowledgemodal').classList.contains('open')"))
        js("document.querySelector('#knowledgechannel').value='Finance'; document.querySelector('#knowledgeinstalled').value='21.0'; document.querySelector('#knowledgecontext').requestSubmit()")
        time.sleep(0.3)
        manual = SCR / "manual.md"
        manual.write_text("# Ripple trimming\nRipple trimming moves downstream clips.", encoding="utf-8")
        document_id = send("DOM.getDocument")["root"]["nodeId"]
        input_id = send("DOM.querySelector", nodeId=document_id, selector="#knowledgefile")["nodeId"]
        send("DOM.setFileInputFiles", nodeId=input_id, files=[str(manual)])
        js("document.querySelector('#knowledgescope').value='channel'; document.querySelector('#knowledgeapplication').value='resolve'; document.querySelector('#knowledgeversion').value='21.1'; document.querySelector('#knowledgeimport').requestSubmit()")
        check("manual import becomes ready", wait_for("document.querySelector('#knowledgedocuments').innerText.includes('ready')", 15))
        js("document.querySelector('#knowledgequery').value='ripple'; document.querySelector('#knowledgesearch').requestSubmit()")
        check("retrieval displays a cited passage", wait_for("document.querySelector('#knowledgeresults').innerText.includes('section 1') && document.querySelector('#knowledgeresults').innerText.includes('downstream clips')"))
        check("newer manual shown with a warning", "21.1 manual; 21.0 is installed" in (js("document.querySelector('#knowledgeresults').innerText") or ""))
        check("source opening uses protected fetch", js("document.querySelector('#knowledgeresults [data-source]') !== null"))
        js("document.querySelector('#learningtitle').value='Transitions'; document.querySelector('#learningtext').value='Use restrained transitions.'; document.querySelector('#learningform').requestSubmit()")
        check("direction saved in channel memory", wait_for("document.querySelector('#learninglist').innerText.includes('Use restrained transitions.')"))
        js("document.querySelector('#learninglist [data-correct]').click(); document.querySelector('#learningtext').value='Keep transitions subtle.'; document.querySelector('#learningform').requestSubmit()")
        check("correction replaces active direction", wait_for("document.querySelector('#learninglist').innerText.includes('Keep transitions subtle.') && !document.querySelector('#learninglist').innerText.includes('Use restrained transitions.')"))
        shot("knowledge-library")
        js("document.querySelector('#learninglist [data-learning-action=forget]').click()")
        check("forgotten direction leaves active memory", wait_for("document.querySelector('#learninglist').innerText.includes('No saved directions')"))
        js("document.querySelector('#knowledgeclose').click()")

    if os.environ.get("UI_DRIVE_RESOLVE") == "1":       # server runs against the stand-in Resolve
        js("document.querySelector('#resolvebtn').click()")
        check("resolve panel opens", wait_for("document.querySelector('#resolvemodal').classList.contains('open')"))
        check("resolve control starts off", js("document.querySelector('#resolveenabled').checked") is False)
        check("resolve MCP switch starts off and shows the exe it found",
              js("document.querySelector('#resolvemcp').checked") is False
              and js("document.querySelector('#resolvemcpinfo').innerText.includes('ResolveMCP.exe')"))
        js("document.querySelector('#resolvemcp').click()")
        check("resolve MCP turns on and says changes ask first",
              wait_for("document.querySelector('#resolvemcpinfo').innerText.includes('show you the code first')", 5))
        js("document.querySelector('#resolveenabled').click()")
        time.sleep(0.4)
        js("document.querySelector('#resolvecheck').click()")
        check("probe shows version and open project", wait_for("document.querySelector('#resolvestatus').innerText.includes('21.0.0.47') && document.querySelector('#resolvestatus').innerText.includes('Owner project')", 20))
        plan = {"title": "UI cut", "operations": [{"op": "import_media", "files": ["voice.mp3"]},
                                                 {"op": "create_timeline", "name": "Main"},
                                                 {"op": "append_clips", "files": ["voice.mp3"]}]}
        js("document.querySelector('#resolveadd').parentElement.open = true; document.querySelector('#resolveplanjson').value = " + json.dumps(json.dumps(plan)) + "; document.querySelector('#resolveadd').requestSubmit()")
        check("plan listed for review", wait_for("document.querySelector('#resolveplans').innerText.includes('New timeline \"Main\"')", 20))
        js("document.querySelector('#resolveplans [data-run]').click()")
        time.sleep(0.5)
        check("run refused until saved box ticked", js("document.querySelector('#resolveplans').innerText.includes('proposed')"))
        js("document.querySelector('#resolveplans [data-saved]').click(); document.querySelector('#resolveplans [data-run]').click()")
        check("run passes and is verified", wait_for("document.querySelector('#resolveplans').innerText.includes('Passed') && document.querySelector('#resolveplans').innerText.includes('verified method')", 30))
        check("control toggle still shows on", js("document.querySelector('#resolveenabled').checked") is True, str(js("document.querySelector('#resolveenabled').checked")))
        shot("resolve-plans")
        js("document.querySelector('#resolveclose').click()")

    if os.environ.get("UI_DRIVE_COMPETITORS") == "1":
        js("document.querySelector('#compbtn').click()")
        check("competitors tab opens", wait_for("document.querySelector('#compmodal').classList.contains('open')"))
        check("no styles yet", wait_for("document.querySelector('#comp_list').innerText.includes('No styles yet')", 5))
        check("explains the download is for measuring only",
              "never used in your videos" in (js("document.querySelector('#compform').innerText") or ""))
        js("document.querySelector('#comp_links').value='no links in here'; document.querySelector('#compform').requestSubmit()")
        check("text without YouTube links is refused in plain words",
              wait_for("[...document.querySelectorAll('.bubble')].some(b=>b.innerText.includes('paste at least one YouTube'))", 10))
        js("""{ const b = document.createElement('div'); b.id = 'stdemo'; document.body.appendChild(b);
             renderStyle(b, {state: 'running', name: 'Rival fast', stages: [{id: 'watch', title: 'Watch the videos', status: 'passed', detail: '1 video(s) measured'},
               {id: 'learn', title: 'Learn the style', status: 'running', detail: 'the AI is looking at the frames'}],
               videos: [{title: 'Why the Sahara was green', shots: 48, cuts_per_min: 0.2, beats_per_min: 11}], problems: [{link: 'https://youtu.be/x', why: 'Video unavailable'}]}, 'x', null);
             b.insertAdjacentHTML('beforeend', styleDetail({summary: 's', pacing: {hook: 'a cut every second'}, visual_language: [{kind: 'kinetic type', share: 60, how: 'words slam in'}],
               typography: {case: 'upper'}, color: {approach: 'dark'}, motion: {eases: 'fast'}, sound: {sfx: 'clicks'}, do: ['cut on claims'], dont: ['slow fades']})); }""")
        txt = (js("document.querySelector('#stdemo').innerText") or "").lower()
        check("style panel shows stages, videos, problems and the profile",
              all(x in txt for x in ("watch the videos", "learn the style", "11 visible changes/min", "0.2 hard cuts/min", "video unavailable",
                                     "kinetic type", "a cut every second", "cut on claims", "slow fades")), txt[:300])
        js("document.querySelector('#stdemo').remove()")

    if os.environ.get("UI_DRIVE_EPISODE") == "1":
        js("document.querySelector('#episodebtn').click()")
        check("episode dialog opens", wait_for("document.querySelector('#episodemodal').classList.contains('open')"))
        check("scripts listed (not gate reports)", wait_for("[...document.querySelectorAll('#ep_script option')].some(o=>o.value==='notes.txt')"))
        check("music offers project audio", "voice.mp3" in (js("[...document.querySelectorAll('#ep_music option')].map(o=>o.value).join()") or ""))
        check("heavy work listed", "DaVinci Resolve" in (js("document.querySelector('#ep_heavy').innerText") or ""))
        check("a draft is the default and says shot scripts run without a card and Resolve renders",
              js("document.querySelector('#ep_draft').checked") is True
              and wait_for("document.querySelector('#ep_heavy').innerText.includes('without a card') && "
                           "document.querySelector('#ep_heavy').innerText.includes('Resolve renders the draft')", 5))
        check("preflight says what is missing before the run",
              wait_for("document.querySelector('#ep_heavy li.bad') && document.querySelector('#ep_heavy').innerText.includes('Claude')", 15),
              (js("document.querySelector('#ep_heavy').innerText") or "")[:300])
        check("style picker points to the Competitors tab", "Competitors tab" in (js("document.querySelector('#ep_styles').innerText") or ""))
        js("document.querySelector('#ep_draft').click()")
        check("without the draft switch you approve each script and render yourself",
              "render the timeline yourself" in (js("document.querySelector('#ep_heavy').innerText") or ""))
        js("document.querySelector('#ep_draft').click()")
        check("no size or burn-in controls (Fusion is 1080p60)", js("!document.querySelector('#ep_size') && !document.querySelector('#ep_burn')"))
        js("document.querySelector('#ep_name').value='Ep 1'; document.querySelector('#episodeform').requestSubmit()")
        check("start refused until heavy work is OK'd", wait_for("document.querySelector('#toast').textContent.includes('OK for this episode')"))
        check("dialog stays open", js("document.querySelector('#episodemodal').classList.contains('open')"))
        js("document.querySelector('#episodeclose').click()")
        js("""{ const b = document.createElement('div'); b.id = 'epdemo'; document.body.appendChild(b);
             renderEpisode(b, {state: 'needs_you', spec: {name: 'Ep 1'}, master: null, tasks: [{id: 'visuals', rounds: 1, tokens: 1200}],
               stages: [{id: 'script', title: 'Script', status: 'passed', detail: '120 spoken words'},
                        {id: 'voice', title: 'Voiceover', status: 'passed', detail: '24 s', report: 'gates/voice-1.json'},
                        {id: 'visuals', title: 'Visuals', status: 'needs_you', detail: 'still failing'},
                        {id: 'edit', title: 'Edit', status: 'waiting', detail: ''}], notes: ['Visuals: check failed']}, 'x', null); }""")
        txt = (js("document.querySelector('#epdemo').innerText") or "").lower()      # status chips are upper-cased by CSS
        check("episode panel shows each stage", all(x in txt for x in ("script", "passed", "needs you", "waiting", "review 1", "gate report")), txt[:200])
        js("document.querySelector('#epdemo').remove()")
        js("""{ const b = document.createElement('div'); b.id = 'epwait'; document.body.appendChild(b);
             renderEpisode(b, {state: 'waiting_render', spec: {name: 'Ep 1'}, master: null, tasks: [], result: {stage: 'edit', project: 'Ep 1 - One Production 1'},
               stages: [{id: 'visuals', title: 'Visuals (Fusion)', status: 'passed', detail: '5 Fusion shots'},
                        {id: 'edit', title: 'Render', status: 'waiting', detail: 'Render the timeline'}], notes: []}, state.current, null); }""")
        check("waiting episode asks for the render and offers the project's videos",
              wait_for("document.querySelector('#epwait [data-rendered]') && document.querySelector('#epwait').innerText.includes('Ep 1 - One Production 1')"
                       " && document.querySelector('#epwait [data-video]').innerText.includes('No video in this project yet')", 10),
              (js("document.querySelector('#epwait').innerText") or "")[:200])
        js("document.querySelector('#epwait').remove()")

    # accounts (P2) - only when the server runs against the fake CLIs
    if os.environ.get("UI_DRIVE_ACCOUNTS") == "1":
        check("only Demo in picker at start", js("[...document.querySelectorAll('#picker option')].map(o=>o.value).join()") == "demo")
        js("document.querySelector('#accbtn').click()")
        check("accounts panel opens", wait_for("document.querySelector('#accmodal').classList.contains('open')"))
        check("empty-state shown", "No accounts yet" in (js("document.querySelector('#acclist').innerText") or ""))
        shot("08-accounts-empty")

        def add_cli(prov, label):
            js(f"document.querySelector('.tile[data-prov=\"{prov}\"]').click()")
            js(f"document.querySelector('#addlabel').value='{label}'; document.querySelector('#addform').requestSubmit()")
            return wait_for(f"[...document.querySelectorAll('.acc')].some(a=>a.innerText.includes('{label}') && a.querySelector('.stat.ok'))", 20)

        check("Claude login signs in", add_cli("claude", "Main Claude"))
        check("email read from Claude", "friend@example.com" in (js("document.querySelector('#acclist').innerText") or ""))
        check("Codex login signs in", add_cli("codex", "My Codex"))
        check("API key tile asks for key", js("document.querySelector('.tile[data-prov=\"anthropic_api\"]').click(); getComputedStyle(document.querySelector('#keyrow')).display") != "none")
        check("API key tile has no token box", js("getComputedStyle(document.querySelector('#tokrow')).display") == "none")
        check("Claude login tile has no token box", js("document.querySelector('.tile[data-prov=\"claude\"]').click(); getComputedStyle(document.querySelector('#tokrow')).display") == "none")
        check("OAuth token tile asks for the token", js("document.querySelector('.tile[data-prov=\"claude_token\"]').click(); getComputedStyle(document.querySelector('#tokrow')).display") != "none"
              and js("getComputedStyle(document.querySelector('#keyrow')).display") == "none"
              and "setup-token" in (js("document.querySelector('#addnote').innerText") or ""))
        js("document.querySelector('#addlabel').value='Token Claude'; document.querySelector('#addtoken').value='sk-ant-oat01-"
           + "UIdriveFAKEtoken" * 5 + "'; document.querySelector('#addform').requestSubmit()")
        check("OAuth token tile adds a ready Claude account", wait_for(
            "[...document.querySelectorAll('.acc')].some(a=>a.innerText.includes('Token Claude') && a.querySelector('.stat.ok') && a.innerText.includes('sign-in token'))", 15),
            (js("[...document.querySelectorAll('.acc')].map(a=>a.innerText.split('\\n').slice(0,3).join(' ')).join(' | ')") or "")[:300])
        js("document.querySelector('.tile[data-prov=\"claude\"]').click()")
        check("Claude cards offer 'Use a token'", js("[...document.querySelectorAll('.acc')].some(a=>a.innerText.includes('Main Claude') && a.querySelector('[data-act=token]'))"))
        js("document.querySelector('#addcancel').click()")
        shot("09-accounts-signed-in")
        js("document.querySelector('#accclose').click()")

        opts = js("[...document.querySelectorAll('#picker option')].map(o=>o.textContent).join(' | ')") or ""
        check("picker lists new accounts", "Main Claude" in opts and "My Codex" in opts, opts)
        check("picker moves off Demo to a ready account", js("document.querySelector('#picker').value") != "demo",
              js("document.querySelector('#picker').value"))
        # a fresh page load with accounts already set up must not default to Demo either.
        # (Go via about:blank: navigating to the same URL with only a new #fragment is not a
        # page load in Chrome, so the app would never run its startup code.)
        send("Page.navigate", url="about:blank"); time.sleep(0.3)
        send("Page.navigate", url=LAUNCH)
        wait_for("getComputedStyle(document.querySelector('#app')).display==='grid'")
        wait_for("document.querySelectorAll('#picker option').length>1", 10)
        time.sleep(1.5)     # let the background account check finish and re-render
        check("after reload, picker defaults to a ready account", js("document.querySelector('#picker').value") != "demo",
              js("document.querySelector('#picker option:checked').textContent"))
        js("[...document.querySelectorAll('#picker option')].find(o=>o.textContent.includes('Main Claude')).selected=true; document.querySelector('#picker').dispatchEvent(new Event('change'))")
        js("document.querySelector('#projects li[data-id]').click()")
        check("message box locked while project loads", js("document.querySelector('#input').disabled"))
        wait_for("!document.querySelector('#input').disabled")
        n0 = js("document.querySelectorAll('.row.assistant').length")
        js("document.querySelector('#input').value='Write a hook about Varosha'; document.querySelector('#composer').requestSubmit()")
        got = wait_for(f"document.querySelectorAll('.row.assistant').length>{n0} && !document.querySelector('#send').disabled", 20)
        last = js("[...document.querySelectorAll('.row.assistant .bubble')].pop().innerText") or ""
        check("Claude account answers in chat", got and "Fake Claude says: Write a hook about Varosha" in last, last[:80])
        meta = js("[...document.querySelectorAll('.meta-line')].pop()?.innerText") or ""
        check("reply shows who answered", "Main Claude" in meta, meta)
        shot("10-chat-with-claude")

        js("[...document.querySelectorAll('#picker option')].find(o=>o.textContent.includes('My Codex')).selected=true")
        n1 = js("document.querySelectorAll('.row.assistant').length")
        js("document.querySelector('#input').value='Now outline it'; document.querySelector('#composer').requestSubmit()")
        wait_for(f"document.querySelectorAll('.row.assistant').length>{n1} && !document.querySelector('#send').disabled", 20)
        last = js("[...document.querySelectorAll('.row.assistant .bubble')].pop().innerText") or ""
        check("switching account mid-project works", "Fake Codex says: Now outline it" in last, last[:80])

        js("document.querySelector('#accbtn').click()")
        wait_for("document.querySelector('#accmodal').classList.contains('open')")
        meters = js("document.querySelector('#acclist').innerText") or ""
        util = round(float(os.environ.get("FAKE_UTIL", "0.42")) * 100)
        check(f"exact Claude usage shown ({util}% / 10%)", f"{util}%" in meters and "10%" in meters)
        shot("11-accounts-usage")
        js("document.querySelector('#accclose').click()")

    # switching + usage page (P3) - server runs with fake Claude at FAKE_UTIL >= 0.85
    if os.environ.get("UI_DRIVE_SWITCH") == "1":
        # The Claude reply above crossed the threshold, so a switch card should be in the chat.
        js("document.querySelector('#projects li[data-id]').click()")
        wait_for("!document.querySelector('#input').disabled")
        js("[...document.querySelectorAll('#picker option')].find(o=>o.textContent.includes('Main Claude')).selected=true;"
           "document.querySelector('#picker').dispatchEvent(new Event('change'))")
        js("document.querySelector('#input').value='Draft the Pripyat cold open'; document.querySelector('#composer').requestSubmit()")
        check("switch card appears when Claude crosses 85%", wait_for("!!document.querySelector('.offer:not(.used)')", 20))
        card = js("document.querySelector('.offer:not(.used)').innerText") or ""
        check("card names the account and its usage", "Main Claude" in card and "87%" in card, card[:120])
        check("card offers Codex", "Continue on Codex" in card)
        shot("12-switch-offer")

        js("document.querySelector('.offer:not(.used) [data-view]').click()")
        check("handoff opens and has the conversation", wait_for(
            "document.querySelector('#homodal').classList.contains('open') && "
            "document.querySelector('#hobody').innerText.includes('Draft the Pripyat cold open')", 8))
        shot("13-handoff")
        js("document.querySelector('#hoclose').click()")

        n = js("document.querySelectorAll('.row.assistant').length")
        js("[...document.querySelectorAll('.offer:not(.used) [data-to]')].find(b=>b.innerText.includes('Codex')).click()")
        got = wait_for(f"document.querySelectorAll('.row.assistant').length>{n} && !document.querySelector('#send').disabled", 20)
        last = js("[...document.querySelectorAll('.row.assistant .bubble')].pop().innerText") or ""
        check("Codex carries on after the switch", got and "Fake Codex says" in last, last[:80])
        check("'Continued on' divider shown", "Continued on My Codex" in (js("document.querySelector('.thread').innerText") or ""))
        check("picker now on Codex", "My Codex" in (js("document.querySelector('#picker option:checked').textContent") or ""))
        shot("14-after-switch")

        # usage page
        js("document.querySelector('#usagebtn').click()")
        check("usage page opens", wait_for("document.querySelector('#usagemodal').classList.contains('open') && document.querySelectorAll('.ucard').length>=2", 8))
        utext = js("document.querySelector('#usagemodal').innerText") or ""
        check("Claude card EXACT 87%", "EXACT" in utext and "87% used" in utext)
        check("Codex card EXACT from its session file (35%)", "35% used" in utext and utext.count("EXACT") >= 2)
        check("switch log has the hand-over", "Main Claude" in (js("document.querySelector('#switchlog').innerText") or "")
              and "threshold" in (js("document.querySelector('#switchlog').innerText") or ""))
        shot("15-usage")
        # reorder: move Codex to the top
        first_before = js("document.querySelector('.ucard .aname').innerText")
        js("[...document.querySelectorAll('.ucard')].find(c=>c.innerText.includes('My Codex')).querySelector('[data-move=\"-1\"]').click()")
        check("reorder moves Codex up", wait_for("document.querySelector('.ucard .aname').innerText.includes('My Codex')", 8), first_before)
        # range tabs
        js("document.querySelector('#rangetabs [data-r=\"7d\"]').click()")
        check("range tab switches", wait_for("document.querySelector('#rangetabs [data-r=\"7d\"]').classList.contains('on')", 5))
        shot("16-usage-after-edits")
        js("document.querySelector('#usageclose').click()")

        # manual switch button
        js("document.querySelector('#switchbtn').click()")
        check("manual switch shows a card with 'not now'", wait_for(
            "[...document.querySelectorAll('.offer:not(.used)')].some(o=>o.querySelector('[data-dismiss]'))", 8))
        js("document.querySelector('.offer:not(.used) [data-dismiss]').click()")
        check("'not now' dismisses it", not js("!!document.querySelector('.offer:not(.used) [data-dismiss]')"))

    # voiceover (P4) - server runs against the stand-in voice app
    if os.environ.get("UI_DRIVE_VOICE") == "1":
        js("document.querySelector('#newbtn').click(); document.querySelector('#projname').value='Voice test';"
           " document.querySelector('#newform').requestSubmit()")
        wait_for("document.querySelector('#crumb').innerText.includes('Voice test') && !document.querySelector('#input').disabled")
        script = OUT / "ghost-towns-ep1.txt"
        script.write_text("## Pripyat\n\nPripyat is an abandoned city in northern Ukraine.\n\n"
                          "## Hashima\n\nHashima is an island off Nagasaki.\n", encoding="utf-8")
        doc = send("DOM.getDocument")["root"]["nodeId"]
        node = send("DOM.querySelector", nodeId=doc, selector="#fileinput")["nodeId"]
        send("DOM.setFileInputFiles", nodeId=node, files=[str(script)])
        check("script file shows a voice button", wait_for("!!document.querySelector('#files .fvoice')", 10))
        js("document.querySelector('#files .fvoice').click()")
        check("voice dialog opens with the script picked", wait_for(
            "document.querySelector('#voicemodal').classList.contains('open') && "
            "document.querySelector('#vsrc') && document.querySelector('#vsrc').value==='ghost-towns-ep1.txt'", 30))
        check("dialog shows spoken-word count (titles not counted)",
              wait_for("document.querySelector('#vstats').innerText.includes('14 spoken words')", 8),
              js("document.querySelector('#vstats').innerText"))
        check("voices come from the voice app", "human_ref48.wav" in (js("document.querySelector('#vvoice').innerText") or ""))
        for scheme in ("dark", "light"):                  # user report: dropdown text unreadable in dark mode
            send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])
            time.sleep(0.2)
            bad = js("""(()=>{const ink=getComputedStyle(document.body).color;
  return [...document.querySelectorAll('#voicemodal select, #voicemodal input:not([type=checkbox]):not([type=file]), #picker')]
    .filter(e=>getComputedStyle(e).color!==ink).map(e=>e.id||e.tagName)})()""")
            check(f"dialog text readable in {scheme} mode", bad == [], ", ".join(bad or []))
            if scheme == "dark":
                shot("17a-voice-dialog-dark")
        js("{const s=document.querySelector('#vvoice'); s.value='human_ref48.wav'; s.dispatchEvent(new Event('change'))}")
        js("document.querySelector('#vplay').click()")
        check("play button plays the chosen voice", wait_for(
            "voiceSample.playing==='human_ref48.wav' && !voiceSample.audio.paused && voiceSample.audio.currentTime>0", 8),
            js("JSON.stringify({p: voiceSample.playing, paused: voiceSample.audio.paused, err: String(voiceSample.audio.error && voiceSample.audio.error.code)})"))
        check("button turns into stop", js("document.querySelector('#vplay').textContent") == "■")
        js("{const s=document.querySelector('#vvoice'); s.value='buster_ref48.wav'; s.dispatchEvent(new Event('change'))}")
        check("picking another voice switches the sample", wait_for(
            "voiceSample.playing==='buster_ref48.wav' && voiceSample.audio.src.startsWith('blob:')", 8))
        shot("17-voice-dialog")
        js("document.querySelector('#vplay').click()")
        check("stop button stops it", js("voiceSample.audio.paused && voiceSample.playing===null") is True)
        js("document.querySelector('#vnew').click()")
        check("+ opens the clone panel", js("!document.querySelector('#vclone').hidden") is True)
        js("document.querySelector('#cname').value='Narrator 2'")
        js("{const dt=new DataTransfer(); dt.items.add(new File([new Uint8Array(64)], 'my take.wav', {type:'audio/wav'}));"
           "document.querySelector('#cfile').files=dt.files}")
        js("document.querySelector('#cgo').click()")
        check("clone refused until permission is ticked", wait_for(
            "document.querySelector('#cmsg').innerText.includes('permission')", 5))
        js("document.querySelector('#cconsent').checked=true; document.querySelector('#cgo').click()")
        check("new voice added, selected and playing", wait_for(
            "document.querySelector('#vvoice').value==='Narrator 2_ref48.wav' && voiceSample.playing==='Narrator 2_ref48.wav'", 10),
            js("document.querySelector('#cmsg').innerText"))
        shot("17b-voice-cloned")
        js("document.querySelector('#vplay').click(); document.querySelector('#ccancel').click()")
        js("{const s=document.querySelector('#vvoice'); s.value='human_ref48.wav'; s.dispatchEvent(new Event('change'))}")
        js("document.querySelector('#vgo').click()")
        check("GPU approval asked before anything runs", wait_for(
            "document.querySelector('#vapprove').innerText.includes('needs your OK')", 8))
        shot("18-voice-approval")
        js("document.querySelector('#vapprove [data-a=\"always\"]').click()")
        check("dialog closes and a voice card appears", wait_for(
            "!document.querySelector('#voicemodal').classList.contains('open') && !!document.querySelector('.vcard')", 10))
        check("card shows progress per section", wait_for("document.querySelectorAll('.vcard .vsec').length===2", 15))
        # user report: left the project mid-render, came back, the card was gone
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Empty one')).click()")
        time.sleep(0.4)
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Voice test')).click()")
        check("running voiceover card comes back after switching projects", wait_for(
            "document.querySelectorAll('.vcard').length===1", 8))
        check("card finishes", wait_for("document.querySelector('.vcard').classList.contains('done')", 30))
        card = js("document.querySelector('.vcard').innerText") or ""
        check("card shows length, words, pace", "1:01" in card and "160 words" in card and "157 wpm" in card, card[:120])
        check("mp3, wav and srt listed on the card",
              all(x in card for x in ("ghost-towns-ep1.mp3", "ghost-towns-ep1.wav", "ghost-towns-ep1.srt")))
        check("player present", js("!!document.querySelector('.vcard audio')"))
        check("files appear in the project", wait_for(
            "['ghost-towns-ep1.mp3','ghost-towns-ep1.wav','ghost-towns-ep1.srt'].every(n=>"
            "[...document.querySelectorAll('#files .fname')].some(e=>e.innerText===n))", 10))
        shot("19-voice-done")
        # second run: already approved, so no question
        js("document.querySelector('#micbtn').click()")
        wait_for("!!document.querySelector('#vgo')", 15)
        js("document.querySelector('#vgo').click()")
        check("'Always allow' remembered - no second question", wait_for(
            "!document.querySelector('#voicemodal').classList.contains('open') && document.querySelectorAll('.vcard').length===2", 10))
        wait_for("[...document.querySelectorAll('.vcard')].every(c=>c.classList.contains('done'))", 30)
        check("second render gets its own file names", wait_for(
            "[...document.querySelectorAll('#files .fname')].some(e=>e.innerText==='ghost-towns-ep1 (1).mp3')", 10))
        # cards come back after reloading the project
        js("document.querySelector('#projects li[data-id]:nth-child(2)') && document.querySelector('#projects li[data-id]:nth-child(2)').click()")
        time.sleep(0.5)
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Voice test')).click()")
        check("voice cards restored from history", wait_for(
            "document.querySelectorAll('.vcard.done').length===2", 15))

        # the assistant proposes a voiceover; the user runs it from the card
        before = js("document.querySelectorAll('.vcard').length")
        js("{const b=addMsg('assistant','Sure - 165 wpm with Buster.');"
           "addActionCard(b,{tool:'voiceover',file:'ghost-towns-ep1.txt',voice:'buster_ref48.wav',"
           "pace_mode:'wpm',target_wpm:165,preset:'clone_exact'},12345,false)}")
        card = js("document.querySelector('.acard:last-of-type').innerText") or ""
        check("assistant's proposal shows as a Run card", "buster_ref48.wav" in card and "165 words per minute" in card, card[:120])
        shot("21-action-card")
        js("document.querySelector('.acard:last-of-type [data-a=run]').click()")
        if wait_for("document.querySelector('.acard:last-of-type').innerText.includes('needs your OK')", 3):
            js("document.querySelector('.acard:last-of-type [data-a=once]').click()")
        check("Run starts the voiceover", wait_for(
            f"document.querySelectorAll('.vcard').length==={before + 1} && "
            "document.querySelector('.acard:last-of-type').innerText.includes('Started')", 10))
        check("that voiceover finishes", wait_for(
            f"document.querySelectorAll('.vcard.done').length==={before + 1}", 30))

        # delete a project: inline confirm, Keep backs out, Delete removes it
        names = lambda: js("[...document.querySelectorAll('#projects li[data-id] .pname')].map(e=>e.innerText)") or []
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Empty one')).querySelector('.pdel').click()")
        check("delete asks first (inline, no browser dialog)", wait_for(
            "!!document.querySelector('#projects li.confirm') && document.querySelector('#projects li.confirm').innerText.includes('trash')", 5))
        shot("20-delete-confirm")
        js("document.querySelector('#projects li.confirm [data-a=keep]').click()")
        check("Keep backs out", wait_for("!document.querySelector('#projects li.confirm')", 5) and "Empty one" in names())
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Empty one')).querySelector('.pdel').click()")
        wait_for("!!document.querySelector('#projects li.confirm')", 5)
        js("document.querySelector('#projects li.confirm [data-a=del]').click()")
        check("Delete removes the project", wait_for(
            "!document.querySelector('#projects li.confirm') && "
            "![...document.querySelectorAll('#projects .pname')].some(e=>e.innerText==='Empty one')", 5), ", ".join(names()))
        check("the open project is untouched", "Voice test" in names() and
              js("document.querySelectorAll('.vcard.done').length") == before + 1)

    # agent mode (P5) - server runs with a scripted agent (tests/p5/test_ui_agent.py)
    if os.environ.get("UI_DRIVE_AGENT") == "1":
        go = lambda name: js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('%s')).click()" % name)
        # set up here, after the standard steps, so they still run against Demo in an empty app
        js("""(async()=>{
          const j = {method:'POST', headers:{'Content-Type':'application/json'}};
          const a = await (await api('/api/accounts', {...j, body: JSON.stringify({provider:'claude'})})).json();
          await api(`/api/accounts/${a.id}/login`, {method:'POST'});
          for (let i = 0; i < 60; i++) {
            const c = await (await api(`/api/accounts/${a.id}/check`, {method:'POST'})).json();
            if (c.status === 'ok') break;
            await new Promise(r => setTimeout(r, 100));
          }
          const p = await (await api('/api/projects', {...j, body: JSON.stringify({name:'Agent test'})})).json();
          const fd = new FormData(); fd.append('files', new File(['## Hook\\nDebt is a machine.\\n'], 'ep1.md'));
          await api(`/api/projects/${p.id}/files`, {method:'POST', body: fd});
          window._agentAcc = a.id; await loadProjects();
        })()""")
        check("agent account signed in", wait_for("!!window._agentAcc", 15))
        acc_id = js("window._agentAcc")
        go("Agent test")
        wait_for("!document.querySelector('#input').disabled", 8)
        js(f"loadAccounts().then(()=>{{const p=document.querySelector('#picker'); p.value='{acc_id}'; p.dispatchEvent(new Event('change'))}})")
        time.sleep(0.6)
        send_msg = lambda t: js("document.querySelector('#input').value=%s; document.querySelector('#composer').requestSubmit()" % json.dumps(t))

        # turn 1: edits inside the project freely, asks before a command
        send_msg("Tighten the hook and check the audio")
        check("agent shows its steps live", wait_for("document.querySelectorAll('.bubble .step').length>=2", 10))
        check("Stop button shown while it works", js("document.querySelector('#stop').classList.contains('on')") is True)
        check("command needs approval", wait_for("!!document.querySelector('.askcard')", 10),
              js("(document.querySelector('.askcard')||{}).innerText||''"))
        card = js("document.querySelector('.askcard').innerText") or ""
        check("approval card shows the exact command", "ffprobe hook.mp3" in card and "runs a command" in card, card[:120])
        shot("22-agent-ask")
        js("[...document.querySelectorAll('.askcard button')].find(b=>b.innerText==='Deny').click()")
        check("Deny is recorded on the card", wait_for("document.querySelector('.askcard').innerText.includes('Denied')", 8))
        check("turn finishes", wait_for("!document.querySelector('#stop').classList.contains('on')", 10))
        check("edit inside the project ran without asking", js(
            "[...document.querySelectorAll('.bubble .step')].some(s=>s.innerText.includes('Write') && s.classList.contains('ok'))") is True)
        check("denied command marked as failed", js(
            "[...document.querySelectorAll('.bubble .step')].some(s=>s.innerText.includes('Run command') && s.classList.contains('bad'))") is True)
        check("new file appears in Files", wait_for("document.querySelector('#files').innerText.includes('hook.md')", 8))
        shot("23-agent-done")

        # turn 2: leave while it works, come back: it re-attaches and replays
        send_msg("Write the outro")
        wait_for("document.querySelector('#stop').classList.contains('on')", 5)
        time.sleep(0.4)
        go("Ghost Towns")
        time.sleep(0.6)
        check("other project usable while the agent works", js("document.querySelector('#send').disabled") is False or
              js("document.querySelector('#input').disabled") is False)
        go("Agent test")
        check("coming back re-attaches to the live run", wait_for(
            "[...document.querySelectorAll('.bubble .seg')].some(s=>s.innerText.includes('Outro draft one'))", 10))
        check("re-attached run finishes", wait_for(
            "[...document.querySelectorAll('.bubble .seg')].some(s=>s.innerText.includes('Outro done'))", 15))
        wait_for("!document.querySelector('#stop').classList.contains('on')", 10)

        # turn 3: Stop
        send_msg("Do something long")
        wait_for("document.querySelector('#stop').classList.contains('on')", 5)
        wait_for("[...document.querySelectorAll('.bubble .seg')].some(s=>s.innerText.includes('Starting a long job'))", 8)
        js("document.querySelector('#stop').click()")
        check("Stop ends the run", wait_for("!document.querySelector('#stop').classList.contains('on')", 10))
        check("what it said before Stop is kept", wait_for(
            "[...document.querySelectorAll('.row.assistant')].pop().innerText.includes('Starting a long job')", 5))

        # history after reload of the project keeps the steps
        go("Ghost Towns")
        time.sleep(0.4); go("Agent test")
        check("steps kept in history (done and denied)", wait_for(
            "document.querySelectorAll('.bubble .step.ok').length>=1 && document.querySelectorAll('.bubble .step.bad').length>=1", 8))
        shot("24-agent-history")

    # model + effort pickers, team mode, spend caps (P9)
    if os.environ.get("UI_DRIVE_P9") == "1":
        go = lambda name: js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('%s')).click()" % name)
        js("""(async()=>{
          const j = {method:'POST', headers:{'Content-Type':'application/json'}};
          const a = await (await api('/api/accounts', {...j, body: JSON.stringify({provider:'claude'})})).json();
          await api(`/api/accounts/${a.id}/login`, {method:'POST'});
          for (let i = 0; i < 60; i++) {
            const c = await (await api(`/api/accounts/${a.id}/check`, {method:'POST'})).json();
            if (c.status === 'ok') break;
            await new Promise(r => setTimeout(r, 100));
          }
          await api('/api/projects', {...j, body: JSON.stringify({name:'Team test'})});
          window._teamAcc = a.id; await loadProjects();
        })()""")
        check("team account signed in", wait_for("!!window._teamAcc", 15))
        acc_id = js("window._teamAcc")
        go("Team test")
        wait_for("!document.querySelector('#input').disabled", 8)
        js(f"loadAccounts().then(()=>{{const p=document.querySelector('#picker'); p.value='{acc_id}'; p.dispatchEvent(new Event('change'))}})")
        check("model + effort pickers shown for a Claude account", wait_for(
            "getComputedStyle(document.querySelector('#mpick')).display!=='none' && document.querySelector('#modelpick').options.length===4", 8))
        labels = js("[...document.querySelectorAll('#modelpick option')].map(o=>o.text).join('|')") or ""
        check("models: Opus 5.5, Sonnet 5.5, Haiku 4.5, Fable 5.1", all(m in labels for m in ("Opus 5.5", "Sonnet 5.5", "Haiku 4.5", "Fable 5.1")), labels)
        efforts = js("[...document.querySelectorAll('#effortpick option')].map(o=>o.value).join(',')")
        check("efforts: auto, low..max", efforts == "auto,low,medium,high,xhigh,max", efforts)
        js("{const m=document.querySelector('#modelpick'); m.value='opus'; m.dispatchEvent(new Event('change'))}")
        time.sleep(0.5)
        js("{const e=document.querySelector('#effortpick'); e.value='xhigh'; e.dispatchEvent(new Event('change'))}")
        check("choice saved on the account", wait_for(
            f"api('/api/accounts/{acc_id}/models').then(r=>r.json()).then(c=>c.model==='opus'&&c.effort==='xhigh')", 8))
        js("{const m=document.querySelector('#modelpick'); m.value='haiku'; m.dispatchEvent(new Event('change'))}")
        check("Haiku has no effort setting", wait_for("document.querySelector('#effortpick').disabled", 5))
        shot("40-model-effort")

        js("document.querySelector('#teambtn').click()")
        check("team toggle on, settings chip shows Opus -> Sonnet", wait_for(
            "document.querySelector('#teambtn').classList.contains('on') && document.querySelector('#teamchip').innerText.includes('opus·high → sonnet·medium ×3')", 6),
            js("document.querySelector('#teamchip').innerText"))
        js("document.querySelector('#teamchip').click()")
        check("team settings open", wait_for("document.querySelector('#teammodal').classList.contains('open') && !!document.querySelector('#twe')", 6))
        shot("41-team-settings")
        js("document.querySelector('#twe').value='low'; document.querySelector('#tsave').click()")
        check("team settings saved", wait_for("document.querySelector('#teamchip').innerText.includes('sonnet·low')", 6),
              js("document.querySelector('#teamchip').innerText"))

        js("document.querySelector('#input').value='Plan episode 2'; document.querySelector('#composer').requestSubmit()")
        check("team panel appears", wait_for("!!document.querySelector('.bubble .teampanel .trow')", 10))
        check("worker row shows it working", wait_for(
            "[...document.querySelectorAll('.teampanel .tst')].some(s=>s.classList.contains('running')||s.classList.contains('review'))", 10))
        shot("42-team-working")
        check("task ends done after the master's review", wait_for(
            "[...document.querySelectorAll('.teampanel .tst')].some(s=>s.classList.contains('done'))", 15))
        check("final review shown", wait_for("[...document.querySelectorAll('.bubble .seg.final')].some(s=>s.innerText.includes('Final review'))", 15))
        wait_for("!document.querySelector('#stop').classList.contains('on')", 10)
        check("worker's file appears in Files", wait_for("document.querySelector('#files').innerText.includes('notes.md')", 8))
        shot("43-team-done")
        go("Ghost Towns"); time.sleep(0.4); go("Team test")
        check("team run kept in history", wait_for(
            "!!document.querySelector('.bubble .teampanel') && document.querySelector('#messages').innerText.includes('TEAM GOAL')", 8))
        # your own parts + more workers than 3
        js("document.querySelector('#teambody').innerHTML=''; document.querySelector('#teamchip').click()")
        wait_for("!!document.querySelector('#tmw')", 6)
        js("{const i=document.querySelector('#tmw'); i.value='5'; i.dispatchEvent(new Event('input'))}")
        check("more than 3 workers shows a warning", "5 workers at once" in (js("document.querySelector('#tmwwarn').innerText") or ""))
        shot("45-team-five")
        js("document.querySelector('#tsave').click()")
        check("5 workers saved", wait_for("document.querySelector('#teamchip').innerText.includes('×5')", 6),
              js("document.querySelector('#teamchip').innerText"))
        js("document.querySelector('#partsbtn').click()")
        check("parts dialog opens with 3 empty parts", wait_for(
            "document.querySelector('#partsmodal').classList.contains('open') && document.querySelectorAll('#prows .prow').length===3", 6))
        js("""{const rows=document.querySelectorAll('#prows .prow');
              const set=(r,f,v)=>{const e=r.querySelector(`[data-f=${f}]`); e.value=v; e.dispatchEvent(new Event('input'))};
              set(rows[0],'title','Hooks'); set(rows[0],'brief','Write hooks.md'); set(rows[0],'files','hooks.md');
              set(rows[1],'title','Titles'); set(rows[1],'brief','Write titles.md'); set(rows[1],'files','HOOKS.md');
              rows[2].querySelector('button').click();}""")
        check("same file in two parts is pointed out", "take turns" in (js("document.querySelector('#pwarn').innerText") or ""))
        js("{const r=document.querySelectorAll('#prows .prow')[1].querySelector('[data-f=files]'); r.value='titles.md'; r.dispatchEvent(new Event('input'))}")
        check("parts counted", "2 parts" in (js("document.querySelector('#pinfo').innerText") or ""))
        shot("46-parts")
        js("document.querySelector('#pstart').click()")
        check("team runs your parts", wait_for(
            "[...document.querySelectorAll('.bubble .teampanel')].pop().innerText.includes('your parts')", 10))
        check("both parts done", wait_for(
            "[...document.querySelectorAll('.bubble .teampanel')].pop().querySelectorAll('.tst.done').length===2", 20))
        wait_for("!document.querySelector('#stop').classList.contains('on')", 10)
        shot("47-parts-done")
        js("document.querySelector('#teambtn').click()")

        js("openUsage()")
        check("usage page shows spend caps", wait_for("!!document.querySelector('#ucards input[data-cap=cap_day_tokens]')", 8))
        js("{const i=document.querySelector('#ucards input[data-cap=cap_day_tokens]'); i.value='50000'; i.dispatchEvent(new Event('change'))}")
        check("daily cap saved", wait_for(
            f"api('/api/usage?range=today').then(r=>r.json()).then(u=>u.cards.find(c=>c.id==='{acc_id}').caps.cap_day_tokens===50000)", 8))
        check("usage by role shows master and worker", wait_for(
            "document.querySelector('#byrole').innerText.includes('Team master') && document.querySelector('#byrole').innerText.includes('Team worker')", 8))
        shot("44-usage-caps")
        js("closeUsage()")

    # Script Studio: competitors -> style guide -> FacelessOS script -> Opus review loop (P10)
    if os.environ.get("UI_DRIVE_P10") == "1":
        go = lambda name: js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('%s')).click()" % name)
        js("""(async()=>{
          const j = {method:'POST', headers:{'Content-Type':'application/json'}};
          const a = await (await api('/api/accounts', {...j, body: JSON.stringify({provider:'claude'})})).json();
          await api(`/api/accounts/${a.id}/login`, {method:'POST'});
          for (let i = 0; i < 60; i++) {
            const c = await (await api(`/api/accounts/${a.id}/check`, {method:'POST'})).json();
            if (c.status === 'ok') break;
            await new Promise(r => setTimeout(r, 100));
          }
          await api('/api/projects', {...j, body: JSON.stringify({name:'Script test'})});
          window._sAcc = a.id; await loadProjects();
        })()""")
        check("script account signed in", wait_for("!!window._sAcc", 15))
        go("Script test")
        wait_for("!document.querySelector('#input').disabled", 8)
        js("loadFiles()")
        js("document.querySelector('#scriptbtn').click()")
        check("New script dialog opens", wait_for("document.querySelector('#scriptmodal').classList.contains('open') && !!document.querySelector('#s_topic')", 8))
        check("FacelessOS built in by default", "built-in FacelessOS" in (js("document.querySelector('#s_fosst').textContent") or ""),
              js("document.querySelector('#s_fosst').innerText"))
        js("document.querySelector('.sset').open = true")
        js("document.querySelector('#s_fos').value=%s; document.querySelector('#s_save').click()" % json.dumps(os.environ["UI_FOS"]))
        check("own FacelessOS folder used instead", wait_for("document.querySelector('#s_fosst').innerText.includes('your FacelessOS folder')", 8),
              js("document.querySelector('#s_fosst').innerText"))
        js("""document.querySelector('#s_topic').value='The Lost City';
              document.querySelector('#s_fmt').value='true_crime';
              document.querySelector('#s_min').value='8';
              document.querySelector('#s_links').value='My rivals: https://www.youtube.com/@mrfacts and youtu.be/NOCAPTION01';""")
        shot("50-new-script")
        js("document.querySelector('#s_go').click()")
        check("Script Studio panel appears", wait_for("!!document.querySelector('.bubble .teampanel') && document.querySelector('.bubble .teampanel').innerText.includes('Script Studio')", 10))
        check("competitor captions read, one skipped", wait_for(
            "[...document.querySelectorAll('.teampanel')].pop().innerText.includes('4 transcripts') && [...document.querySelectorAll('.teampanel')].pop().innerText.includes('1 not transcribed')", 20))
        check("first review: score card with must-fix", wait_for(
            "[...document.querySelectorAll('.teampanel')].pop().querySelectorAll('.sround').length>=1 && [...document.querySelectorAll('.teampanel')].pop().innerText.includes('the hook takes 40 seconds')", 20))
        shot("51-script-round1")
        check("loops until the reviewer passes it", wait_for(
            "[...document.querySelectorAll('.teampanel')].pop().innerText.includes('Ready:') && [...document.querySelectorAll('.teampanel')].pop().querySelectorAll('.sround').length===2", 20))
        wait_for("!document.querySelector('#stop').classList.contains('on')", 10)
        check("script and style guide in Files", wait_for(
            "document.querySelector('#files').innerText.includes('the-lost-city') && document.querySelector('#files').innerText.includes('style-guide')", 8),
            (js("document.querySelector('#files').innerText") or "")[:300])
        shot("52-script-done")
        go("Ghost Towns"); time.sleep(0.4); go("Script test")
        check("Script Studio run kept in history", wait_for(
            "document.querySelector('#messages').innerText.includes('SCRIPT STUDIO') && document.querySelectorAll('.bubble .sround').length===2", 8))

    # backups, system scan, profile + PIN (P6)
    if os.environ.get("UI_DRIVE_P6") == "1":
        go = lambda name: js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('%s')).click()" % name)
        names = lambda: js("[...document.querySelectorAll('#projects li[data-id] .pname')].map(e=>e.innerText)") or []
        go("Ghost Towns"); time.sleep(0.5)
        files_before = js("document.querySelectorAll('#files li').length")
        js("document.querySelector('#bkbtn').click()")
        check("backups screen opens", wait_for("document.querySelector('#bkmodal').classList.contains('open')", 5))
        check("backup folder shown", wait_for("document.querySelector('#bkfolder').value.length>3", 5),
              js("document.querySelector('#bkfolder').value"))
        js("document.querySelector('#bknow').click()")
        check("Back up now makes a backup", wait_for("document.querySelectorAll('#bklist .bk').length===1", 15))
        check("backup lists every project", js("document.querySelectorAll('#bklist .bkp').length") == len(names()),
              f"{js('document.querySelectorAll(\'#bklist .bkp\').length')} rows, {len(names())} projects")
        shot("25-backups")
        js("document.querySelector('#bkclose').click()")
        # delete Ghost Towns, then bring it back from the backup
        js("[...document.querySelectorAll('#projects li[data-id]')].find(l=>l.innerText.includes('Ghost Towns')).querySelector('.pdel').click()")
        wait_for("!!document.querySelector('#projects li.confirm')", 5)
        js("document.querySelector('#projects li.confirm [data-a=del]').click()")
        # the inline confirm row hides the name at once; deleted = that row is gone too (the
        # DELETE finished), else the Backups screen can load first and still see the project
        check("project deleted", wait_for("!document.querySelector('#projects li.confirm') && "
                                          "![...document.querySelectorAll('#projects .pname')].some(e=>e.innerText.includes('Ghost Towns'))", 5))
        js("document.querySelector('#bkbtn').click()")
        check("backup shows it as deleted", wait_for(
            "[...document.querySelectorAll('#bklist .bkp')].some(r=>r.innerText.includes('Ghost Towns') && r.innerText.includes('DELETED'))", 8),
              js("[...document.querySelectorAll('#bklist .bkp')].map(r=>r.innerText).join(' | ')"))
        js("[...document.querySelectorAll('#bklist .bkp')].find(r=>r.innerText.includes('Ghost Towns')).querySelector('[data-a=restore]').click()")
        check("restore asks first (inline)", wait_for("!!document.querySelector('#bklist .bkp .ask')", 5))
        shot("26-restore-ask")
        js("document.querySelector('#bklist .bkp .ask [data-a=yes]').click()")
        check("project restored", wait_for("[...document.querySelectorAll('#projects .pname')].some(e=>e.innerText.includes('Ghost Towns'))", 10))
        js("document.querySelector('#bkclose').click()")
        go("Ghost Towns"); time.sleep(0.8)
        check("restored project has all its files", wait_for(f"document.querySelectorAll('#files li').length==={files_before}", 8),
              f"{js('document.querySelectorAll(\'#files li\').length')} vs {files_before}")
        check("restored chat history shown", wait_for("document.querySelectorAll('.row.user').length>=1", 5))

        # system scan
        js("document.querySelector('#sysbtn').click()")
        check("system scan shows this PC", wait_for("document.querySelectorAll('#sysbody .sysrow').length>=12", 30),
              str(js("document.querySelectorAll('#sysbody .sysrow').length")))
        check("every problem says what to do", js(
            "[...document.querySelectorAll('#sysbody .sysrow.warn, #sysbody .sysrow.bad')].every(r=>!!r.querySelector('.sf'))") is True)
        shot("27-system")
        js("document.querySelector('#sysclose').click()")

        # profile + PIN
        js("document.querySelector('#profbtn').click()")
        wait_for("!!document.querySelector('#pname')", 5)
        js("document.querySelector('#pname').value='Sam'; document.querySelector('#psave').click()")
        check("name shows in the corner", wait_for("document.querySelector('#profbtn').innerText==='S'", 5))
        js("document.querySelector('#pnew').value='4829'; document.querySelector('#pnew2').value='4829'; document.querySelector('#pset').click()")
        check("PIN set", wait_for("!!document.querySelector('#plock')", 5))
        shot("28-profile")
        js("document.querySelector('#plock').click()")
        check("Lock now shows the PIN screen", wait_for("document.querySelector('#pinlock').classList.contains('open')", 5))
        js("document.querySelector('#pininput').value='0000'; document.querySelector('#pinform').requestSubmit()")
        check("wrong PIN refused", wait_for("document.querySelector('#pinmsg').innerText.includes('wrong')", 5))
        shot("29-pin")
        js("document.querySelector('#pininput').value='4829'; document.querySelector('#pinform').requestSubmit()")
        check("right PIN unlocks", wait_for("!document.querySelector('#pinlock').classList.contains('open')", 5))
        go("Empty one"); time.sleep(0.5)
        check("app works again after unlock", wait_for("!document.querySelector('#input').disabled", 5))
        # remove the PIN so later checks (reload = locked page) are about the token, not the PIN
        js("document.querySelector('#profbtn').click()")
        wait_for("!!document.querySelector('#pcur')", 5)
        js("document.querySelector('#pcur').value='4829'; document.querySelector('#premove').click()")
        check("PIN removed", wait_for("!!document.querySelector('#pset')", 5))
        js("document.querySelector('#profclose').click()")

    # setup wizard (P7) - server runs with a stand-in installer (tests/p7/test_ui_setup.py)
    if os.environ.get("UI_DRIVE_SETUP") == "1":
        nxt = lambda: js("document.querySelector('#wnext').click()")
        on_step = lambda n: wait_for(f"document.querySelector('#wsteps .wstep.on') && document.querySelector('#wsteps .wstep.on').dataset.i==='{n}'", 8)
        js("document.querySelector('#sysbtn').click()")
        wait_for("document.querySelector('#sysmodal').classList.contains('open')", 5)
        js("document.querySelector('#syssetup').click()")
        check("setup reopens from System", wait_for("document.querySelector('#setup').classList.contains('open')", 5) and on_step(2))
        js("document.querySelector('#wsteps .wstep[data-i=\"1\"]').click()")
        check("scan step shows this PC", wait_for("document.querySelectorAll('#wbody .sysrow').length>=12", 30))
        nxt()
        check("modules step: core always on, voice suggested on the graphics card", on_step(2) and wait_for(
            "document.querySelector('.mod[data-id=voice] .opt.on') && document.querySelector('.mod[data-id=voice] .opt.on').dataset.mode==='gpu'", 8))
        check("later modules greyed", js("document.querySelectorAll('.mod.later').length") == 3)
        check("image assets module offers an install", js("!!document.querySelector('.mod[data-id=image] #imginstall')"))
        check("stock module offers a Pexels key field", js("!!document.querySelector('.mod[data-id=stock] #stockkey') && "
                                                          "!!document.querySelector('.mod[data-id=stock] #stocksave')"))
        shot("31-setup-modules")
        nxt()
        check("install step lists what's missing with size and source", on_step(3) and wait_for(
            "document.querySelectorAll('#wbody .inst').length===5 && document.querySelector('#wbody').innerText.includes('GB')", 10))
        js("document.querySelector('#winstall').click()")
        check("nothing installs without each OK", wait_for("document.querySelector('#wfootnote').innerText.includes('needs your OK')", 5))
        js("document.querySelector('#wallok').click()")
        shot("32-setup-install-ok")
        js("document.querySelector('#winstall').click()")
        check("install runs with progress", wait_for("document.querySelectorAll('#wbody .bar').length>=1 || document.querySelector('#wbody').innerText.includes('installed and checked')", 10))
        check("voice module installed and checked", wait_for("document.querySelector('#wbody').innerText.includes('installed and checked')", 30),
              (js("document.querySelector('#wbody').innerText") or "")[-300:])
        shot("33-setup-installed")
        nxt()
        check("accounts step explains the three kinds", on_step(4) and wait_for(
            "document.querySelector('#wbody').innerText.includes('Anthropic API key')", 8))
        nxt()
        check("can't continue without an account", wait_for("document.querySelector('#wfootnote').innerText.includes('Connect an account')", 5))
        js(f"window.TEST_KEY={json.dumps(os.environ.get('UI_DRIVE_TEST_KEY', ''))}")
        js("api('/api/accounts', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({provider:'anthropic_api', api_key: window.TEST_KEY})})"
           ".then(()=>showStep(4)).then(()=>window._accOk=1)")
        wait_for("window._accOk===1", 8)
        check("connected account listed", wait_for("document.querySelector('#wbody').innerText.includes('✓')", 8))
        nxt()
        check("voice step offers to add your own voice", on_step(5) and wait_for("!!document.querySelector('#wvadd')", 15))
        js("document.querySelector('#wvname').value='My narrator';"
           "{const dt=new DataTransfer(); dt.items.add(new File([new Uint8Array(64)], 'take.wav', {type:'audio/wav'}));"
           "document.querySelector('#wvfile').files=dt.files}")
        js("document.querySelector('#wvadd').click()")
        check("voice needs the permission tick", wait_for("document.querySelector('#wvmsg').innerText.includes('permission')", 5))
        js("document.querySelector('#wvok').checked=true; document.querySelector('#wvadd').click()")
        check("voice added", wait_for("document.querySelector('#wvmsg').innerText.includes('Added')", 8))
        js("document.querySelector('#wvtune').click()")
        check("tuning asks for the GPU first", wait_for("document.querySelector('#wvtunebox').innerText.includes('needs your OK')", 8))
        js("document.querySelector('#wvtunebox [data-a=once]').click()")
        check("tuning finds the best settings", wait_for("document.querySelector('#wvtunebox').innerText.includes('Best for')", 30),
              js("document.querySelector('#wvtunebox').innerText"))
        shot("34-setup-voice")
        nxt()
        check("test step", on_step(6) and wait_for("!!document.querySelector('#wtchat')", 8))
        js("document.querySelector('#wtchat').click()")
        check("test message answered", wait_for("document.querySelector('#wtchatout').innerText.includes('✓')", 20),
              js("document.querySelector('#wtchatout').innerText"))
        js("document.querySelector('#wtvoice').click()")
        check("15-word voice test plays", wait_for("!!document.querySelector('#wtvoiceout audio')", 30),
              js("document.querySelector('#wtvoiceout').innerText"))
        shot("35-setup-test")
        nxt()
        check("Finish closes setup", wait_for("!document.querySelector('#setup').classList.contains('open')", 8))
        check("setup remembered as done", js("fetch('/api/setup',{headers:{'X-OneProd-Token':TOKEN}}).then(r=>r.json()).then(j=>window._done=j.done)") is not None
              and wait_for("window._done===true", 5))

    # sample project, guide, updates panel, diagnostics (P8)
    if os.environ.get("UI_DRIVE_P8") == "1":
        js("state.current=null; renderWelcome()")
        check("start page offers the sample", wait_for("!!document.querySelector('.qcard[data-act=sample]')", 5))
        shot("36-start-sample")
        js("document.querySelector('.qcard[data-act=sample]').click()")
        check("sample project opens", wait_for("document.querySelector('#crumb').innerText.includes('Getting started')", 8))
        check("sample script and guide in Files", wait_for(
            "document.querySelector('#files').innerText.includes('Sample script') && document.querySelector('#files').innerText.includes('Start here')", 8))
        js("document.querySelector('#guidebtn').click()")
        check("guide opens", wait_for("document.querySelector('#guidemodal').classList.contains('open')", 5))
        check("guide covers approvals, voice, backups, updates, diagnostics", all(
            w in (js("document.querySelector('#guidemodal').innerText") or "") for w in
            ("Allow once", "LUFS", "Backups", "Roll back", "Export diagnostics")))
        shot("37-guide")
        js("document.querySelector('#guideclose').click()")
        js("document.querySelector('#sysbtn').click()")
        check("updates panel says this is a development copy", wait_for(
            "document.querySelector('#sysupd').innerText.includes('development copy')", 8))
        js("window._dl=null; const o=URL.createObjectURL; URL.createObjectURL=b=>{window._dl=b.size; return o(b)};"
           "document.querySelector('#sysdiag').click()")
        check("diagnostics zip downloads", wait_for("window._dl>1000", 30), str(js("window._dl")))
        shot("38-system-updates")
        js("document.querySelector('#sysclose').click()")

    # dark mode (Windows dark theme)
    send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    time.sleep(0.3); shot("06b-dark")
    send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])

    # toast must not overlap the composer
    js("toast('overlap test')"); time.sleep(0.35)
    ov = js("""(()=>{const a=document.querySelector('#toast').getBoundingClientRect(),
               b=document.querySelector('.composer').getBoundingClientRect();
               return !(a.bottom<b.top||a.top>b.bottom)})()""")
    check("toast clear of message box", not ov)

    # reload = locked
    send("Page.reload")
    time.sleep(1.2)
    check("reload shows lock screen", js("getComputedStyle(document.querySelector('#locked')).display")=="grid")
    shot("07-locked")

    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    ws.close()
finally:
    chrome.kill()
print(f"\n{sum(r[1] for r in results)}/{len(results)} passed")
sys.exit(0 if results and all(r[1] for r in results) else 1)
