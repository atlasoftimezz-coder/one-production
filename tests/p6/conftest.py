"""P6 fixtures: One Production on a throwaway data folder, the stand-in voice app from P4,
and helpers to fill projects with files of every kind."""
import hashlib
import os
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p4.conftest import FakeVoiceApp

TOKEN = "test-session-token-p6-0123456789abcdef"
FAKES4 = Path(__file__).parents[1] / "p4" / "fakes"


@pytest.fixture
def make_client(tmp_path, monkeypatch, request):
    monkeypatch.setattr("app.voice.POLL", 0.05)
    voice_dir = tmp_path / "vo-app"
    voice_dir.mkdir(exist_ok=True)
    for f in ("server.py", "make_srt.py"):
        (voice_dir / f).write_bytes((FAKES4 / f).read_bytes())
    fake_voice = FakeVoiceApp(tmp_path / "vo-outputs")

    def make(data_dir=None, **app_kw):
        settings = Settings(data_dir=data_dir or tmp_path / "data", voice_app_dir=str(voice_dir),
                            voice_python=sys.executable, login_console=False)
        settings.ensure_dirs()
        app_kw.setdefault("backup_delay", 3600)
        app = create_app(settings, token=TOKEN, voice_transport=httpx.MockTransport(fake_voice.handler), **app_kw)
        c = TestClient(app, base_url="http://127.0.0.1")
        c.__enter__()
        request.addfinalizer(lambda: c.__exit__(None, None, None))
        c.headers[TOKEN_HEADER] = TOKEN
        c.data_dir = settings.data_dir
        c.fake = fake_voice
        return c
    return make


@pytest.fixture
def client(make_client):
    return make_client()


FILES = {
    "episode.docx": b"PK\x03\x04" + os.urandom(3000),
    "voice.mp3": b"ID3" + os.urandom(40000),
    "voice.wav": b"RIFF....WAVE" + os.urandom(60000),
    "thumb.png": b"\x89PNG\r\n\x1a\n" + os.urandom(5000),
    "notes.md": "## Hook\r\nPripyat · Hashima — ✓ (CRLF and accents kept exactly)\r\n".encode("utf-8"),
    "sub.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
}


def project_with_files(c, name="Ghost Towns", files=FILES):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    r = c.post(f"/api/projects/{pid}/files", files=[("files", (n, b)) for n, b in files.items()])
    assert r.status_code == 200, r.text
    c.post(f"/api/projects/{pid}/chat", json={"message": "Plan the cold open", "account_id": "demo"})
    return pid


def tree(folder: Path) -> dict:
    """relative path -> sha256 of every file under folder."""
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}
