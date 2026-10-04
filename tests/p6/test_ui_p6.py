"""Real-browser check of P6: back up, delete, restore from the Backups screen; the System
scan screen; profile name, PIN, Lock now, wrong and right PIN."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_backups_system_and_pin_in_headless_chrome(tmp_path):
    env = os.environ.copy()
    env.update({"ONEPROD_DATA_DIR": str(tmp_path / "data"), "ONEPROD_OPEN_BROWSER": "false",
                "ONEPROD_SETTINGS_FILE": str(tmp_path / "s.json"), "ONEPROD_LOG_LEVEL": "warning",
                "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        launch = None
        while not launch:
            line = proc.stdout.readline()
            if not line:
                break
            m = re.search(r"launch: (\S+)", line)
            launch = m and m.group(1)
        assert launch, "server did not start"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"), launch,
                            str(tmp_path / "shots")], env={**os.environ, "UI_DRIVE_P6": "1"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        proc.kill()
        proc.wait(10)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    token = launch.split("#t=")[-1]
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:].replace(token, "<token>")
    assert "right PIN unlocks" in out
