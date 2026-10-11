"""Sound effects from Epidemic Sound with the owner's own API key (owner, 2026-10-11: "Use epidamic api key").

Checked against Epidemic Sound's OpenAPI spec (partner-content-api.epidemicsound.com/docs/spec.json,
read 2026-10-11):
    GET https://partner-content-api.epidemicsound.com/v0/sound-effects/search?term=&limit=&offset=&sort=
        {"soundEffects": [{"id": uuid, "title", "added", "length": seconds, "images"}], "pagination", "links"}
    GET https://partner-content-api.epidemicsound.com/v0/sound-effects/{id}/download?format=mp3&quality=normal|high
        {"url", "expires"}    (normal = 128 kbps, enough under a voiceover)
    Header "Authorization: Bearer epidemic_live_..." (+ "x-partner-user-id": a stable anonymous id)

Licence: Epidemic's free API plan is "for prototyping" (a small download cap, no commercial licence).
The owner confirms their plan covers YouTube; the app counts downloads and reuses every file it
already has (data_dir/cache/sfx), so one effect costs one download however often it is used.

The key lives in Windows Credential Manager (OneProduction / epidemic) and never reaches the AI.
Files land in the project's files/sfx/ with credits in sfx/CREDITS.md.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import keyring

from .accounts import KEYRING_SERVICE

KEY_NAME = "epidemic"
API = "https://partner-content-api.epidemicsound.com"     # the spec's "servers" entry (2026-10-11)
MAX_BYTES = 30 * 1024 * 1024
UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")


class SfxError(Exception):
    pass


class Sfx:
    def __init__(self, store, data_dir: Path, transport: httpx.BaseTransport | None = None):
        self.store, self.transport = store, transport
        self.cache = data_dir / "cache" / "sfx"
        self.state_file = data_dir / "sfx.json"
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- key + counters
    @staticmethod
    def key() -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, KEY_NAME)
        except keyring.errors.KeyringError:
            return None

    def _state(self) -> dict:
        try:
            d = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        except ValueError:
            d = {}
        if not d.get("user_id"):
            d["user_id"] = secrets.token_hex(12)        # stable and anonymous: never the owner's email
            d.setdefault("downloads", 0)
            self._save(d)
        return d

    def _save(self, d: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(d, indent=1), encoding="utf-8")

    def status(self) -> dict:
        k = self.key()
        d = self._state()
        return {"provider": "epidemic", "key_set": bool(k), "key_hint": ("…" + k[-4:]) if k else None,
                "downloads": d.get("downloads", 0), "cached": len(list(self.cache.glob("*.mp3"))) if self.cache.exists() else 0,
                "licence_note": "Epidemic's free API plan is for prototyping only (no commercial licence). "
                                "Use a plan that covers your YouTube channel."}

    def set_key(self, key: str) -> dict:
        key = (key or "").strip()
        if not re.fullmatch(r"epidemic_[A-Za-z0-9_\-]{10,200}", key):
            raise SfxError("That doesn't look like an Epidemic Sound API key (it starts with epidemic_live_).")
        self._get("/v0/sound-effects/search", {"term": "whoosh", "limit": 1}, key=key)     # proves it works
        keyring.set_password(KEYRING_SERVICE, KEY_NAME, key)
        return self.status()

    def remove_key(self) -> dict:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
        return self.status()

    # ---------------------------------------------------------------- HTTP
    def _client(self, timeout: float = 30) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=True)

    def _get(self, path: str, params: dict, key: str | None = None) -> dict:
        key = key or self.key()
        if not key:
            raise SfxError("No Epidemic Sound key yet: add it under System > Set up / modules > Sound effects.")
        try:
            with self._client() as c:
                r = c.get(API + path, params=params, headers={"Authorization": f"Bearer {key}",
                                                             "x-partner-user-id": self._state()["user_id"]})
        except httpx.HTTPError as e:
            raise SfxError(f"Epidemic Sound could not be reached ({e.__class__.__name__}).") from None
        if r.status_code in (401, 403):
            raise SfxError("Epidemic Sound refused the key (401/403). Check it in the Epidemic Sound developer portal.")
        if r.status_code == 429:
            raise SfxError("Epidemic Sound's request or download limit is used up for now.")
        if r.status_code != 200:
            raise SfxError(f"Epidemic Sound answered {r.status_code}.")
        try:
            return r.json()
        except ValueError:
            raise SfxError("Epidemic Sound sent something that isn't JSON.") from None

    # ---------------------------------------------------------------- search / fetch
    def search(self, term: str, count: int = 10, max_seconds: float = 0) -> dict:
        term = (term or "").strip()
        if not term:
            raise SfxError("Say what to search for (e.g. whoosh, soft click, riser, impact).")
        d = self._get("/v0/sound-effects/search", {"term": term[:100], "limit": max(1, min(int(count or 10), 60))})
        out = [{"id": s["id"], "title": s.get("title"), "seconds": s.get("length"),
                "cached": (self.cache / f"epidemic-{s['id']}.mp3").exists()}
               for s in d.get("soundEffects") or []
               if not max_seconds or (s.get("length") or 0) <= float(max_seconds)]
        return {"results": out, "term": term}

    def fetch(self, pid: str, sfx_id: str, title: str = "") -> dict:
        sfx_id = str(sfx_id or "").strip()
        if not UUID.match(sfx_id):
            raise SfxError("id must be the id from sfx_search.")
        name = f"epidemic-{sfx_id}.mp3"
        cached = self.cache / name
        downloaded = not cached.exists()
        if downloaded:
            link = self._get(f"/v0/sound-effects/{sfx_id}/download", {"format": "mp3", "quality": "normal"}).get("url")
            u = urlparse(link or "")
            if u.scheme != "https" or not u.hostname:
                raise SfxError("Epidemic Sound gave no usable download link.")
            self.cache.mkdir(parents=True, exist_ok=True)
            part = cached.with_suffix(".part")
            size = 0
            try:
                with self._client(timeout=120) as c, c.stream("GET", link) as r:
                    if r.status_code != 200:
                        raise SfxError(f"The download answered {r.status_code}.")
                    with open(part, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise SfxError("The file is over 30 MB - not a sound effect.")
                            fh.write(chunk)
                if size == 0:
                    raise SfxError("The download was empty.")
                part.replace(cached)
            except httpx.HTTPError as e:
                raise SfxError(f"The download failed ({e.__class__.__name__}).") from None
            finally:
                part.unlink(missing_ok=True)
            with self._lock:
                d = self._state()
                d["downloads"] = d.get("downloads", 0) + 1
                self._save(d)
        folder = self.store.files_dir(pid) / "sfx"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if not target.exists():
            shutil.copyfile(cached, target)
        rel = f"sfx/{name}"
        self._credit(folder, rel, title)
        return {"file": rel, "title": title or None, "downloaded": downloaded}

    def _credit(self, folder: Path, rel: str, title: str) -> None:
        with self._lock:
            data_file = folder / "credits.json"
            try:
                data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.exists() else {}
            except ValueError:
                data = {}
            data.setdefault(rel, {"title": title or "", "source": "Epidemic Sound", "added": time.strftime("%Y-%m-%d")})
            data_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
            lines = ["# Sound effect credits", "", "Sound effects from Epidemic Sound (https://www.epidemicsound.com), "
                     "used under the owner's Epidemic Sound plan.", ""]
            lines += [f"- `{f}` - {c['title'] or 'sound effect'}" for f, c in sorted(data.items())]
            (folder / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
