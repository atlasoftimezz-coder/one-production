"""Generated hero assets for episodes (owner, 2026-10-12: "use image generation for asset if hard to create
in davinci" - after rating the first draft 5/10 next to the competitor).

The image module (installer.ImageRecipe, server imagegen/server.py) makes one isolated object per call -
FLUX.1-schnell on the owner's GPU, cut out to a transparent PNG. This service starts that server when
needed, gives every asset of an episode the same look (a style prefix from the episode's art direction),
keeps a project index (files/assets/index.json: name -> file, w, h, prompt, seed) that the Fusion kit
reads (S.image), and reuses a result when the same prompt comes again. Real people, logos and text are
kept out by the prompt (owner's rule: designed cards for people and headlines).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("oneprod.assets")
START_TIMEOUT = 120
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
BANNED = re.compile(r"\b(logo|trademark|brand(ed)?|photo of (?:president|senator|minister|ceo)|portrait of|face of|"
                    r"[A-Z][a-z]+ [A-Z][a-z]+'s face)\b", re.I)
BASE_STYLE = ("a single isolated object, centred, on a plain neutral background, studio product render, soft key light "
              "and rim light, crisp edges, high detail, no text, no letters, no numbers, no logo, no watermark, no people")
# A scene plate sets each shot in a place (owner, 2026-10-12: v2 shots were objects floating on an empty dark void).
SCENE_STYLE = ("a wide cinematic establishing shot, no people, moody documentary lighting, deep shadows, shallow depth "
               "of field, rich texture, no text, no letters, no numbers, no logo, no watermark")
SCENE_SIZE = (1344, 768)          # 16:9-ish, multiples of 16 for FLUX


class AssetError(RuntimeError):
    pass


# FLUX.1-schnell is Apache-2.0 but its Hugging Face repo is gated behind a free "accept the terms" click
# (first real install, 2026-10-12: 401 GatedRepoError). The owner's own read token, in Credential Manager.
HF_KEY = "huggingface"
HF_GATED = "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/model_index.json"


def hf_token() -> str | None:
    import keyring
    from .accounts import KEYRING_SERVICE
    try:
        return keyring.get_password(KEYRING_SERVICE, HF_KEY)
    except keyring.errors.KeyringError:
        return None


def set_hf_token(token: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Check the token and that the owner accepted FLUX.1-schnell's terms, then keep it in Credential Manager."""
    import keyring
    from .accounts import KEYRING_SERVICE
    token = (token or "").strip()
    if not re.fullmatch(r"hf_[A-Za-z0-9]{20,80}", token):
        raise AssetError("That doesn't look like a Hugging Face token (it starts with hf_). Make a 'Read' token at "
                         "huggingface.co/settings/tokens.")
    try:
        with httpx.Client(transport=transport, timeout=20, follow_redirects=True) as c:
            who = c.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {token}"})
            if who.status_code != 200:
                raise AssetError("Hugging Face refused the token - check it at huggingface.co/settings/tokens.")
            gate = c.head(HF_GATED, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise AssetError(f"Hugging Face could not be reached ({e.__class__.__name__}).") from None
    if gate.status_code in (401, 403):
        raise AssetError("The token works, but this account hasn't accepted FLUX.1-schnell's terms yet: open "
                         "huggingface.co/black-forest-labs/FLUX.1-schnell, press 'Agree and access repository', then save again.")
    keyring.set_password(KEYRING_SERVICE, HF_KEY, token)
    return {"hf_token": True, "hf_user": (who.json() or {}).get("name")}


def style_prefix(art: dict | None) -> str:
    """One look for every asset of an episode, from its art.json (palette + concept)."""
    art = art or {}
    pal = art.get("palette") or {}
    colours = ", ".join(f"{k} {v}" for k, v in list(pal.items())[:5])
    concept = str(art.get("asset_look") or art.get("concept") or "")[:220]
    parts = ["matte stylised 3D render, slightly isometric", f"colour palette: {colours}" if colours else "",
             f"look: {concept}" if concept else ""]
    return ", ".join(p for p in parts if p)


class ImageService:
    """The local image server (like VoiceService for the voice app): start on demand, generate, unload."""

    def __init__(self, url: str = "http://127.0.0.1:8722", transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.transport = transport
        self.python: Path | None = None
        self.server: Path | None = None
        self.env: dict[str, str] = {}
        self.off_reason: str | None = "the image module isn't installed yet - open System, Modules"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def configure(self, python: Path | None, server: Path | None, env: dict | None, off_reason: str | None) -> None:
        self.python, self.server, self.env, self.off_reason = python, server, dict(env or {}), off_reason

    def ready(self) -> bool:
        return not self.off_reason

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout)

    def running(self) -> bool:
        try:
            with self._client(5) as c:
                return c.get(self.url + "/api/meta").status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_running(self) -> None:
        with self._lock:
            if self.running():
                return
            if self.off_reason:
                raise AssetError(self.off_reason)
            port = self.url.rsplit(":", 1)[-1]
            log.info("starting the image server on %s", self.url)
            self._proc = subprocess.Popen([str(self.python), str(self.server), "--port", port], cwd=str(self.server.parent),
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          env={**os.environ, **self.env}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.time() + START_TIMEOUT
            while time.time() < deadline:
                if self.running():
                    return
                if self._proc.poll() is not None:
                    raise AssetError(f"the image server stopped while starting (exit code {self._proc.returncode})")
                time.sleep(1)
            raise AssetError("the image server did not start within 2 minutes")

    def generate(self, prompt: str, out: Path, seed: int, size: tuple[int, int] | None = None,
                 cutout: bool = True) -> dict:
        self.ensure_running()
        body = {"prompt": prompt, "out": str(out), "seed": seed, "cutout": cutout}
        if size:
            body.update(width=size[0], height=size[1])
        try:
            with self._client(600) as c:                 # the first call loads FLUX (a minute or two)
                r = c.post(self.url + "/api/generate", json=body)
        except httpx.HTTPError as e:
            raise AssetError(f"the image server did not answer ({e.__class__.__name__})") from None
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise AssetError(f"image generation failed: {detail or r.status_code}")
        return r.json()

    def unload(self) -> None:
        """Free the image model for Resolve. The server this app started is stopped outright: after /api/unload it
        still held ~9 GB of memory and Resolve ran out ("Memory allocation failed", no stills - v2 re-draft,
        2026-10-12). One someone else started only gets the unload call."""
        try:
            with self._client(30) as c:
                c.post(self.url + "/api/unload")
        except httpx.HTTPError:
            pass
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        log.info("stopping the image server to free memory for Resolve")
        if os.name == "nt":        # the venv's python.exe is a launcher: take its child with it
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        proc.wait()


class Assets:
    """A project's generated assets: files/assets/<name>.png + index.json."""

    def __init__(self, store, service: ImageService):
        self.store, self.svc = store, service
        self._lock = threading.Lock()

    def index_file(self, pid: str) -> Path:
        return self.store.files_dir(pid) / "assets" / "index.json"

    def index(self, pid: str) -> dict:
        f = self.index_file(pid)
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except ValueError:
            return {}

    def generate(self, pid: str, name: str, prompt: str, art: dict | None = None, seed: int | None = None,
                 variant: int = 0, kind: str = "object") -> dict:
        """variant: 0 = the default take; 1, 2 = other takes of the same prompt (to replace a bad result).
        kind: "object" (cut out, transparent) or "scene" (a full-frame background plate, not cut out)."""
        if kind not in ("object", "scene"):
            raise AssetError('kind is "object" or "scene"')
        name = (name or "").strip().lower()
        if not NAME.match(name):
            raise AssetError("name: 2-41 lower-case letters, digits and dashes, e.g. vault-door")
        prompt = re.sub(r"\s+", " ", (prompt or "").strip())[:400]
        if not prompt:
            raise AssetError("say what the object is")
        if BANNED.search(prompt):
            raise AssetError("assets are objects only - no logos, brands or real people's faces (use S.person_tag / "
                             "S.quote_card for people and headlines)")
        full = f"{prompt}, {style_prefix(art)}, {SCENE_STYLE if kind == 'scene' else BASE_STYLE}"
        seed = int(seed if seed is not None else int(hashlib.sha256(full.encode()).hexdigest()[:8], 16) % 2**31)
        seed = (seed + 7919 * max(0, min(int(variant or 0), 5))) % 2**31
        key = hashlib.sha256(f"{full}|{seed}".encode()).hexdigest()[:16]
        idx = self.index(pid)
        old = idx.get(name)
        target = self.store.files_dir(pid) / "assets" / f"{name}.png"
        if old and old.get("key") == key and target.exists():
            return {**old, "name": name, "reused": True}
        res = (self.svc.generate(full, target, seed, SCENE_SIZE, cutout=False) if kind == "scene"
               else self.svc.generate(full, target, seed))
        entry = {"file": f"assets/{name}.png", "w": int(res["w"]), "h": int(res["h"]), "prompt": prompt, "seed": seed,
                 "kind": kind,
                 "key": key, "seconds": res.get("seconds"), "made": time.time()}
        with self._lock:
            idx = self.index(pid)
            idx[name] = entry
            self.index_file(pid).parent.mkdir(parents=True, exist_ok=True)
            self.index_file(pid).write_text(json.dumps(idx, indent=1), encoding="utf-8")
        return {**entry, "name": name, "reused": False}

    async def agenerate(self, *a, **kw) -> dict:
        return await asyncio.to_thread(self.generate, *a, **kw)
