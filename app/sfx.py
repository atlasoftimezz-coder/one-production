"""Sound effects from Epidemic Sound with the owner's own key (owner, 2026-10-11: "Use epidamic api key").

The owner's key comes from epidemicsound.com/account/api-keys ("MCP Server", valid one year). It is
NOT a developer partner-API key - that API answers 401 to it - so the app talks to Epidemic's MCP
server, exactly as an MCP client would. Measured live on 2026-10-11:

    POST https://www.epidemicsound.com/a/mcp-service/mcp     (streamable HTTP, JSON-RPC 2.0)
         Authorization: Bearer epidemic_live_...   Accept: application/json, text/event-stream
    initialize -> Mcp-Session-Id header; answers come as server-sent events ("data: {...}")
    tools/call SearchSoundEffects {"query": {"term"}, "filter": {"duration": {"max": ms}}, "first": n}
        -> text JSON {"data": {"soundEffects": {"nodes": [{"soundEffect": {"id", "title",
           "audioFile": {"durationInMilliseconds"}, "tags": [...]}}], "meta": {"total"}}}}
    tools/call DownloadSoundEffect {"id", "options": {"fileType": "MP3"}}
        -> text JSON {"data": {"soundEffectDownload": {"assetUrl": "https://audiocdn.epidemicsound.com/..."}}}

Licence: the MCP server is a Beta under Epidemic's MCP Server licence; content falls under the
owner's own Epidemic plan. The app counts downloads and reuses every file it already has
(data_dir/cache/sfx), so one effect costs one download however often it is used.

The key lives in Windows Credential Manager (OneProduction / epidemic) and never reaches the AI.
Files land in the project's files/sfx/ with credits in sfx/CREDITS.md.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import keyring

from .accounts import KEYRING_SERVICE

KEY_NAME = "epidemic"
MCP_URL = "https://www.epidemicsound.com/a/mcp-service/mcp"
DOWNLOAD_HOSTS = ("audiocdn.epidemicsound.com",)
MAX_BYTES = 30 * 1024 * 1024
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class SfxError(Exception):
    pass


def _events(r: httpx.Response) -> dict:
    """A JSON-RPC answer, sent as plain JSON or as server-sent events (the last data line wins)."""
    if "text/event-stream" in r.headers.get("content-type", ""):
        found = None
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    found = json.loads(line[5:].strip())
                except ValueError:
                    continue
        if found is None:
            raise SfxError("Epidemic Sound sent an empty answer.")
        return found
    try:
        return r.json()
    except ValueError:
        raise SfxError("Epidemic Sound sent something that isn't JSON.") from None


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
            return json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        except ValueError:
            return {}

    def _save(self, d: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(d, indent=1), encoding="utf-8")

    def status(self) -> dict:
        k = self.key()
        return {"provider": "epidemic", "key_set": bool(k), "key_hint": ("…" + k[-4:]) if k else None,
                "downloads": self._state().get("downloads", 0),
                "cached": len(list(self.cache.glob("*.mp3"))) if self.cache.exists() else 0,
                "licence_note": "Sound effects come under your own Epidemic Sound plan (via Epidemic's MCP server, "
                                "a Beta). Make sure your plan covers your YouTube channel."}

    def set_key(self, key: str) -> dict:
        key = (key or "").strip()
        if not re.fullmatch(r"epidemic_[A-Za-z0-9_\-]{10,200}", key):
            raise SfxError("That doesn't look like an Epidemic Sound key (it starts with epidemic_live_; "
                           "make one at epidemicsound.com/account/api-keys).")
        self._tool("SearchSoundEffects", {"query": {"term": "whoosh"}, "first": 1}, key=key)     # proves it works
        keyring.set_password(KEYRING_SERVICE, KEY_NAME, key)
        return self.status()

    def remove_key(self) -> dict:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
        return self.status()

    # ---------------------------------------------------------------- MCP over HTTP
    def _client(self, timeout: float = 60) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=True)

    def _post(self, c: httpx.Client, headers: dict, body: dict) -> httpx.Response:
        try:
            r = c.post(MCP_URL, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise SfxError(f"Epidemic Sound could not be reached ({e.__class__.__name__}).") from None
        if r.status_code in (401, 403):
            raise SfxError("Epidemic Sound refused the key (401/403). Make a new one at epidemicsound.com/account/api-keys.")
        if r.status_code == 429:
            raise SfxError("Epidemic Sound's request or download limit is used up for now.")
        if r.status_code >= 400:
            raise SfxError(f"Epidemic Sound answered {r.status_code}.")
        return r

    def _tool(self, name: str, arguments: dict, key: str | None = None) -> dict:
        """One MCP tool call (initialize -> initialized -> tools/call); the tool's JSON 'data'."""
        key = key or self.key()
        if not key:
            raise SfxError("No Epidemic Sound key yet: add it under System > Set up / modules > Sound effects.")
        h = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
             "Content-Type": "application/json"}
        with self._client() as c:
            r = self._post(c, h, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "one-production", "version": "1"}}})
            init = _events(r)
            if init.get("error"):
                raise SfxError("Epidemic Sound: " + str(init["error"].get("message") or init["error"])[:200])
            if r.headers.get("mcp-session-id"):
                h["Mcp-Session-Id"] = r.headers["mcp-session-id"]
            self._post(c, h, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            out = _events(self._post(c, h, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                            "params": {"name": name, "arguments": arguments}}))
        if out.get("error"):
            raise SfxError("Epidemic Sound: " + str(out["error"].get("message") or out["error"])[:200])
        res = out.get("result") or {}
        text = "".join(p.get("text", "") for p in res.get("content") or [] if p.get("type") == "text")
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            body = {}
        if res.get("isError") or body.get("errors"):
            msg = "; ".join(str(e.get("message")) for e in body.get("errors") or []) or text[:200] or "the call failed"
            raise SfxError(f"Epidemic Sound ({name}): {msg}")
        return body.get("data") or {}

    # ---------------------------------------------------------------- search / fetch
    def search(self, term: str, count: int = 10, max_seconds: float = 0) -> dict:
        term = (term or "").strip()
        if not term:
            raise SfxError("Say what to search for (e.g. whoosh, soft click, riser, impact).")
        args: dict = {"query": {"term": term[:100]}, "first": max(1, min(int(count or 10), 50))}
        if max_seconds:
            args["filter"] = {"duration": {"max": int(float(max_seconds) * 1000)}}
        data = self._tool("SearchSoundEffects", args)
        out = []
        for node in (data.get("soundEffects") or {}).get("nodes") or []:
            s = node.get("soundEffect") or {}
            if not s.get("id"):
                continue
            ms = (s.get("audioFile") or {}).get("durationInMilliseconds") or 0
            out.append({"id": s["id"], "title": s.get("title"), "seconds": round(ms / 1000, 2),
                        "tags": [t.get("displayName") for t in s.get("tags") or []][:5],
                        "cached": (self.cache / f"epidemic-{s['id']}.mp3").exists()})
        return {"results": out, "term": term, "total": ((data.get("soundEffects") or {}).get("meta") or {}).get("total")}

    def fetch(self, pid: str, sfx_id: str, title: str = "") -> dict:
        sfx_id = str(sfx_id or "").strip()
        if not UUID.match(sfx_id):
            raise SfxError("id must be the id from sfx_search.")
        name = f"epidemic-{sfx_id}.mp3"
        cached = self.cache / name
        downloaded = not cached.exists()
        if downloaded:
            data = self._tool("DownloadSoundEffect", {"id": sfx_id, "options": {"fileType": "MP3"}})
            link = (data.get("soundEffectDownload") or {}).get("assetUrl")
            u = urlparse(link or "")
            if u.scheme != "https" or (u.hostname or "").lower() not in DOWNLOAD_HOSTS:
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
