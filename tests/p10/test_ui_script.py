"""Real-browser check of Script Studio (P10): FacelessOS folder, competitor links, the live panel
with score cards, the review loop until it passes, history. Stand-in agent and yt-dlp."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import keyring
import pytest
import uvicorn

from app.config import Settings
from app.main import create_app
from tests.p2.conftest import FAKE, MemoryKeyring, api_handler
from tests.p4.conftest import TOKEN, FakeVoiceApp

from tests.p5.conftest import FakeAgent, Script
from tests.p10.test_competitors import FAKE as FAKE_YT, fake_facelessos

ROOT = Path(__file__).resolve().parents[2]


def test_script_studio_in_headless_chrome(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    bin_ = tmp_path / "bin"
    exe = bin_ / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    for tool in ("claude", "codex"):
        (bin_ / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{FAKE}" {tool} %*\r\n', encoding="utf-8")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls.txt"))
    monkeypatch.setenv("FAKE_EMAIL", "me@example.com")
    script = Script()
    passing = json.dumps({"scores": {k: 9 for k in ("hook", "structure", "retention", "rehooks", "anti_slop",
                                                    "authenticity", "style_match", "length", "outro")},
                          "must_fix": [], "notes": "ready to record", "verdict": "pass"})
    revise = json.dumps({"scores": {"hook": 5, "structure": 8, "retention": 7, "rehooks": 7, "anti_slop": 9,
                                    "authenticity": 9, "style_match": 8, "length": 6, "outro": 8},
                         "must_fix": ["the hook takes 40 seconds"], "notes": "cut the intro to one line"})
    script.turns = [
        [("tool", "Write", {"file_path": "competitors/style-guide.md", "content": "Fast, curious."}), ("text", "Style guide written.")],
        [("sleep", 1.0), ("tool", "Write", {"file_path": "scripts/the-lost-city.md", "content": "Slow hook. " * 40}), ("text", "Draft 1.")],
        [("sleep", 1.0), ("text", revise)],
        [("tool", "Write", {"file_path": "scripts/the-lost-city.md", "content": "Fast hook. " * 600}), ("text", "Draft 2.")],
        [("sleep", 1.0), ("text", passing)],
    ]
    monkeypatch.setenv("ONEPROD_YTDLP", str(FAKE_YT))
    fos = tmp_path / "Faceless os"
    fake_facelessos(fos)
    settings = Settings(data_dir=tmp_path / "data", claude_cmd=str(bin_ / "claude.cmd"), codex_cmd=str(bin_ / "codex.cmd"),
                        login_console=False, anthropic_base_url="https://api.test", log_level="warning")
    app = create_app(settings, token=TOKEN, api_transport=httpx.MockTransport(api_handler),
                     voice_transport=httpx.MockTransport(FakeVoiceApp(tmp_path / "vo").handler),
                     agent_client=lambda options: FakeAgent(options, script))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        end = time.time() + 15
        while not server.started and time.time() < end:
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"), f"{base}/#t={TOKEN}",
                            str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_P10": "1", "UI_FOS": str(fos)},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        server.should_exit = True
        th.join(10)
        keyring.set_keyring(previous)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(TOKEN, "<token>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:]
    assert "loops until the reviewer passes it" in out
