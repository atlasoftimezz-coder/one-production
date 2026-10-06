"""Stock footage (P10 add-on): Pexels videos and photos, with the owner's own free API key.

Owner, 2026-10-06: no Storyblocks key, so Pexels for now. Pexels has a photo and a video API
(no audio). Verified against https://www.pexels.com/api/documentation/ :
    GET https://api.pexels.com/videos/search?query=&orientation=&size=&per_page=&page=
        {"videos": [{"id", "width", "height", "duration", "url", "image", "user": {"name", "url"},
                     "video_files": [{"quality", "file_type", "width", "height", "fps", "link"}]}]}
    GET https://api.pexels.com/v1/search?query=&orientation=&size=&per_page=&page=
        {"photos": [{"id", "width", "height", "url", "alt", "photographer", "photographer_url",
                     "src": {"original", "large2x", ...}}]}
    Header "Authorization: <key>". Every answer carries X-Ratelimit-Limit / -Remaining / -Reset
    (the owner's key showed a limit of 25,000 on 2026-10-06; the docs' default is 200 an hour,
    20,000 a month) - so the app reports what Pexels says rather than a fixed number.

The key lives in Windows Credential Manager (OneProduction / pexels): never in settings, logs,
backups or diagnostics, and the AI never sees it - it only gets search results and files.
Downloads land in the project's files/stock/ with a credit line in stock/CREDITS.md (Pexels
asks for credit to the photographer and a link back when possible).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import keyring

from .accounts import KEYRING_SERVICE

KEY_NAME = "pexels"
API = "https://api.pexels.com"
ORIENTATIONS = ("landscape", "portrait", "square")
MIN_HEIGHT = 720                 # the video gate wants >= 1280x720
MAX_HEIGHT = 1080                # no 4K: 5-10x the disk for nothing at 1080p output
MAX_BYTES = 400 * 1024 * 1024
DOWNLOAD_HOSTS = (".pexels.com", "player.vimeo.com")
PHOTO_WIDTH = 2560               # the editor moves stills on a 2x crop; originals can be 6000 px+


class StockError(Exception):
    pass


def words_from_page(url: str) -> str:
    """Pexels video pages carry their description in the slug: /video/aerial-view-of-a-river-1234/."""
    slug = urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-?\d+$", "", slug)
    return slug.replace("-", " ").strip()


def best_video_file(files: list[dict]) -> dict | None:
    """An mp4 between 720p and 1080p (by its short side), the largest such one."""
    ok = []
    for f in files or []:
        w, h = int(f.get("width") or 0), int(f.get("height") or 0)
        short = min(w, h) if w and h else 0
        if (f.get("file_type") or "").lower() != "video/mp4" or not f.get("link"):
            continue
        if MIN_HEIGHT <= short <= MAX_HEIGHT:
            ok.append((short, w * h, f))
    if not ok:
        return None
    return max(ok, key=lambda t: (t[0], t[1]))[2]


def allowed_download(url: str) -> bool:
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h.lstrip(".") or host.endswith(h) for h in DOWNLOAD_HOSTS)


class Stock:
    def __init__(self, store, transport: httpx.BaseTransport | None = None):
        self.store = store
        self.transport = transport
        self.rate: dict = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- key
    @staticmethod
    def key() -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, KEY_NAME)
        except keyring.errors.KeyringError:
            return None

    def status(self) -> dict:
        k = self.key()
        return {"provider": "pexels", "key_set": bool(k), "key_hint": ("…" + k[-4:]) if k else None,
                "rate": self.rate or None}

    def set_key(self, key: str) -> dict:
        key = (key or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{20,100}", key):
            raise StockError("That doesn't look like a Pexels API key (letters and digits only).")
        self._get("/v1/search", {"query": "nature", "per_page": 1}, key=key)     # proves it works
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
            raise StockError("No Pexels key yet: add your free key under System > Set up / modules > Stock footage.")
        try:
            with self._client() as c:
                r = c.get(API + path, params=params, headers={"Authorization": key})
        except httpx.HTTPError as e:
            raise StockError(f"Pexels could not be reached ({e.__class__.__name__}).") from None
        self.rate = {k: r.headers.get(f"X-Ratelimit-{k.title()}") for k in ("limit", "remaining", "reset")
                     if r.headers.get(f"X-Ratelimit-{k.title()}")}
        if r.status_code in (401, 403):
            raise StockError("Pexels refused the key (401/403). Check it at pexels.com/api.")
        if r.status_code == 429:
            raise StockError("Pexels' request limit is used up; try again after it resets.")
        if r.status_code != 200:
            raise StockError(f"Pexels answered {r.status_code}.")
        try:
            return r.json()
        except ValueError:
            raise StockError("Pexels sent something that isn't JSON.") from None

    # ---------------------------------------------------------------- search
    def search(self, query: str, kind: str = "video", orientation: str = "landscape",
               min_seconds: float = 0, count: int = 10, page: int = 1) -> dict:
        query = (query or "").strip()
        if not query:
            raise StockError("Say what to search for.")
        if kind not in ("video", "photo"):
            raise StockError("kind is video or photo.")
        if orientation not in ORIENTATIONS:
            raise StockError(f"orientation is one of {', '.join(ORIENTATIONS)}.")
        count = max(1, min(int(count or 10), 40))
        params = {"query": query[:100], "orientation": orientation, "per_page": count, "page": max(1, int(page or 1))}
        out = []
        if kind == "video":
            params["size"] = "medium"                  # Pexels: medium = at least Full HD
            for v in self._get("/videos/search", params).get("videos") or []:
                f = best_video_file(v.get("video_files"))
                if not f or float(v.get("duration") or 0) < float(min_seconds or 0):
                    continue
                out.append({"id": v["id"], "kind": "video", "about": words_from_page(v.get("url")),
                            "seconds": v.get("duration"), "size": f"{f['width']}x{f['height']}",
                            "by": (v.get("user") or {}).get("name"), "page": v.get("url")})
        else:
            for p in self._get("/v1/search", params).get("photos") or []:
                if min(int(p.get("width") or 0), int(p.get("height") or 0)) < MIN_HEIGHT:
                    continue
                out.append({"id": p["id"], "kind": "photo", "about": p.get("alt") or words_from_page(p.get("url")),
                            "size": f"{p.get('width')}x{p.get('height')}", "by": p.get("photographer"),
                            "page": p.get("url")})
        return {"results": out, "query": query, "kind": kind, "page": params["page"],
                "requests_left": self.rate.get("remaining")}

    # ---------------------------------------------------------------- fetch
    def fetch(self, pid: str, kind: str, item_id) -> dict:
        """Download one video / photo by id into files/stock/ and credit it."""
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            raise StockError("id must be the number from stock_search.") from None
        folder = self.store.files_dir(pid) / "stock"
        if kind == "video":
            v = self._get(f"/videos/videos/{item_id}", {})
            f = best_video_file(v.get("video_files"))
            if not f:
                raise StockError("That video has no mp4 between 720p and 1080p.")
            url, name, ext = f["link"], f"pexels-video-{item_id}", ".mp4"
            by, by_url, page = (v.get("user") or {}).get("name"), (v.get("user") or {}).get("url"), v.get("url")
            extra = {"seconds": v.get("duration"), "size": f"{f['width']}x{f['height']}"}
        elif kind == "photo":
            p = self._get(f"/v1/photos/{item_id}", {})
            original = (p.get("src") or {}).get("original")
            if not original:
                raise StockError("That photo has no file.")
            url = original.split("?", 1)[0] + f"?auto=compress&cs=tinysrgb&w={PHOTO_WIDTH}"
            name, ext = f"pexels-photo-{item_id}", ".jpg"
            by, by_url, page = p.get("photographer"), p.get("photographer_url"), p.get("url")
            extra = {"size": f"{p.get('width')}x{p.get('height')} (saved {PHOTO_WIDTH} wide at most)"}
        else:
            raise StockError("kind is video or photo.")
        if not allowed_download(url):
            raise StockError("Pexels pointed the download somewhere unexpected; not downloading it.")
        target = folder / (name + ext)
        rel = f"stock/{name}{ext}"
        if not target.exists():
            folder.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(ext + ".part")
            size = 0
            try:
                with self._client(timeout=120) as c, c.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise StockError(f"The download answered {r.status_code}.")
                    with open(part, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise StockError(f"The file is over {MAX_BYTES >> 20} MB; pick another.")
                            fh.write(chunk)
                if size == 0:
                    raise StockError("The download was empty.")
                part.replace(target)
            except httpx.HTTPError as e:
                raise StockError(f"The download failed ({e.__class__.__name__}).") from None
            finally:
                part.unlink(missing_ok=True)
        self._credit(folder, rel, kind, by, by_url, page)
        return {"file": rel, "kind": kind, "by": by, "page": page,
                "mb": round(target.stat().st_size / 1e6, 1), **extra,
                "credit": f"{'Video' if kind == 'video' else 'Photo'} by {by} on Pexels"}

    def _credit(self, folder: Path, rel: str, kind: str, by, by_url, page) -> None:
        with self._lock:
            data_file = folder / "credits.json"
            try:
                data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.exists() else {}
            except ValueError:
                data = {}
            data[rel] = {"kind": kind, "by": by, "by_url": by_url, "page": page, "source": "Pexels",
                         "added": time.strftime("%Y-%m-%d")}
            data_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
            lines = ["# Stock credits", "", "Free footage from Pexels (https://www.pexels.com). "
                     "Credit line for a description: \"Stock footage: Pexels\" plus the names below.", ""]
            for f, c in sorted(data.items()):
                lines.append(f"- `{f}` - {c['kind'].capitalize()} by {c['by']} on Pexels: {c['page']}")
            (folder / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
