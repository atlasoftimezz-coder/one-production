"""Read-only system scan (plan R13 step 2): what this PC has, in plain words.

Every item is {id, group, label, status, value, fix}:
    status  ok (green) | warn (amber) | bad (red) | info
    fix     one plain sentence on what to do, when something isn't right
Nothing here installs or changes anything. Commands run with a timeout; a missing tool is a
finding, never an error.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

from .providers.cli import resolve

GB = 1024 ** 3
Runner = Callable[[list[str], float], Awaitable[tuple[int, str]]]


async def run(argv: list[str], timeout: float = 20) -> tuple[int, str]:
    """(exit code, stdout+stderr). -1 when the program is missing or hangs."""
    try:
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    except (OSError, ValueError):
        return -1, ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, ""
    return proc.returncode, out.decode("utf-8", "replace")


def item(id_, group, label, status, value, fix=""):
    return {"id": id_, "group": group, "label": label, "status": status, "value": value, "fix": fix}


# ---------------------------------------------------------------- this PC
def windows() -> dict:
    if sys.platform != "win32":
        return item("os", "This PC", "Operating system", "warn", platform.platform(),
                    "One Production is built and tested for Windows 10/11.")
    v = sys.getwindowsversion()
    name = "Windows 11" if v.build >= 22000 else f"Windows {platform.release()}"
    edition = ""
    try:
        edition = platform.win32_edition() or ""
    except Exception:                                   # noqa: BLE001
        pass
    pretty = {"Professional": "Pro", "Core": "Home", "Enterprise": "Enterprise"}.get(edition, edition)
    ok = v.major >= 10
    return item("os", "This PC", "Operating system", "ok" if ok else "bad",
                f"{name} {pretty} (build {v.build})".replace("  ", " "),
                "" if ok else "Windows 10 or 11 is needed.")


def memory() -> dict:
    total = avail = None
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            total, avail = st.ullTotalPhys, st.ullAvailPhys
    elif hasattr(os, "sysconf"):
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        except (ValueError, OSError):
            pass
    if not total:
        return item("ram", "This PC", "Memory (RAM)", "info", "unknown")
    t = total / GB
    status = "ok" if t >= 15 else "warn" if t >= 7.5 else "bad"
    return item("ram", "This PC", "Memory (RAM)", status, f"{t:.0f} GB ({avail / GB:.0f} GB free now)",
                "" if status == "ok" else "16 GB or more keeps the voice engine and the browser smooth.")


def disk(id_: str, label: str, path: Path) -> dict:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
    except OSError:
        return item(id_, "This PC", label, "warn", f"{path} - not reachable", "Check the drive is connected.")
    free = u.free / GB
    status = "ok" if free >= 20 else "warn" if free >= 5 else "bad"
    drive = os.path.splitdrive(str(path))[0] or str(p)
    return item(id_, "This PC", label, status, f"{free:.0f} GB free of {u.total / GB:.0f} GB on {drive}",
                "" if status == "ok" else "Voiceovers and backups need space - free some up or pick another drive.")


def parse_nvidia(text: str) -> list[dict]:
    gpus = []
    for line in text.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3 or not parts[1].replace(".", "").isdigit():
            continue
        g = {"name": parts[0], "vram_gb": round(float(parts[1]) / 1024, 1), "driver": parts[2]}
        if len(parts) > 3 and re.match(r"^\d+\.\d+$", parts[3]):
            g["compute"] = parts[3]
        g["blackwell"] = bool(re.search(r"RTX\s*50\d\d", g["name"], re.I)) or \
            (g.get("compute", "0").split(".")[0].isdigit() and int(g.get("compute", "0").split(".")[0]) >= 12)
        gpus.append(g)
    return gpus


async def gpu(runner: Runner) -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return item("gpu", "This PC", "Graphics card (GPU)", "warn", "No NVIDIA GPU found",
                    "The voice module needs an NVIDIA card with 8 GB or more; without one it runs on the "
                    "processor, about 20 times slower.")
    code, out = await runner([exe, "--query-gpu=name,memory.total,driver_version,compute_cap",
                              "--format=csv,noheader,nounits"], 20)
    if code != 0:                                       # older drivers don't know compute_cap
        code, out = await runner([exe, "--query-gpu=name,memory.total,driver_version",
                                  "--format=csv,noheader,nounits"], 20)
    gpus = parse_nvidia(out) if code == 0 else []
    if not gpus:
        return item("gpu", "This PC", "Graphics card (GPU)", "warn", "nvidia-smi didn't answer",
                    "Update the NVIDIA driver from nvidia.com.")
    g = gpus[0]
    value = f"{g['name']} - {g['vram_gb']:g} GB - driver {g['driver']}"
    if g["blackwell"]:
        value += " - RTX 50-series"
    status = "ok" if g["vram_gb"] >= 7.5 else "warn"
    fix = "" if status == "ok" else "The voice module wants 8 GB of graphics memory; smaller cards may run out."
    if g["blackwell"]:
        fix = (fix + " " if fix else "") + "RTX 50-series cards need the CUDA 12.8 (cu128) build of PyTorch."
    out_item = item("gpu", "This PC", "Graphics card (GPU)", status, value, fix)
    out_item["gpus"] = gpus
    return out_item


# ---------------------------------------------------------------- tools
async def ffmpeg(runner: Runner, settings) -> list[dict]:
    items = []
    exe = shutil.which("ffmpeg")
    if not exe:
        items.append(item("ffmpeg", "Tools", "ffmpeg (audio/video tool)", "warn", "not on PATH",
                          "The voice module brings its own; other video tools later will need it on PATH."))
    else:
        code, out = await runner([exe, "-hide_banner", "-version"], 20)
        ver = (re.search(r"ffmpeg version (\S+)", out) or [None, "?"])[1] if code == 0 else "?"
        code, filters = await runner([exe, "-hide_banner", "-filters"], 20)
        rb = "rubberband" in filters
        items.append(item("ffmpeg", "Tools", "ffmpeg (audio/video tool)", "ok",
                          f"{ver} - {'has' if rb else 'no'} rubberband pitch filter - {exe}"))
    if settings.voice_app_dir and settings.voice_python and Path(settings.voice_python).exists():
        code, out = await runner([settings.voice_python, "-c",
                                  "import sys; sys.path.insert(0, sys.argv[1]); import config; "
                                  "print(config.FFMPEG); print(config.FFMPEG_HAS_RUBBERBAND)",
                                  settings.voice_app_dir], 60)
        lines = out.strip().splitlines()[-2:] if code == 0 else []
        if len(lines) == 2 and lines[0] != "None":
            rb = lines[1].strip() == "True"
            items.append(item("ffmpeg_voice", "Tools", "ffmpeg used by the voice module", "ok" if rb else "warn",
                              f"{lines[0]} - {'has' if rb else 'no'} rubberband",
                              "" if rb else "Presets that change pitch need an ffmpeg build with rubberband."))
        else:
            items.append(item("ffmpeg_voice", "Tools", "ffmpeg used by the voice module", "bad", "not found",
                              "The voice module can't master audio without ffmpeg."))
    return items


async def version_of(runner: Runner, id_: str, label: str, exe: str | None, args: list[str], pattern: str,
                     missing_fix: str) -> dict:
    if not exe:
        return item(id_, "Tools", label, "warn", "not installed", missing_fix)
    code, out = await runner([exe, *args], 30)
    m = re.search(pattern, out or "")
    if code != 0 or not m:
        return item(id_, "Tools", label, "warn", "installed but not answering", missing_fix)
    return item(id_, "Tools", label, "ok", m.group(1))


def python_runtime() -> dict:
    exe = Path(sys.executable)
    where = "development runtime (.venv)" if ".venv" in exe.parts else "bundled runtime" \
        if "runtime" in exe.parts else "system Python"
    return item("python", "Tools", "Python (runs One Production)", "ok",
                f"{platform.python_version()} - {where}")


# ---------------------------------------------------------------- accounts & modules
def accounts_item(provider: str, label: str, accounts: list[dict]) -> dict:
    mine = [a for a in accounts if a["provider"] == provider]
    if not mine:
        return item(f"acc_{provider}", "Accounts & modules", label, "info", "none added",
                    "Add one under Accounts if you want to use it.")
    ok = [a for a in mine if a.get("status") == "ok"]
    status = "ok" if len(ok) == len(mine) else "warn" if ok else "bad"
    value = f"{len(ok)} of {len(mine)} signed in"
    return item(f"acc_{provider}", "Accounts & modules", label, status, value,
                "" if status == "ok" else "Open Accounts and press Re-login on the red ones.")


async def voice_item(voice_svc) -> dict:
    missing = voice_svc.missing()
    if missing:
        return item("voice", "Accounts & modules", "Voice module", "warn", "not set up",
                    "Point One Production at the voiceover app (voice_app_dir / voice_python in settings).")
    running = await voice_svc.running()
    meta = voice_svc.last_meta or {}
    voices = [v for v in meta.get("voices", []) if not v.startswith("_upload_")]
    value = ("running" if running else "set up, starts when needed") + (f" - {len(voices)} voices" if voices else "")
    return item("voice", "Accounts & modules", "Voice module", "ok", value)


def backups_item(backups) -> dict:
    lst = backups.list()
    folder = backups.settings()["folder_resolved"]
    if not lst:
        return item("backups", "Accounts & modules", "Backups", "warn", f"none yet - folder {folder}",
                    "Open Backups and press Back up now (a daily one also runs by itself).")
    newest = max((b.get("created") or 0) for b in lst)
    age_h = (time.time() - newest) / 3600
    status = "ok" if age_h <= 48 else "warn"
    when = f"{age_h:.0f} hours ago" if age_h >= 1 else "less than an hour ago"
    return item("backups", "Accounts & modules", "Backups", status, f"{len(lst)} kept, newest {when} - {folder}",
                "" if status == "ok" else "No backup for two days - open Backups and press Back up now.")


async def scan(settings, accounts: list[dict], voice_svc, backups, runner: Runner = run) -> dict:
    started = time.time()
    claude = resolve("claude", settings.claude_cmd)
    codex = resolve("codex", settings.codex_cmd)
    node = shutil.which("node")
    results = await asyncio.gather(
        gpu(runner), ffmpeg(runner, settings),
        version_of(runner, "claude", "Claude Code", claude, ["--version"], r"(\d+\.\d+\.\d+)",
                   "Install Claude Code if you want to use Claude logins (API keys work without it)."),
        version_of(runner, "codex", "Codex", codex, ["--version"], r"(\d+\.\d+\.\d+)",
                   "Install Codex if you want to use ChatGPT/Codex logins."),
        version_of(runner, "node", "Node.js", node, ["--version"], r"v?(\d+\.\d+\.\d+)",
                   "Only needed to install or update the Claude Code / Codex command-line tools."),
        voice_item(voice_svc))
    gpu_i, ff_items, claude_i, codex_i, node_i, voice_i = results
    items = [windows(), memory(), disk("disk_data", "Space for your projects", settings.data_dir),
             disk("disk_backup", "Space for backups", Path(backups.settings()["folder_resolved"])), gpu_i,
             *ff_items, claude_i, codex_i, node_i, python_runtime(),
             accounts_item("claude", "Claude logins", accounts),
             accounts_item("codex", "Codex logins", accounts),
             accounts_item("anthropic_api", "Anthropic API keys", accounts),
             voice_i, backups_item(backups)]
    return {"scanned_at": time.time(), "took_s": round(time.time() - started, 1), "items": items}
