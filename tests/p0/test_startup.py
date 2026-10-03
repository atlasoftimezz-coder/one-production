"""Starts the real launcher as a separate process, the way start.bat does."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path):
    env = os.environ.copy()
    env.update({
        "ONEPROD_DATA_DIR": str(tmp_path / "data"),
        "ONEPROD_OPEN_BROWSER": "false",
        "ONEPROD_SETTINGS_FILE": str(tmp_path / "settings.json"),
        "ONEPROD_LOG_LEVEL": "warning",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def _launch(tmp_path):
    return subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=_env(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")


def _wait_for_port(proc, timeout=20):
    """Read the launcher's banner to learn which port it picked."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if "http://127.0.0.1:" in line:
            return int(line.strip().rstrip("/").rsplit(":", 1)[1])
        if proc.poll() is not None:
            break
    raise AssertionError("launcher did not print its URL")


def _health(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                return json.load(r)
        except OSError:
            time.sleep(0.3)
    raise AssertionError("health check never answered")


def _stop(proc):
    proc.kill()
    proc.wait(10)


def test_launcher_starts_and_answers_health(tmp_path):
    proc = _launch(tmp_path)
    try:
        port = _wait_for_port(proc)
        h = _health(port)
        assert h["status"] == "ok"
        assert h["data_dir"] == str(tmp_path / "data")
        assert (tmp_path / "data" / "logs" / "app.log").exists()
    finally:
        _stop(proc)


def test_second_launch_picks_another_port(tmp_path):
    a = _launch(tmp_path / "a")
    try:
        port_a = _wait_for_port(a)
        _health(port_a)
        b = _launch(tmp_path / "b")
        try:
            port_b = _wait_for_port(b)
            assert port_b != port_a
            assert _health(port_b)["status"] == "ok"
        finally:
            _stop(b)
    finally:
        _stop(a)


def test_server_is_not_reachable_on_other_interfaces(tmp_path):
    proc = _launch(tmp_path)
    try:
        port = _wait_for_port(proc)
        _health(port)
        lan_ip = socket.gethostbyname(socket.gethostname())
        if lan_ip.startswith("127."):
            return  # no LAN address on this machine; nothing to test
        with socket.socket() as s:
            s.settimeout(2)
            assert s.connect_ex((lan_ip, port)) != 0, f"server reachable on {lan_ip}"
    finally:
        _stop(proc)
