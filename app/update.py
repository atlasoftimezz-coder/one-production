"""Updates and rollback (plan R15) for the packaged app.

A packaged install looks like this (tools/build_package.py):

    <install>\\current.txt            the version that runs
    <install>\\install.json           {"current", "previous", "update_source"}
    <install>\\versions\\<version>\\   its own Python + One Production

An update never touches the version that is running, the launcher, or the data folder:
  1. read the manifest (latest.json, or OneProduction-<v>-win64.json) from the update source -
     a web address or a folder (e.g. a shared drive)
  2. download the zip (resumable, sha256 from the manifest must match)
  3. unpack only versions\\<new> next to the current one, check it is complete
  4. switch: previous = current, current = new; the app exits with code 75 and the launcher
     starts the new version. Rollback is the same switch the other way.
Only the current and the previous version are kept.

A development copy (run from the git checkout) has no install.json and never updates itself.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .installer import Cancelled, Ctx, InstallError, Item, download
from .version import __version__

RELAUNCH = 75            # exit code: the launcher starts current.txt's version again
KEEP = 2


def parse_version(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v or "0"))


def install_root() -> Path | None:
    """<install> when running from a package (versions\\<v>\\app\\update.py), else None."""
    here = Path(__file__).resolve()
    root = here.parents[3] if len(here.parents) > 3 else None
    if root and (root / "install.json").is_file() and (root / "versions").is_dir():
        return root
    return None


class Updater:
    def __init__(self, root: Path | None = None, opener=urllib.request.urlopen):
        self.root = root if root is not None else install_root()
        self.opener = opener
        self.state: dict = {"running": False}
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()

    # ---------------------------------------------------------------- install.json
    @property
    def packaged(self) -> bool:
        return self.root is not None

    def _load(self) -> dict:
        try:
            return json.loads((self.root / "install.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self, d: dict) -> None:
        (self.root / "install.json").write_text(json.dumps(d, indent=1), encoding="utf-8")

    def info(self) -> dict:
        if not self.packaged:
            return {"packaged": False, "current": __version__,
                    "message": "This is a development copy - it's updated with git, not from here."}
        d = self._load()
        versions = sorted((p.name for p in (self.root / "versions").iterdir() if p.is_dir() and not p.name.endswith(".tmp")),
                          key=parse_version)
        return {"packaged": True, "current": d.get("current") or __version__, "previous": d.get("previous"),
                "update_source": d.get("update_source"), "versions": versions, "root": str(self.root),
                "state": self.state}

    def set_source(self, source: str | None) -> dict:
        if not self.packaged:
            raise InstallError("a development copy doesn't update itself")
        source = (source or "").strip().strip('"') or None
        if source and not (re.match(r"^https?://", source) or Path(source).is_absolute()):
            raise InstallError("give a web address (https://...) or a full folder path")
        d = self._load()
        d["update_source"] = source
        self._save(d)
        return self.info()

    # ---------------------------------------------------------------- the manifest
    def _read(self, where: str) -> bytes:
        if re.match(r"^https?://", where):
            with self.opener(urllib.request.Request(where, headers={"User-Agent": "OneProduction-update"}),
                             timeout=30) as r:
                return r.read()
        return Path(where).read_bytes()

    def _manifest_location(self, source: str) -> str:
        if re.match(r"^https?://", source):
            return source if source.endswith(".json") else source.rstrip("/") + "/latest.json"
        p = Path(source)
        return str(p if p.suffix == ".json" else p / "latest.json")

    def check(self) -> dict:
        if not self.packaged:
            return {**self.info(), "newer": False}
        source = self._load().get("update_source")
        if not source:
            return {**self.info(), "newer": False,
                    "message": "No update source yet - paste the link or folder you were given."}
        where = self._manifest_location(source)
        try:
            m = json.loads(self._read(where))
        except Exception as e:                         # noqa: BLE001 - shown to the user
            raise InstallError(f"couldn't read the update information at {where}: {e}")
        for k in ("version", "file", "sha256"):
            if k not in m:
                raise InstallError(f"the update information has no {k!r}")
        current = self.info()["current"]
        newer = parse_version(m["version"]) > parse_version(current)
        return {**self.info(), "latest": m["version"], "newer": newer, "notes": m.get("notes", ""),
                "size": m.get("size"), "manifest": where, "file": self._join(where, m["file"]),
                "sha256": m["sha256"]}

    @staticmethod
    def _join(manifest_at: str, name: str) -> str:
        if re.match(r"^https?://", manifest_at):
            return urllib.parse.urljoin(manifest_at, name)
        return str(Path(manifest_at).parent / name)

    # ---------------------------------------------------------------- downloading + unpacking
    def start_download(self) -> dict:
        if self.thread and self.thread.is_alive():
            raise InstallError("already downloading")
        c = self.check()
        if not c.get("newer"):
            raise InstallError("you already have the newest version")
        self.stop.clear()
        self.state = {"running": True, "version": c["latest"], "progress": 0.0, "detail": "starting", "error": None}
        self.thread = threading.Thread(target=self._download, args=(c,), daemon=True)
        self.thread.start()
        return self.state

    def cancel(self) -> bool:
        if self.thread and self.thread.is_alive():
            self.stop.set()
            return True
        return False

    def _download(self, c: dict) -> None:
        item = Item("update", f"One Production {c['latest']}", "", 0, "", lambda x: None, lambda: True)
        ctx = Ctx(item, self.stop)
        orig = ctx.progress

        def progress(frac, detail=""):
            orig(frac, detail)
            self.state.update(progress=round(frac, 3), detail=detail)
        ctx.progress = progress
        downloads = self.root / "downloads"
        downloads.mkdir(exist_ok=True)
        zpath = downloads / Path(urllib.parse.urlparse(c["file"]).path if c["file"].startswith("http") else c["file"]).name
        try:
            if c["file"].startswith("http"):
                download(c["file"], zpath, ctx, c["sha256"], f"One Production {c['latest']}", opener=self.opener)
            else:
                shutil.copy2(c["file"], zpath)
                from .installer import sha256_of
                if sha256_of(zpath).lower() != c["sha256"].lower():
                    zpath.unlink(missing_ok=True)
                    raise InstallError("the update file is damaged (checksum mismatch)")
            self.state.update(detail="unpacking")
            self._unpack(zpath, c["latest"])
            zpath.unlink(missing_ok=True)
            self.state.update(running=False, ready=c["latest"], progress=1.0, detail="ready - restart to use it")
        except Cancelled:
            self.state.update(running=False, error="cancelled - press Download to continue where it stopped")
        except Exception as e:                         # noqa: BLE001
            self.state.update(running=False, error=str(e)[:500])

    def _unpack(self, zpath: Path, version: str) -> Path:
        prefix = f"OneProduction/versions/{version}/"
        target = self.root / "versions" / version
        tmp = self.root / "versions" / f"{version}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if n.startswith(prefix)]
            if not names:
                raise InstallError(f"the update doesn't contain version {version}")
            for n in names:
                rel = n[len(prefix):]
                if not rel or n.endswith("/"):
                    continue
                dest = (tmp / rel).resolve()
                if tmp.resolve() not in dest.parents:
                    raise InstallError(f"unsafe path in the update: {n}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
        if not (tmp / "python" / "python.exe").is_file() or not (tmp / "app" / "__init__.py").is_file():
            shutil.rmtree(tmp, ignore_errors=True)
            raise InstallError("the update is incomplete")
        if (tmp / "VERSION").read_text(encoding="utf-8").strip() != version:
            shutil.rmtree(tmp, ignore_errors=True)
            raise InstallError("the update says it is a different version")
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
        return target

    # ---------------------------------------------------------------- switching
    def _switch(self, to: str, previous: str | None) -> dict:
        if not (self.root / "versions" / to / "python" / "python.exe").is_file():
            raise InstallError(f"version {to} isn't on this PC")
        d = self._load()
        d["current"], d["previous"] = to, previous
        self._save(d)
        (self.root / "current.txt").write_text(to, encoding="ascii")
        self._prune(keep={to, previous})
        return self.info()

    def apply(self) -> dict:
        ready = self.state.get("ready")
        if not ready:
            raise InstallError("download the update first")
        return self._switch(ready, self.info()["current"])

    def rollback(self) -> dict:
        d = self._load()
        prev = d.get("previous")
        if not prev:
            raise InstallError("there is no previous version to go back to")
        return self._switch(prev, d.get("current"))

    def _prune(self, keep: set) -> None:
        for p in (self.root / "versions").iterdir():
            if p.is_dir() and p.name not in keep and not p.name.endswith(".tmp"):
                if p.name == __version__:
                    continue                           # never delete the version that's running now
                shutil.rmtree(p, ignore_errors=True)
