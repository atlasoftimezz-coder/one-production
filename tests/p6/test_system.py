"""System scan (plan R13 step 2): read-only, and it must tell the truth about this PC."""
import asyncio
import re
import shutil
import subprocess
import sys

import pytest

from app import system

from .conftest import tree


def test_nvidia_output_is_read_including_rtx_50_series():
    g = system.parse_nvidia("NVIDIA GeForce RTX 5070 Ti, 16303, 581.42, 12.0\n")[0]
    assert g == {"name": "NVIDIA GeForce RTX 5070 Ti", "vram_gb": 15.9, "driver": "581.42", "compute": "12.0",
                 "blackwell": True}
    old = system.parse_nvidia("NVIDIA GeForce GTX 1060 6GB, 6144, 472.12\n")[0]
    assert old["blackwell"] is False and old["vram_gb"] == 6.0
    assert system.parse_nvidia("NVIDIA-SMI has failed\n") == []


def test_small_gpu_and_rtx50_get_plain_advice(monkeypatch):
    monkeypatch.setattr(system.shutil, "which", lambda n: "nvidia-smi.exe" if n == "nvidia-smi" else None)

    async def fake(argv, timeout):
        return 0, "NVIDIA GeForce RTX 5060, 8151, 581.42, 12.0\n"
    g = asyncio.run(system.gpu(fake))
    assert g["status"] == "ok" and "cu128" in g["fix"] and "RTX 50-series" in g["value"]

    async def small(argv, timeout):
        return 0, "NVIDIA GeForce GTX 1050, 2048, 472.12, 6.1\n"
    assert asyncio.run(system.gpu(small))["status"] == "warn"


def test_no_nvidia_gpu_is_a_warning_not_an_error(monkeypatch):
    monkeypatch.setattr(system.shutil, "which", lambda n: None)

    async def never(argv, timeout):
        raise AssertionError("nothing to run")
    g = asyncio.run(system.gpu(never))
    assert g["status"] == "warn" and "20 times slower" in g["fix"]


def test_scan_endpoint_lists_everything_and_changes_nothing(client):
    before = tree(client.data_dir)
    r = client.get("/api/system")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    for key in ("os", "ram", "disk_data", "disk_backup", "gpu", "claude", "codex", "node", "python",
                "acc_claude", "acc_codex", "acc_anthropic_api", "voice", "backups"):
        assert key in items, key
        assert items[key]["status"] in ("ok", "warn", "bad", "info") and items[key]["label"]
    assert all(i["fix"] for i in items.values() if i["status"] in ("warn", "bad"))   # every problem says what to do
    assert tree(client.data_dir) == before                                         # read-only


# ---------------------------------------------------------------- matches this PC
@pytest.fixture(scope="module")
def scanned():
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.main import create_app
    import tempfile
    from pathlib import Path
    s = load_settings().model_copy(update={"data_dir": Path(tempfile.mkdtemp()), "open_browser": False})
    with TestClient(create_app(s, token="s" * 32), base_url="http://127.0.0.1") as c:
        c.headers["X-OneProd-Token"] = "s" * 32
        return {i["id"]: i for i in c.get("/api/system").json()["items"]}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_build_matches(scanned):
    assert f"build {sys.getwindowsversion().build}" in scanned["os"]["value"]


@pytest.mark.skipif(not shutil.which("nvidia-smi"), reason="no NVIDIA GPU on this PC")
def test_gpu_matches_nvidia_smi(scanned):
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.splitlines()[0]
    name, mib = [x.strip() for x in out.split(",")]
    assert name in scanned["gpu"]["value"]
    assert f"{round(float(mib) / 1024, 1):g} GB" in scanned["gpu"]["value"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg on PATH")
def test_ffmpeg_matches_path_and_filters(scanned):
    exe = shutil.which("ffmpeg")
    filters = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    v = scanned["ffmpeg"]["value"]
    assert exe in v and (("has rubberband" in v) == ("rubberband" in filters))


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code not installed")
def test_claude_version_matches(scanned):
    out = subprocess.run("claude --version", shell=True, capture_output=True, text=True).stdout
    assert re.search(r"\d+\.\d+\.\d+", out).group(0) == scanned["claude"]["value"]


@pytest.mark.skipif(not shutil.which("codex"), reason="Codex not installed")
def test_codex_version_matches(scanned):
    out = subprocess.run("codex --version", shell=True, capture_output=True, text=True).stdout
    assert re.search(r"\d+\.\d+\.\d+", out).group(0) == scanned["codex"]["value"]
