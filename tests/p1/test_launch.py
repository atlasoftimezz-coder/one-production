"""The launcher hands the token over in the URL fragment and nowhere persistent."""
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _start(tmp_path):
    env = os.environ.copy()
    env.update({"ONEPROD_DATA_DIR": str(tmp_path / "data"), "ONEPROD_OPEN_BROWSER": "false",
                "ONEPROD_SETTINGS_FILE": str(tmp_path / "s.json"), "ONEPROD_LOG_LEVEL": "info",
                "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    deadline = time.time() + 20
    while time.time() < deadline:
        line = proc.stdout.readline()
        m = re.search(r"launch: (http://127\.0\.0\.1:(\d+)/#t=(\S+))", line)
        if m:
            return proc, int(m.group(2)), m.group(3)
    proc.kill()
    raise AssertionError("no launch link printed")


def _get(port, path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("X-OneProd-Token", token)
    for _ in range(40):
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except OSError:
            time.sleep(0.25)
    raise AssertionError("server never answered")


def test_launch_link_carries_token_in_fragment_and_it_works(tmp_path):
    proc, port, token = _start(tmp_path)
    try:
        assert len(token) >= 32
        assert _get(port, "/api/projects")[0] == 401
        assert _get(port, "/api/projects", token)[0] == 200
    finally:
        proc.kill(); proc.wait(10)
    log = (tmp_path / "data" / "logs" / "app.log").read_text(encoding="utf-8")
    assert token not in log, "session token written to the log file"


def test_each_launch_gets_a_new_token(tmp_path):
    p1, _, t1 = _start(tmp_path / "a")
    p1.kill(); p1.wait(10)
    p2, _, t2 = _start(tmp_path / "b")
    p2.kill(); p2.wait(10)
    assert t1 != t2
