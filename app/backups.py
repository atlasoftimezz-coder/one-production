"""Backups of project data (plan R11).

A backup is one zip in the backup folder (default: data_dir/backups; the user can pick any
folder, ideally on another drive):

    oneprod-<yyyymmdd-hhmmss>-<daily|manual>.zip
        manifest.json                      what is inside, with a sha256 per file
        projects/<id>/...                  each project folder: chat, files, handoffs
        voicejobs/<job>.json               voiceover records, so voice cards come back too

Rules:
  - every text file goes through the one redaction function (R6): keys and tokens become
    [REDACTED:...]; a file with nothing to redact is stored byte for byte
  - audio/video can be left out (setting); a restore then keeps those files from the
    current copy of the project when it exists
  - written to <name>.part and renamed when complete, so a half-written zip never counts
  - the newest `keep` backups are kept (default 14), older ones are deleted
  - restore never overwrites in place: the current project folder moves to the trash
    folder first (nothing is erased), every file is checked against its sha256, and a
    failed restore puts the current folder back
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from .version import __version__

MEDIA = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm",
         ".avi", ".m4v"}
TEXT = {".md", ".txt", ".json", ".jsonl", ".srt", ".vtt", ".csv", ".html", ".htm", ".xml", ".yaml",
        ".yml", ".toml", ".log", ".py", ".js", ".css", ".ini"}
TEXT_LIMIT = 50 * 1024 * 1024          # bigger "text" files are stored as they are
NAME_RE = re.compile(r"^oneprod-(\d{8}-\d{6})(?:-(\d+))?-(daily|manual)\.zip$")
PID_RE = re.compile(r"^[a-z0-9\-]{1,64}$")
DAY = 24 * 3600
DEFAULTS = {"folder": None, "include_media": True, "keep": 14, "last_daily": 0.0}


class BackupError(Exception):
    pass


class Busy(BackupError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(rel: str) -> str:
    """A path from a manifest, refused if it could escape the folder it is restored into."""
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts or ":" in rel or rel.startswith(("/", "\\")):
        raise BackupError(f"unsafe path in backup: {rel!r}")
    return rel


class Backups:
    def __init__(self, data_dir: Path, store, redact):
        self.data_dir = data_dir
        self.store = store
        self.redact = redact
        self.cfg_file = data_dir / "backup.json"
        self.jobs_dir = data_dir / "cache" / "voicejobs"
        self._lock = threading.Lock()                     # one backup or restore at a time

    # ---------------------------------------------------------------- settings
    def settings(self) -> dict:
        cfg = dict(DEFAULTS)
        if self.cfg_file.exists():
            try:
                cfg.update(json.loads(self.cfg_file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        cfg["folder_resolved"] = str(self.folder(cfg))
        return cfg

    def folder(self, cfg: dict | None = None) -> Path:
        cfg = cfg or self.settings()
        return Path(cfg["folder"]) if cfg.get("folder") else self.data_dir / "backups"

    def _save(self, cfg: dict) -> None:
        keep = {k: cfg[k] for k in DEFAULTS}
        self.cfg_file.write_text(json.dumps(keep, indent=1), encoding="utf-8")

    def update(self, folder: str | None = None, include_media: bool | None = None,
               keep: int | None = None) -> dict:
        cfg = self.settings()
        if folder is not None:
            folder = folder.strip().strip('"')
            if folder == "":
                cfg["folder"] = None                     # back to the default
            else:
                p = Path(os.path.expandvars(folder)).expanduser()
                if not p.is_absolute():
                    raise BackupError("give the full folder path, e.g. D:\\Backups\\one-production")
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    probe = p / f".write-test-{secrets.token_hex(4)}"
                    probe.write_bytes(b"ok")
                    probe.unlink()
                except OSError as e:
                    raise BackupError(f"can't write to that folder: {e.strerror or e}")
                cfg["folder"] = str(p)
        if include_media is not None:
            cfg["include_media"] = bool(include_media)
        if keep is not None:
            if not 1 <= int(keep) <= 365:
                raise BackupError("keep between 1 and 365 backups")
            cfg["keep"] = int(keep)
        self._save(cfg)
        return self.settings()

    # ---------------------------------------------------------------- making one
    def busy(self) -> bool:
        return self._lock.locked()

    def create(self, kind: str = "manual", include_media: bool | None = None) -> dict:
        if not self._lock.acquire(blocking=False):
            raise Busy("a backup or restore is already running")
        try:
            return self._create(kind, include_media)
        finally:
            self._lock.release()

    def _create(self, kind: str, include_media: bool | None) -> dict:
        cfg = self.settings()
        media = cfg["include_media"] if include_media is None else include_media
        folder = self.folder(cfg)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # same-second backups get a counter that only goes up (a pruned name is never reused,
        # or the new backup would sort as the oldest and be pruned straight away)
        used = [int(m.group(2) or 1) for p in folder.iterdir()
                if (m := NAME_RE.match(p.name.removesuffix(".part"))) and m.group(1) == stamp]
        n = max(used, default=0) + 1
        name = f"oneprod-{stamp}-{kind}.zip" if n == 1 else f"oneprod-{stamp}-{n}-{kind}.zip"
        part = folder / (name + ".part")
        manifest = {"app": "One Production", "version": __version__, "created": time.time(), "kind": kind,
                    "include_media": media, "projects": []}
        try:
            with zipfile.ZipFile(part, "w", allowZip64=True) as z:
                for p in self.store.list_projects():
                    manifest["projects"].append(self._add_project(z, p, media))
                z.writestr("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False),
                           compress_type=zipfile.ZIP_DEFLATED)
            part.replace(folder / name)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if kind == "daily":
            cfg["last_daily"] = manifest["created"]
            self._save(cfg)
        self.prune()
        return self.info(folder / name)

    def _add_project(self, z: zipfile.ZipFile, p: dict, media: bool) -> dict:
        pid = p["id"]
        pdir = self.store.project_dir(pid)
        entry = {"id": pid, "name": p.get("name", pid), "files": [], "skipped": [], "jobs": []}
        for f in sorted(x for x in pdir.rglob("*") if x.is_file()):
            rel = f.relative_to(pdir).as_posix()
            if rel.endswith(".part"):
                continue
            ext = f.suffix.lower()
            size = f.stat().st_size
            if ext in MEDIA and not media:
                entry["skipped"].append({"path": rel, "size": size})
                continue
            arc = f"projects/{pid}/{rel}"
            entry["files"].append(self._add_file(z, f, arc, rel, ext, size))
        jobs = []
        if self.jobs_dir.exists():
            for jf in self.jobs_dir.glob("*.json"):
                try:
                    if json.loads(jf.read_text(encoding="utf-8")).get("project") == pid:
                        jobs.append(jf)
                except (OSError, ValueError):
                    continue
        for jf in jobs:
            for extra in sorted(self.jobs_dir.glob(jf.stem + "*")):     # the job record + its script copy
                if extra.is_file():
                    self._add_file(z, extra, f"voicejobs/{extra.name}", extra.name, extra.suffix.lower(),
                                   extra.stat().st_size)
                    entry["jobs"].append(extra.name)
        return entry

    def _add_file(self, z, f: Path, arc: str, rel: str, ext: str, size: int) -> dict:
        compress = zipfile.ZIP_STORED if ext in MEDIA else zipfile.ZIP_DEFLATED
        if ext in TEXT and size <= TEXT_LIMIT:
            raw = f.read_bytes()
            data, redacted = raw, False
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                clean = self.redact(text)
                if clean != text:
                    data, redacted = clean.encode("utf-8"), True
            info = zipfile.ZipInfo(arc, time.localtime(f.stat().st_mtime)[:6])
            info.compress_type = compress
            z.writestr(info, data)
            return {"path": rel, "size": len(data), "sha256": _sha(data), "redacted": redacted}
        z.write(f, arc, compress_type=compress)
        return {"path": rel, "size": size, "sha256": _sha_file(f), "redacted": False}

    def prune(self) -> list[str]:
        keep = int(self.settings()["keep"])
        zips = self._zips()
        gone = []
        for path in zips[keep:]:
            try:
                path.unlink()
                gone.append(path.name)
            except OSError:
                pass
        return gone

    # ---------------------------------------------------------------- listing
    def _zips(self) -> list[Path]:
        folder = self.folder()
        if not folder.exists():
            return []
        found = [p for p in folder.iterdir() if p.is_file() and NAME_RE.match(p.name)]

        def order(p: Path):                      # time, then the counter for same-second backups
            m = NAME_RE.match(p.name)
            return m.group(1), int(m.group(2) or 1)
        return sorted(found, key=order, reverse=True)

    def manifest(self, name: str) -> dict:
        path = self._path(name)
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json"))

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise BackupError("not a One Production backup name")
        path = self.folder() / name
        if not path.is_file():
            raise BackupError("that backup is not in the backup folder")
        return path

    def info(self, path: Path) -> dict:
        try:
            with zipfile.ZipFile(path) as z:
                m = json.loads(z.read("manifest.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            return {"name": path.name, "broken": True, "size": path.stat().st_size}
        return {"name": path.name, "kind": m.get("kind"), "created": m.get("created"), "size": path.stat().st_size,
                "include_media": m.get("include_media"), "version": m.get("version"),
                "projects": [{"id": p["id"], "name": p.get("name"), "files": len(p["files"]),
                              "skipped": len(p["skipped"]),
                              "bytes": sum(f["size"] for f in p["files"]),
                              "exists": self._exists(p["id"])} for p in m.get("projects", [])]}

    def _exists(self, pid: str) -> bool:
        return (self.store.root / pid / "project.json").is_file()

    def list(self) -> list[dict]:
        return [self.info(p) for p in self._zips()]

    # ---------------------------------------------------------------- restoring
    def restore(self, name: str, pid: str) -> dict:
        if not PID_RE.match(pid or ""):
            raise BackupError("not a project id")
        if not self._lock.acquire(blocking=False):
            raise Busy("a backup or restore is already running")
        try:
            return self._restore(self._path(name), pid)
        finally:
            self._lock.release()

    def _restore(self, path: Path, pid: str) -> dict:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read("manifest.json"))
            entry = next((p for p in m.get("projects", []) if p["id"] == pid), None)
            if not entry:
                raise BackupError("that project is not in this backup")
            target = self.store.root / pid
            tmp = self.store.root / f".restore-{pid}-{secrets.token_hex(3)}"
            tmp.mkdir()
            previous = None
            try:
                for f in entry["files"]:
                    rel = _safe_rel(f["path"])
                    dest = (tmp / rel).resolve()
                    if tmp.resolve() not in dest.parents:
                        raise BackupError(f"unsafe path in backup: {rel!r}")
                    data = z.read(f"projects/{pid}/{rel}")
                    if _sha(data) != f["sha256"]:
                        raise BackupError(f"{rel} is damaged in this backup (checksum mismatch)")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                if not (tmp / "project.json").is_file():
                    raise BackupError("this backup has no project.json for that project")
                if target.exists():
                    previous = self.store.delete_project(pid)        # current copy -> trash, nothing erased
                kept, missing = [], []
                for s in entry["skipped"]:
                    rel = _safe_rel(s["path"])
                    src = previous / rel if previous else None
                    if src and src.is_file():
                        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, tmp / rel)
                        kept.append(rel)
                    else:
                        missing.append(rel)
                tmp.rename(target)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                if previous and not target.exists():
                    previous.rename(target)                          # put the current copy back
                raise
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            for j in entry.get("jobs", []):
                dest = self.jobs_dir / _safe_rel(j)
                if not dest.exists():
                    dest.write_bytes(z.read(f"voicejobs/{j}"))
        return {"restored": pid, "name": entry.get("name"), "files": len(entry["files"]),
                "from": path.name, "previous": str(previous) if previous else None,
                "kept_media": kept, "missing_media": missing}

    # ---------------------------------------------------------------- daily
    def due(self, now: float | None = None) -> bool:
        now = now or time.time()
        last = float(self.settings().get("last_daily") or 0)
        return now - last >= DAY and bool(self.store.list_projects())
