"""Real-browser check of the MCP and Storage pages (owner, 2026-10-12)."""
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.p1.test_launch import _start

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_and_storage_pages_in_headless_chrome(tmp_path):
    store = tmp_path / "exports"
    store.mkdir()
    (store / "sample.txt").write_text("hi", encoding="utf-8")
    proc, port, token = _start(tmp_path)
    threading.Thread(target=lambda: list(proc.stdout), daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/#t={token}"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"), url, str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_PAGES": "1", "UI_DRIVE_PY": sys.executable,
                                "UI_DRIVE_FAKE_MCP": str(Path(__file__).parent / "fakes" / "fake_mcp_stdio.py"),
                                "UI_DRIVE_STORE": str(store)},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    finally:
        proc.kill(); proc.wait(10)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0, out + r.stderr.replace(token, "<token>")[-2000:]
    assert "FAIL" not in out
