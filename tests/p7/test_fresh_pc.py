"""A brand-new PC, as close as a test can get without making a real Windows account: the real
launcher in its own process, an empty user profile, and a PATH with nothing but Windows on it
(no Claude Code, no Codex, no Node, no ffmpeg). The manual check repeats this in a real new
Windows user."""
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def windows_only_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.pathsep.join([os.path.join(root, "System32"), root, os.path.join(root, "System32", "Wbem")])


def launch_lines(proc, q):
    for line in proc.stdout:
        m = re.search(r"launch: (\S+)", line)
        if m:
            q.put(m.group(1))


def next_launch(q, timeout=30):
    return q.get(timeout=timeout)


def client(launch):
    base, token = launch.split("/#t=")
    return httpx.Client(base_url=base, headers={"X-OneProd-Token": token}, timeout=60)


def test_new_user_with_nothing_installed_reaches_setup_and_moves_the_data_folder(tmp_path):
    profile = tmp_path / "Users" / "friend"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ONEPROD_", "VOAPP_"))}
    env.update({"PATH": windows_only_path(), "LOCALAPPDATA": str(profile / "AppData" / "Local"),
                "APPDATA": str(profile / "AppData" / "Roaming"), "USERPROFILE": str(profile),
                "ONEPROD_OPEN_BROWSER": "false", "ONEPROD_LOG_LEVEL": "warning", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    q: queue.Queue = queue.Queue()
    threading.Thread(target=launch_lines, args=(proc, q), daemon=True).start()
    try:
        first = next_launch(q)
        with client(first) as c:
            s = c.get("/api/setup").json()
            assert s["first_run"] is True and s["done"] is False and s["can_restart"] is True
            assert Path(s["data_dir"]) == profile / "AppData" / "Local" / "OneProduction"
            scan = {i["id"]: i for i in c.get("/api/system").json()["items"]}
            for tool in ("claude", "codex", "node"):
                assert scan[tool]["status"] == "warn" and scan[tool]["value"] == "not installed", scan[tool]
            assert scan["ffmpeg"]["status"] == "warn" and scan["ffmpeg"]["fix"]
            voice = {m["id"]: m for m in c.get("/api/modules").json()["modules"]}["voice"]
            assert voice["state"] in ("off", "needs_install")                  # never 'ready' on a new PC
            new_home = tmp_path / "D" / "OneProduction"
            r = c.post("/api/setup/data-folder", json={"path": str(new_home)}).json()
            assert r["restarting"] is True
        second = next_launch(q)                                                 # same window, new session
        assert second != first
        with client(second) as c:
            s = c.get("/api/setup").json()
            assert Path(s["data_dir"]) == new_home and s["chose_folder"] is True and s["done"] is False
            assert c.post("/api/projects", json={"name": "First video"}).status_code == 200
        assert (new_home / "projects").is_dir()
        old_token = first.split("#t=")[1]
        with httpx.Client(base_url=second.split("/#t=")[0], timeout=10) as raw:
            assert raw.get("/api/setup", headers={"X-OneProd-Token": old_token}).status_code == 401
    finally:
        proc.kill()
        proc.wait(10)
