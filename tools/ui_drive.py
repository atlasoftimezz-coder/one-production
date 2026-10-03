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
                           "--no-first-run", "--no-default-browser-check", "about:blank"],
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
        end = time.time() + timeout
        while time.time() < end:
            if js(expr): return True
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
