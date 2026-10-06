"""Real-browser check of the Resolve panel against the stand-in Resolve: turn control on, probe,
add a plan, the run is refused until the owner confirms, then passes and is verified."""
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.p1.test_launch import _start

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).parent / "fakes" / "fake_resolve_runner.py"


def test_resolve_plans_in_headless_chrome(tmp_path, monkeypatch):
    monkeypatch.setenv("ONEPROD_RESOLVE_RUNNER", str(FAKE))
    monkeypatch.setenv("FAKE_RESOLVE_STATE", str(tmp_path / "resolve.json"))
    for k in ("FAKE_RESOLVE_DOWN", "FAKE_RESOLVE_FAIL", "FAKE_RESOLVE_VERSION", "ONEPROD_RESOLVE_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "ResolveMCP.exe").write_bytes(b"MZ stand-in")          # found, never started by this test
    monkeypatch.setenv("ONEPROD_RESOLVE_MCP_CMD", str(tmp_path / "ResolveMCP.exe"))
    proc, port, token = _start(tmp_path)
    threading.Thread(target=lambda: list(proc.stdout), daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/#t={token}"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"), url, str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_RESOLVE": "1"}, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    finally:
        proc.kill(); proc.wait(10)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0, out + r.stderr.replace(token, "<token>")[-2000:]
    assert "FAIL" not in out
    assert "run passes and is verified" in out
