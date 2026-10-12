"""Custom storage (owner, 2026-10-12): finished episodes copied out of the PC, and files pulled into a
project from storage. Two locations:

  internal   a folder the owner picks (another drive, a NAS share, an external disk)
  drive      the owner's Google Drive (their 5 TB plan) through the Drive API - owner's choice "Drive API
             only", no Drive for desktop and no MCP in between

Google Drive uses the owner's OWN OAuth client (type "Desktop app" in their Google Cloud project, the
same kind as the Google sign-in), the loopback + PKCE flow of google_signin.py, and two scopes:
  drive.file      create folders and upload into them (only what the app made)
  drive.readonly  browse the Drive to import a file
The client secret and the refresh token live in Windows Credential Manager; settings.json keeps only
the client ID, the folder name, the "copy finished episodes" switches and the account's email.

Episodes land in  <folder>/<project name>/<episode folder>/...  on both locations. Uploads are resumable
(8 MB chunks), so a long render survives a slow line.
"""
from __future__ import annotations

import re
import secrets as pysecrets
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
import keyring

from .accounts import KEYRING_SERVICE
from .google_signin import AUTH_URL, CLIENT_ID_END, TOKEN_URL, WAIT, _Listener, _pkce

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME = "application/vnd.google-apps.folder"
SECRET_NAME = "drive-oauth-secret"
REFRESH_NAME = "drive-refresh-token"
CHUNK = 8 * 1024 * 1024                       # a multiple of 256 KiB, as Drive's resumable upload wants
IMPORT_DIR = "imports"
SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StorageError(RuntimeError):
    pass


def safe_name(name: str) -> str:
    out = SAFE.sub("_", str(name or "")).strip(" .")
    return out[:120] or "file"


def default_config() -> dict:
    return {"internal": {"path": "", "auto": False}, "drive": {"client_id": "", "folder": "One Production",
                                                               "auto": False, "email": ""}}


class Storage:
    def __init__(self, settings, save_setting, store, transport: httpx.BaseTransport | None = None):
        self.settings, self.save_setting, self.store, self.transport = settings, save_setting, store, transport
        self.pending: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- settings
    def config(self) -> dict:
        cfg = default_config()
        saved = getattr(self.settings, "storage", None) or {}
        for k in cfg:
            cfg[k].update({kk: vv for kk, vv in (saved.get(k) or {}).items() if kk in cfg[k]})
        return cfg

    def _save(self, cfg: dict) -> None:
        self.settings.storage = cfg
        self.save_setting("storage", cfg)

    def status(self) -> dict:
        cfg = self.config()
        path = cfg["internal"]["path"]
        internal = {**cfg["internal"], "ok": bool(path) and Path(path).is_dir()}
        drive = {**cfg["drive"], "has_secret": bool(keyring.get_password(KEYRING_SERVICE, SECRET_NAME)),
                 "connected": bool(keyring.get_password(KEYRING_SERVICE, REFRESH_NAME))}
        return {"internal": internal, "drive": drive, "jobs": list(self.jobs.values())[-10:]}

    def set_internal(self, path: str, auto: bool) -> dict:
        cfg = self.config()
        path = (path or "").strip().strip('"')
        if path:
            p = Path(path)
            if not p.is_absolute():
                raise StorageError("Give the full folder path, e.g. E:\\One Production exports")
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / f".oneprod-{uuid.uuid4().hex[:8]}"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as e:
                raise StorageError(f"Can't write to {path}: {e.strerror or e}") from None
        cfg["internal"] = {"path": path, "auto": bool(auto and path)}
        self._save(cfg)
        return self.status()

    def set_drive(self, client_id: str, client_secret: str | None, folder: str, auto: bool) -> dict:
        cfg = self.config()
        client_id = (client_id or "").strip()
        if client_id and not client_id.endswith(CLIENT_ID_END):
            raise StorageError(f"That isn't an OAuth client ID - it ends with {CLIENT_ID_END}")
        if client_id != cfg["drive"]["client_id"]:
            self._forget_login()
        if client_secret:
            keyring.set_password(KEYRING_SERVICE, SECRET_NAME, client_secret.strip())
        folder = safe_name(folder or "One Production")
        cfg["drive"].update(client_id=client_id, folder=folder, auto=bool(auto and client_id))
        if not client_id:
            cfg["drive"]["email"] = ""
        self._save(cfg)
        return self.status()

    def _forget_login(self) -> None:
        self._token = None
        try:
            keyring.delete_password(KEYRING_SERVICE, REFRESH_NAME)
        except keyring.errors.PasswordDeleteError:
            pass

    def disconnect(self) -> dict:
        self._forget_login()
        cfg = self.config()
        cfg["drive"].update(email="", auto=False)
        self._save(cfg)
        return self.status()

    # ---------------------------------------------------------------- Google Drive sign-in (loopback + PKCE)
    def _client(self, timeout: float = 60) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=True)

    def connect(self) -> dict:
        cfg = self.config()["drive"]
        if not cfg["client_id"] or not keyring.get_password(KEYRING_SERVICE, SECRET_NAME):
            raise StorageError("Add your OAuth client ID and secret first.")
        state = pysecrets.token_urlsafe(24)
        verifier, challenge = _pkce()
        listener = _Listener(state)
        redirect = f"http://127.0.0.1:{listener.port}/"
        with self._lock:
            self.pending[state] = {"listener": listener, "verifier": verifier, "redirect": redirect,
                                   "started": time.time()}
        url = AUTH_URL + "?" + urlencode({
            "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent"})
        return {"url": url, "state": state}

    def connect_status(self, state: str) -> dict:
        with self._lock:
            p = self.pending.get(state)
        if not p:
            raise StorageError("That sign-in is over - start it again.")
        lst = p["listener"]
        if lst.error:
            self._end(state)
            raise StorageError(f"Google said: {lst.error}")
        if not lst.code:
            if time.time() - p["started"] > WAIT:
                self._end(state)
                raise StorageError("The sign-in timed out - start it again.")
            return {"connected": False}
        try:
            self._exchange(lst.code, p)
        finally:
            self._end(state)
        return {"connected": True, **self.status()}

    def _end(self, state: str) -> None:
        with self._lock:
            p = self.pending.pop(state, None)
        if p:
            p["listener"].close()

    def _exchange(self, code: str, p: dict) -> None:
        cfg = self.config()
        with self._client() as c:
            r = c.post(TOKEN_URL, data={"code": code, "client_id": cfg["drive"]["client_id"],
                                        "client_secret": keyring.get_password(KEYRING_SERVICE, SECRET_NAME) or "",
                                        "redirect_uri": p["redirect"], "grant_type": "authorization_code",
                                        "code_verifier": p["verifier"]})
        if r.status_code != 200:
            raise StorageError(f"Google refused the sign-in ({r.status_code}): {r.text[:200]}")
        tok = r.json()
        if not tok.get("refresh_token"):
            raise StorageError("Google sent no refresh token - remove One Production from your Google account's "
                               "third-party access and connect again.")
        keyring.set_password(KEYRING_SERVICE, REFRESH_NAME, tok["refresh_token"])
        self._token = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
        about = self._get(f"{API}/about", params={"fields": "user(emailAddress),storageQuota(limit,usage)"})
        cfg["drive"]["email"] = (about.get("user") or {}).get("emailAddress", "")
        self._save(cfg)

    def access_token(self) -> str:
        if self._token and self._token[1] > time.time():
            return self._token[0]
        refresh = keyring.get_password(KEYRING_SERVICE, REFRESH_NAME)
        if not refresh:
            raise StorageError("Google Drive isn't connected - open Storage and press Connect Google Drive.")
        cfg = self.config()["drive"]
        with self._client() as c:
            r = c.post(TOKEN_URL, data={"client_id": cfg["client_id"], "refresh_token": refresh,
                                        "client_secret": keyring.get_password(KEYRING_SERVICE, SECRET_NAME) or "",
                                        "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise StorageError("Google Drive needs connecting again (the sign-in was revoked or expired).")
        tok = r.json()
        self._token = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
        return self._token[0]

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def _get(self, url: str, params: dict | None = None) -> dict:
        with self._client() as c:
            r = c.get(url, params=params, headers=self._auth())
        if r.status_code != 200:
            raise StorageError(f"Google Drive: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def quota(self) -> dict:
        q = self._get(f"{API}/about", params={"fields": "storageQuota(limit,usage)"}).get("storageQuota") or {}
        return {"limit": int(q.get("limit") or 0), "usage": int(q.get("usage") or 0)}

    # ---------------------------------------------------------------- browse
    def browse(self, location: str, where: str = "") -> dict:
        if location == "internal":
            root = self._internal_root()
            here = (root / where).resolve() if where else root
            if root != here and root not in here.parents:
                raise StorageError("That folder is outside the storage folder.")
            if not here.is_dir():
                raise StorageError("No such folder.")
            items = [{"id": str(p.relative_to(root)).replace("\\", "/"), "name": p.name, "folder": p.is_dir(),
                      "size": None if p.is_dir() else p.stat().st_size}
                     for p in sorted(here.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                     if not p.name.startswith(".")]
            return {"location": location, "where": str(here.relative_to(root)).replace("\\", "/").strip("."),
                    "items": items[:500]}
        if location == "drive":
            parent = where or "root"
            items, token = [], None
            while len(items) < 500:
                q = {"q": f"'{parent}' in parents and trashed=false", "pageSize": 200, "orderBy": "folder,name",
                     "fields": "nextPageToken,files(id,name,mimeType,size)"}
                if token:
                    q["pageToken"] = token
                page = self._get(f"{API}/files", params=q)
                for f in page.get("files") or []:
                    items.append({"id": f["id"], "name": f["name"], "folder": f["mimeType"] == FOLDER_MIME,
                                  "size": int(f["size"]) if f.get("size") else None,
                                  "google_doc": f["mimeType"].startswith("application/vnd.google-apps.")
                                  and f["mimeType"] != FOLDER_MIME})
                token = page.get("nextPageToken")
                if not token:
                    break
            return {"location": location, "where": parent, "items": items}
        raise StorageError("location is internal or drive")

    def _internal_root(self) -> Path:
        path = self.config()["internal"]["path"]
        if not path or not Path(path).is_dir():
            raise StorageError("Pick an internal storage folder first.")
        return Path(path).resolve()

    # ---------------------------------------------------------------- import into a project
    def import_files(self, pid: str, location: str, ids: list[str]) -> dict:
        dest = self.store.files_dir(pid) / IMPORT_DIR
        dest.mkdir(parents=True, exist_ok=True)
        done, skipped = [], []
        for fid in ids[:50]:
            if location == "internal":
                root = self._internal_root()
                src = (root / fid).resolve()
                if root not in src.parents or not src.is_file():
                    skipped.append(f"{fid}: not a file in the storage folder")
                    continue
                target = _free(dest / safe_name(src.name))
                shutil.copy2(src, target)
            else:
                meta = self._get(f"{API}/files/{fid}", params={"fields": "id,name,mimeType,size"})
                if meta["mimeType"].startswith("application/vnd.google-apps."):
                    skipped.append(f"{meta['name']}: a Google Docs/Sheets file - download it as a normal file first")
                    continue
                target = _free(dest / safe_name(meta["name"]))
                with self._client(timeout=None) as c, c.stream("GET", f"{API}/files/{fid}", params={"alt": "media"},
                                                               headers=self._auth()) as r:
                    if r.status_code != 200:
                        skipped.append(f"{meta['name']}: HTTP {r.status_code}")
                        continue
                    with open(target, "wb") as fh:
                        for chunk in r.iter_bytes(1024 * 1024):
                            fh.write(chunk)
            done.append(f"{IMPORT_DIR}/{target.name}")
        return {"imported": done, "skipped": skipped}

    # ---------------------------------------------------------------- export (finished episodes)
    def auto_locations(self) -> list[str]:
        cfg = self.config()
        out = []
        if cfg["internal"]["auto"] and cfg["internal"]["path"]:
            out.append("internal")
        if cfg["drive"]["auto"] and keyring.get_password(KEYRING_SERVICE, REFRESH_NAME):
            out.append("drive")
        return out

    def export(self, pid: str, paths: list[str], dest_name: str, locations: list[str]) -> list[dict]:
        """Start one background copy per location; returns the jobs (GET /api/storage shows progress)."""
        files_dir = self.store.files_dir(pid)
        project = safe_name(self.store.project(pid).get("name") or pid)
        items: list[tuple[Path, str]] = []
        for rel in paths:
            src = (files_dir / rel).resolve()
            if files_dir.resolve() not in src.parents:
                raise StorageError(f"{rel} is outside the project")
            if src.is_dir():
                items += [(f, str(f.relative_to(src.parent)).replace("\\", "/")) for f in sorted(src.rglob("*")) if f.is_file()]
            elif src.is_file():
                items.append((src, src.name))
        if not items:
            raise StorageError("Nothing to copy.")
        jobs = []
        for loc in locations:
            if loc not in ("internal", "drive"):
                raise StorageError("location is internal or drive")
            job = {"id": uuid.uuid4().hex[:10], "location": loc, "project": pid, "name": dest_name,
                   "files": len(items), "done": 0, "bytes": sum(f.stat().st_size for f, _ in items), "sent": 0,
                   "state": "running", "error": "", "started": time.time()}
            self.jobs[job["id"]] = job
            threading.Thread(target=self._run_export, args=(job, items, project, safe_name(dest_name)),
                             daemon=True).start()
            jobs.append(job)
        return jobs

    def _run_export(self, job: dict, items: list[tuple[Path, str]], project: str, dest: str) -> None:
        try:
            if job["location"] == "internal":
                base = self._internal_root() / project / dest
                for src, rel in items:
                    target = base / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
                    job["done"] += 1
                    job["sent"] += src.stat().st_size
                job["where"] = str(base)
            else:
                cfg = self.config()["drive"]
                folders: dict[str, str] = {}
                top = self._folder(self._folder("root", cfg["folder"]), project)
                base = self._folder(top, dest)
                for src, rel in items:
                    parent = base
                    for part in rel.split("/")[:-1]:
                        key = f"{parent}/{part}"
                        if key not in folders:
                            folders[key] = self._folder(parent, part)
                        parent = folders[key]
                    self._upload(src, parent, job)
                    job["done"] += 1
                job["where"] = f"Google Drive: {cfg['folder']}/{project}/{dest}"
            job["state"] = "done"
        except (StorageError, OSError, httpx.HTTPError) as e:
            job.update(state="failed", error=str(e)[:300])
        except Exception as e:                       # noqa: BLE001 - a copy never stays "running" forever
            job.update(state="failed", error=f"{e.__class__.__name__}: {e}"[:300])
        job["ended"] = time.time()

    def _folder(self, parent: str, name: str) -> str:
        """Find (made by this app) or create a folder under parent; returns its id."""
        q = (f"name='{name.replace(chr(39), chr(92) + chr(39))}' and '{parent}' in parents and "
             f"mimeType='{FOLDER_MIME}' and trashed=false")
        found = self._get(f"{API}/files", params={"q": q, "fields": "files(id)", "pageSize": 1}).get("files") or []
        if found:
            return found[0]["id"]
        with self._client() as c:
            r = c.post(f"{API}/files", params={"fields": "id"}, headers=self._auth(),
                       json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]})
        if r.status_code != 200:
            raise StorageError(f"Google Drive would not create the folder {name}: HTTP {r.status_code}")
        return r.json()["id"]

    def _upload(self, src: Path, parent: str, job: dict) -> str:
        size = src.stat().st_size
        with self._client(timeout=300) as c:
            r = c.post(UPLOAD, params={"uploadType": "resumable", "fields": "id"},
                       headers={**self._auth(), "X-Upload-Content-Length": str(size)},
                       json={"name": src.name, "parents": [parent]})
            if r.status_code != 200 or not r.headers.get("location"):
                raise StorageError(f"Google Drive refused the upload of {src.name}: HTTP {r.status_code}")
            session = r.headers["location"]
            sent = 0
            with open(src, "rb") as fh:
                while True:
                    chunk = fh.read(CHUNK)
                    end = sent + len(chunk) - 1
                    rng = f"bytes {sent}-{end}/{size}" if chunk else f"bytes */{size}"
                    r = c.put(session, content=chunk, headers={"Content-Range": rng})
                    if r.status_code in (200, 201):
                        job["sent"] += len(chunk)
                        return r.json().get("id", "")
                    if r.status_code != 308:
                        raise StorageError(f"Google Drive stopped the upload of {src.name}: HTTP {r.status_code}")
                    got = r.headers.get("range", "")                 # "bytes=0-N": what Drive has
                    sent_now = int(got.rsplit("-", 1)[1]) + 1 if got else sent + len(chunk)
                    job["sent"] += sent_now - sent
                    sent = sent_now
                    fh.seek(sent)


def _free(path: Path) -> Path:
    """path, or "name (2).ext" when that exists."""
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
        n += 1
