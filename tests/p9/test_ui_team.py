"""Real-browser check of P9: model + effort pickers, team toggle and settings, the live team
panel, final review, history, spend caps and usage by role. The app runs in this process (uvicorn in a
thread) so the scripted agent can stand in for the Claude Agent SDK."""
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

ROOT = Path(__file__).resolve().parents[2]


def test_team_flow_in_headless_chrome(tmp_path, monkeypatch):
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
    plan = '{"tasks": [{"title": "Notes", "brief": "Write notes.md", "files": ["notes.md"]}]}'
    script.turns = [
        [("text", plan)],                                                                       # master plans
        [("sleep", 1.5), ("tool", "Write", {"file_path": "notes.md", "content": "three ideas"}),
         ("text", "Wrote notes.md")],                                                           # worker
        [("text", '{"verdict": "pass", "notes": ""}')],                                          # master reviews
        [("text", "Final review: notes.md is ready.")],                                          # final word
        # the user's own two parts run side by side, so every turn says the same: 2 workers, 2 reviews, final
        *[[("text", '{"verdict": "pass"}')] for _ in range(5)],
    ]
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
                           env={**os.environ, "UI_DRIVE_P9": "1"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        server.should_exit = True
        th.join(10)
        keyring.set_keyring(previous)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(TOKEN, "<token>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:]
    assert "final review shown" in out
