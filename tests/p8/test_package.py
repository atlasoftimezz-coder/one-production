"""The plan's P8 check, for real: install from the package into an empty folder -> it starts;
update to v+1 -> data intact; rollback -> the previous version runs; diagnostics hold no secrets.

Two real packages are built (the version in app/version.py and a test "+1"), the first is
extracted the way a friend would, and everything runs through "One Production.cmd" on the
package's own embedded Python, with an empty user profile."""
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_package  # noqa: E402
import publish_release  # noqa: E402

from app.version import __version__  # noqa: E402
from tests.p2.conftest import GOOD_KEY  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the package is for Windows")


def bump(v: str) -> str:
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


# Built under the project's build folder, not the system temp folder: each run unpacks ~150 MB
# and the studio PC's C: drive is nearly full.
WORK = ROOT / "build" / "test-package"


@pytest.fixture(scope="module")
def packages():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    out = WORK / "dist"
    cache = ROOT / "build" / "cache"
    a = build_package.build(__version__, out, cache, None, WORK / "stage-a")
    b = build_package.build(bump(__version__), out, cache, None, WORK / "stage-b")
    yield out, a, b
    shutil.rmtree(WORK / "stage-a", ignore_errors=True)
    shutil.rmtree(WORK / "stage-b", ignore_errors=True)


class Running:
    """One Production.cmd in its own console-less process; every launch line is collected."""

    def __init__(self, install: Path, profile: Path):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ONEPROD_", "VOAPP_"))}
        env.update({"LOCALAPPDATA": str(profile / "Local"), "APPDATA": str(profile / "Roaming"),
                    "USERPROFILE": str(profile), "ONEPROD_OPEN_BROWSER": "false", "ONEPROD_LOG_LEVEL": "warning"})
        self.proc = subprocess.Popen(["cmd", "/c", str(install / "One Production.cmd")], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace")
        self.q: queue.Queue = queue.Queue()
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())
            m = re.search(r"launch: (\S+)", line)
            if m:
                self.q.put(m.group(1))

    def next_client(self, timeout=60) -> httpx.Client:
        try:
            launch = self.q.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("no launch line:\n" + "\n".join(self.lines[-30:]))
        base, token = launch.split("/#t=")
        return httpx.Client(base_url=base, headers={"X-OneProd-Token": token}, timeout=60)

    def stop(self):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)


def wait_update(c, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/api/update").json()["state"]
        if not st.get("running"):
            return st
        time.sleep(0.3)
    raise AssertionError("update download didn't finish")


def test_install_update_rollback_and_diagnostics(packages):
    out, zip_a, zip_b = packages
    tmp_path = WORK / "run"
    v_a, v_b = __version__, bump(__version__)
    install = tmp_path / "Apps"
    with zipfile.ZipFile(zip_a) as z:                                # what a friend does: extract the zip
        z.extractall(install)
    install = install / "OneProduction"
    releases = tmp_path / "releases"
    publish_release.publish(v_b, out, releases)

    app = Running(install, tmp_path / "friend")
    try:
        c = app.next_client()
        h = c.get("/health").json()
        assert h["version"] == v_a and h["python"].startswith("3.12.10")      # its own embedded Python
        u = c.get("/api/update").json()
        assert u["packaged"] and u["current"] == v_a and u["previous"] is None and u["can_relaunch"]
        pid = c.post("/api/projects", json={"name": "Friend's first video"}).json()["id"]
        script = "## Hook\nThe town that stopped. — ✓\n".encode()
        c.post(f"/api/projects/{pid}/files", files={"files": ("script.md", script)})
        c.post(f"/api/projects/{pid}/chat", json={"message": f"my key {GOOD_KEY}", "account_id": "demo"})

        assert c.post("/api/update/check").json()["message"].startswith("No update source")
        assert c.patch("/api/update/source", json={"source": str(releases)}).status_code == 200
        chk = c.post("/api/update/check").json()
        assert chk["newer"] and chk["latest"] == v_b
        c.post("/api/update/download")
        st = wait_update(c)
        assert st.get("ready") == v_b, st
        assert c.post("/api/update/apply").json()["relaunching"] is True
        c.close()

        c = app.next_client()                                          # the launcher started the new version
        assert c.get("/health").json()["version"] == v_b
        u = c.get("/api/update").json()
        assert u["current"] == v_b and u["previous"] == v_a and u["versions"] == [v_a, v_b]
        assert c.get(f"/api/projects/{pid}/files/script.md").content == script        # data intact
        assert any(m["role"] == "user" for m in c.get(f"/api/projects/{pid}/chat").json())
        assert c.post("/api/update/rollback").json()["relaunching"] is True
        c.close()

        c = app.next_client()                                          # back on the previous version
        assert c.get("/health").json()["version"] == v_a
        assert c.get("/api/update").json()["current"] == v_a
        assert c.get(f"/api/projects/{pid}/files/script.md").content == script
        r = c.get("/api/diagnostics")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
            blob = b"".join(z.read(n) for n in names)
        assert {"README.txt", "versions.json", "system-scan.json", "recent-errors.json", "app.log"} <= set(names)
        assert GOOD_KEY.encode() not in blob                            # no secrets
        assert b"Friend's first video" not in blob and b"my key" not in blob          # no chat text
        c.close()
    finally:
        app.stop()
