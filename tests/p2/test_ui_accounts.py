"""Real-browser check of the accounts panel and sign-in flow, against the fake CLIs
(never the user's real logins)."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_accounts_flow_in_headless_chrome(tmp_path, fake_cmds):
    env = os.environ.copy()
    env.update({"ONEPROD_DATA_DIR": str(tmp_path / "data"), "ONEPROD_OPEN_BROWSER": "false",
                "ONEPROD_SETTINGS_FILE": str(tmp_path / "s.json"), "ONEPROD_LOG_LEVEL": "warning",
                "ONEPROD_CLAUDE_CMD": str(fake_cmds / "claude.cmd"),
                "ONEPROD_CODEX_CMD": str(fake_cmds / "codex.cmd"),
                "ONEPROD_LOGIN_CONSOLE": "0", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        launch = None
        deadline = time.time() + 20
        while time.time() < deadline and not launch:
            m = re.search(r"launch: (\S+)", proc.stdout.readline())
            launch = m and m.group(1)
        assert launch, "server did not start"
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tools" / "ui_drive.py"),
                            launch, str(tmp_path / "shots")],
                           env={**os.environ, "UI_DRIVE_ACCOUNTS": "1"},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    finally:
        proc.kill(); proc.wait(10)
    if r.returncode == 3:
        pytest.skip("Chrome is not installed on this PC")
    token = launch.split("#t=")[-1]
    out = r.stdout.replace(token, "<token>")
    assert r.returncode == 0 and "FAIL" not in out, out + r.stderr[-1500:].replace(token, "<token>")
    assert "Claude account answers in chat" in out
