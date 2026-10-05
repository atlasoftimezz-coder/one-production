"""Real-browser check: launch the app, drive headless Chrome through every UI feature
(create project, streaming chat, uploads, duplicate names, download, suggestions, dark mode,
toast placement, reload lock, no JS errors, no garbled text). Screenshots are kept in the
test's temp folder for inspection."""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from .test_launch import _start

ROOT = Path(__file__).resolve().parents[2]


def test_ui_in_headless_chrome(tmp_path):
    proc, port, token = _start(tmp_path)
    # drain the server's log: a full pipe blocks the server and the browser waits forever
    threading.Thread(target=lambda: list(proc.stdout), daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/#t={token}"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"),
                            url, str(tmp_path / "shots")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    finally:
        proc.kill(); proc.wait(10)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0, out + r.stderr.replace(token, "<token>")[-2000:]
    assert "FAIL" not in out
