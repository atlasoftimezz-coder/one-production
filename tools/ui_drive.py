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
