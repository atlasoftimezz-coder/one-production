"""Build the package a friend downloads (plan R15): one zip, nothing to install first.

    .venv\\Scripts\\python tools\\build_package.py [--version X] [--update-source URL-or-folder]

    dist\\OneProduction-<version>-win64.zip     the package
    dist\\OneProduction-<version>-win64.json    its manifest (version, sha256, size, notes) - what
                                              "Check for updates" reads (see tools/publish_release.py)

Inside the zip:

    OneProduction\\
      One Production.cmd     double-click to start; restarts into a new version after an update
      READ ME FIRST.txt
      current.txt            the version that runs
      install.json           current / previous version, where updates come from
      versions\\<version>\\
        python\\             the official embeddable Python 3.12 + One Production's libraries
        app\\ web\\ voice\\    One Production itself, the web page, the voice engine
        samples\\ docs\\

No admin rights, nothing written outside the folder it is extracted to (data lives in
%LOCALAPPDATA%\\OneProduction, or wherever the setup wizard puts it). The heavy voice pieces
(PyTorch, models, ffmpeg) are not in the zip - the setup wizard fetches them for that PC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_VERSION = "3.12.10"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
SHIP = ["app", "web", "voice", "samples", "facelessos"]
SKIP = {"__pycache__", ".pytest_cache"}

LAUNCHER = r"""@echo off
setlocal
title One Production
cd /d "%~dp0"
:run
set "V="
set /p V=<"%~dp0current.txt"
if not exist "%~dp0versions\%V%\python\python.exe" (
  echo One Production %V% is missing from the versions folder.
  echo Extract the zip you were given again, into the same folder.
  pause
  exit /b 1
)
"%~dp0versions\%V%\python\python.exe" -m app %*
rem 75 = restart into another version (after an update or a rollback)
if %errorlevel%==75 goto run
endlocal
"""

README = """One Production {version}
========================

Start: double-click "One Production.cmd". A black window opens (keep it open - closing
it stops One Production) and your browser opens the app. The first time, a short setup
guides you through everything else.

Nothing is installed on Windows itself and no admin rights are needed. You can keep this
folder anywhere, e.g. in Documents or on another drive. Your projects are stored
separately (the setup shows where), so this folder can be replaced or updated safely.

Updates: System -> Updates. The previous version stays here, so you can go back.
Problems: System -> Export diagnostics, and send the zip to whoever gave you One Production.
"""


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def embeddable_python(cache: Path) -> Path:
    zp = cache / f"python-{PY_VERSION}-embed-amd64.zip"
    if not zp.exists() or sha256_of(zp) != PY_SHA256:
        cache.mkdir(parents=True, exist_ok=True)
        log(f"downloading Python {PY_VERSION} (embeddable, ~11 MB)")
        urllib.request.urlretrieve(PY_URL, zp)
        if sha256_of(zp) != PY_SHA256:
            zp.unlink()
            raise SystemExit("the Python download didn't match its checksum - try again")
    return zp


def changelog_notes(version: str) -> str:
    log_file = ROOT / "docs" / "CHANGELOG.md"
    if not log_file.exists():
        return ""
    text = log_file.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in SKIP or n.endswith(".pyc")])


def build(version: str, out: Path, cache: Path, update_source: str | None, stage_root: Path) -> Path:
    stage = stage_root / "OneProduction"
    shutil.rmtree(stage_root, ignore_errors=True)
    vdir = stage / "versions" / version
    py = vdir / "python"
    log(f"building One Production {version}")
    with zipfile.ZipFile(embeddable_python(cache)) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    # sys.path of the embeddable build is exactly this file: its zip, itself, the libraries,
    # and the version folder (..) so that `python -m app` finds One Production
    pth.write_text(f"{pth.stem}.zip\n.\nLib\\site-packages\n..\nimport site\n", encoding="utf-8")
    log("installing One Production's libraries (from requirements-runtime.lock)")
    subprocess.run(["uv", "pip", "install", "--quiet", "--target", str(py / "Lib" / "site-packages"),
                    "--python-version", "3.12", "--python-platform", "x86_64-pc-windows-msvc",
                    "-r", str(ROOT / "requirements-runtime.lock")],
                   check=True, cwd=ROOT)
    for name in SHIP:
        if (ROOT / name).exists():
            copy_tree(ROOT / name, vdir / name)
    (vdir / "docs").mkdir()
    shutil.copy2(ROOT / "docs" / "CHANGELOG.md", vdir / "docs" / "CHANGELOG.md")
    ver_file = vdir / "app" / "version.py"
    ver_file.write_text(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"',
                               ver_file.read_text(encoding="utf-8")), encoding="utf-8")
    (vdir / "VERSION").write_text(version, encoding="utf-8")
    (stage / "One Production.cmd").write_text(LAUNCHER.replace("\n", "\r\n"), encoding="ascii")
    (stage / "READ ME FIRST.txt").write_text(README.format(version=version).replace("\n", "\r\n"), encoding="utf-8")
    (stage / "current.txt").write_text(version, encoding="ascii")
    (stage / "install.json").write_text(json.dumps({"current": version, "previous": None,
                                                    "update_source": update_source}, indent=1), encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"OneProduction-{version}-win64.zip"
    log(f"zipping -> {zpath.name}")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage_root).as_posix())
    manifest = {"app": "One Production", "version": version, "file": zpath.name, "sha256": sha256_of(zpath),
                "size": zpath.stat().st_size, "python": PY_VERSION, "notes": changelog_notes(version)}
    (out / f"OneProduction-{version}-win64.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log(f"done: {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)")
    return zpath


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from app.version import __version__
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=__version__)
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--cache", default=str(ROOT / "build" / "cache"))
    ap.add_argument("--stage", default=str(ROOT / "build" / "stage"))
    ap.add_argument("--update-source", default=None, help="where installed copies look for updates")
    a = ap.parse_args()
    build(a.version, Path(a.out), Path(a.cache), a.update_source, Path(a.stage))


if __name__ == "__main__":
    main()
