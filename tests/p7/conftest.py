"""P7 fixtures: a local download server that honours Range requests (like python.org and
gyan.dev do), and One Production on a throwaway data folder with a chosen 'graphics card'."""
import hashlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p2.conftest import MemoryKeyring, api_handler
from tests.p4.conftest import FakeVoiceApp

TOKEN = "test-session-token-p7-0123456789abcdef"
FAKES4 = Path(__file__).parents[1] / "p4" / "fakes"
RTX5060TI = {"name": "NVIDIA GeForce RTX 5060 Ti", "vram_gb": 15.9, "driver": "591.86", "compute": "12.0",
             "blackwell": True}
GTX1060 = {"name": "NVIDIA GeForce GTX 1060 6GB", "vram_gb": 6.0, "driver": "472.12", "compute": "6.1",
           "blackwell": False}
RTX3060 = {"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0, "driver": "560.94", "compute": "8.6",
           "blackwell": False}


class Files(BaseHTTPRequestHandler):
    files: dict = {}
    log: list = []

    def do_GET(self):
        body = self.files.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        rng = self.headers.get("Range")
        self.log.append((self.path, rng))
        start = 0
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(body):
                self.send_response(416); self.end_headers(); return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    Files.files, Files.log = {}, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.files, srv.log = Files.files, Files.log
    yield srv
    srv.shutdown()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def make_app(tmp_path, monkeypatch, request):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    request.addfinalizer(lambda: keyring.set_keyring(previous))
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr("app.voice.POLL", 0.05)

    def make(gpu=RTX5060TI, external=False, data_dir=None, **kw):
        voice_dir = tmp_path / "vo-app"
        voice_dir.mkdir(exist_ok=True)
        for f in ("server.py", "make_srt.py"):
            (voice_dir / f).write_bytes((FAKES4 / f).read_bytes())
        fake_voice = FakeVoiceApp(tmp_path / "vo-outputs")
        settings = Settings(data_dir=data_dir or tmp_path / "data", login_console=False,
                            claude_cmd=str(tmp_path / "no-claude-here.exe"), codex_cmd=str(tmp_path / "no-codex.exe"),
                            anthropic_base_url="https://api.test",
                            **({"voice_app_dir": str(voice_dir), "voice_python": sys.executable} if external else {}))
        settings.ensure_dirs()
        app = create_app(settings, token=TOKEN, gpu_probe=lambda: gpu, backup_delay=3600,
                         api_transport=httpx.MockTransport(api_handler),
                         voice_transport=httpx.MockTransport(fake_voice.handler), **kw)
        c = TestClient(app, base_url="http://127.0.0.1")
        c.__enter__()
        request.addfinalizer(lambda: c.__exit__(None, None, None))
        c.headers[TOKEN_HEADER] = TOKEN
        c.data_dir, c.fake, c.tmp = settings.data_dir, fake_voice, tmp_path
        return c
    return make
