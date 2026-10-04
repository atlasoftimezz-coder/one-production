"""Real-browser walk through the setup wizard on a fresh data folder: opens by itself, scan,
modules, install (stand-in items - nothing is downloaded), account, own voice + tuning, test
run, Finish. The app runs in this process so the installer can be stood in for."""
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

from app import installer as I
from app.config import Settings
from app.main import create_app
from tests.p2.conftest import GOOD_KEY, MemoryKeyring, api_handler
from tests.p4.conftest import FakeVoiceApp

from .conftest import RTX5060TI, TOKEN
from .test_setup import FAKE_TUNE

ROOT = Path(__file__).resolve().parents[2]
FAKES4 = ROOT / "tests" / "p4" / "fakes"


def stand_in_installer(monkeypatch, engine: Path):
    """Five items that 'download' in a second each, and a final check that really runs."""
    state = {"verified": False, "done": set()}

    def items(self):
        def mk(i, label, mb):
            def run(ctx):
                for k in range(5):
                    ctx.check()
                    ctx.progress((k + 1) / 5, f"{label} - {k + 1} of 5")
                    time.sleep(0.1)
                state["done"].add(i)
            return I.Item(i, label, "stand-in for the test", mb, "test.local", run, lambda: i in state["done"])
        return [mk("ffmpeg", "ffmpeg (audio tool)", 100), mk("python", "Python for the voice engine", 30),
                mk("torch", "PyTorch (graphics card, CUDA 12.8)", 3400), mk("engine", "Voice engine libraries", 650),
                mk("models", "Voice and speech-recognition models", 4800)]

    def verify(self, ctx):
        state["verified"] = True
        return {"cuda": True}
    monkeypatch.setattr(I.VoiceRecipe, "items", items)
    monkeypatch.setattr(I.VoiceRecipe, "verify", verify)
    monkeypatch.setattr(I.VoiceRecipe, "ready", lambda self: state["verified"])
    monkeypatch.setattr(I.VoiceRecipe, "python", property(lambda self: Path(sys.executable)))
    monkeypatch.setattr("app.modules.ENGINE_DIR", engine)


def test_setup_wizard_in_headless_chrome(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr("app.voice.POLL", 0.1)
    engine = tmp_path / "engine"
    engine.mkdir()
    for f in ("server.py", "make_srt.py"):
        (engine / f).write_bytes((FAKES4 / f).read_bytes())
    stand_in_installer(monkeypatch, engine)
    tune = tmp_path / "tune.py"
    tune.write_text(FAKE_TUNE, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", login_console=False, claude_cmd=str(tmp_path / "none.exe"),
                        codex_cmd=str(tmp_path / "none2.exe"), anthropic_base_url="https://api.test", log_level="warning")
    settings.ensure_dirs()
    app = create_app(settings, token=TOKEN, gpu_probe=lambda: RTX5060TI, tune_script=tune,
                     api_transport=httpx.MockTransport(api_handler),
                     voice_transport=httpx.MockTransport(FakeVoiceApp(tmp_path / "vo").handler))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        end = time.time() + 15
        while not server.started and time.time() < end:
            time.sleep(0.05)
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"),
                            f"http://127.0.0.1:{port}/#t={TOKEN}", str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_SETUP": "1", "UI_DRIVE_TEST_KEY": GOOD_KEY},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        server.should_exit = True
        th.join(10)
        keyring.set_keyring(previous)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(TOKEN, "<token>").replace(GOOD_KEY, "<key>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:]
    assert "Finish closes setup" in out
