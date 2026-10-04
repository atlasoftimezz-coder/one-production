"""Real-browser check of the voiceover flow: file -> dialog -> GPU approval -> live card ->
player + files. Runs against the stand-in voice app (started by One Production itself)."""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FAKES = Path(__file__).parent / "fakes"


def test_voice_flow_in_headless_chrome(tmp_path):
    vo = tmp_path / "vo-app"
    vo.mkdir()
    shutil.copy(FAKES / "server.py", vo / "server.py")
    shutil.copy(FAKES / "make_srt.py", vo / "make_srt.py")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); vport = s.getsockname()[1]
    env = os.environ.copy()
    env.update({"ONEPROD_DATA_DIR": str(tmp_path / "data"), "ONEPROD_OPEN_BROWSER": "false",
                "ONEPROD_SETTINGS_FILE": str(tmp_path / "s.json"), "ONEPROD_LOG_LEVEL": "warning",
                "ONEPROD_VOICE_APP_DIR": str(vo), "ONEPROD_VOICE_PYTHON": sys.executable,
                "ONEPROD_VOICE_APP_URL": f"http://127.0.0.1:{vport}", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        launch = None
        deadline = time.time() + 20
        while time.time() < deadline and not launch:
            m = re.search(r"launch: (\S+)", proc.stdout.readline())
            launch = m and m.group(1)
        assert launch, "server did not start"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"),
                            launch, str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_VOICE": "1"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        proc.kill(); proc.wait(10)
        # the stand-in voice app was started by One Production; stop it too
        subprocess.run(f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{vport} ^| findstr LISTENING\') '
                       f'do taskkill /PID %a /F', shell=True, capture_output=True)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    token = launch.split("#t=")[-1]
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:].replace(token, "<token>")
    assert "card finishes" in out
