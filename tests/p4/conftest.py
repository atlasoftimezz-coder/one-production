import json
import shutil
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

TOKEN = "test-session-token-p4-0123456789abcdef"
FAKES = Path(__file__).parent / "fakes"


class FakeVoiceApp:
    """In-memory stand-in for the voiceover app's HTTP API."""

    def __init__(self, out_dir: Path):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.specs = []
        self.polls = {}
        self.fail = False
        self.voices = out_dir / "voices"           # reference clips, like the real voices folder
        self.voices.mkdir(exist_ok=True)
        for v in ("human_ref48.wav", "buster_ref48.wav"):
            (self.voices / v).write_bytes(b"RIFF....WAVE" + v.encode())

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/meta":
            return httpx.Response(200, json={"voices": ["human_ref48.wav", "buster_ref48.wav"],
                                             "voices_dir": str(self.voices),
                                             "presets": {"clone_exact": {"label": "Clone the reference exactly"},
                                                         "narrator": {"label": "Narrator"}}})
        if path == "/api/jobs" and request.method == "POST":
            spec = json.loads(request.content)
            self.specs.append(spec)
            jid = f"job{len(self.specs)}"
            self.polls[jid] = 0
            return httpx.Response(200, json={"id": jid})
        if path.startswith("/api/jobs/"):
            jid = path.rsplit("/", 1)[1]
            self.polls[jid] += 1
            secs = [{"i": 0, "title": "Water", "status": "ok", "wer": 0.0},
                    {"i": 1, "title": "Oxygen", "status": "rendering", "wer": None}]
            if self.polls[jid] < 2:
                return httpx.Response(200, json={"status": "running", "message": "section 2/2: rendering",
                                                 "sections": secs, "result": None, "error": None})
            if self.fail:
                return httpx.Response(200, json={"status": "failed", "message": "failed", "sections": secs,
                                                 "result": None, "error": "CUDA out of memory"})
            name = self.specs[int(jid[3:]) - 1]["name"]
            wav, mp3 = self.out / f"{name}.wav", self.out / f"{name}.mp3"
            wav.write_bytes(b"RIFF....WAVEfake")
            mp3.write_bytes(b"ID3fake-mp3")
            secs[1].update(status="ok", wer=0.01)
            return httpx.Response(200, json={"status": "done", "message": "done", "sections": secs, "error": None,
                                             "result": {"wav": str(wav), "mp3": str(mp3), "seconds": 61.2,
                                                        "clock": "1:01", "words": 160, "wpm": 157, "flagged": []}})
        return httpx.Response(404)


@pytest.fixture
def fake_voice(tmp_path):
    return FakeVoiceApp(tmp_path / "vo-outputs")


@pytest.fixture
def voice_dir(tmp_path):
    d = tmp_path / "vo-app"
    d.mkdir()
    shutil.copy(FAKES / "server.py", d / "server.py")
    shutil.copy(FAKES / "make_srt.py", d / "make_srt.py")
    return d


@pytest.fixture
def make_client(tmp_path, fake_voice, voice_dir, monkeypatch, request):
    monkeypatch.setattr("app.voice.POLL", 0.05)

    def make(configured=True, transport="fake", url=None):
        settings = Settings(data_dir=tmp_path / "data",
                            voice_app_dir=str(voice_dir) if configured else None,
                            voice_python=sys.executable if configured else None,
                            voice_app_url=url or "http://127.0.0.1:8720")
        t = httpx.MockTransport(fake_voice.handler) if transport == "fake" else None
        app = create_app(settings, token=TOKEN, voice_transport=t)
        c = TestClient(app, base_url="http://127.0.0.1")
        c.__enter__()
        request.addfinalizer(lambda: c.__exit__(None, None, None))
        c.headers[TOKEN_HEADER] = TOKEN
        c.data_dir = tmp_path / "data"
        c.fake = fake_voice
        return c
    return make


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def project(client):
    return client.post("/api/projects", json={"name": "P4"}).json()["id"]


def make_docx(path: Path, paragraphs: list[tuple[str | None, str]]) -> Path:
    """Minimal Word file: (style or None, text) per paragraph."""
    body = "".join(
        f'<w:p>{f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""}'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
        for style, text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", "<Types/>")
    return path


def wait_job(c, jid, timeout=20):
    import time
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/voicejobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job never finished: {j}")
