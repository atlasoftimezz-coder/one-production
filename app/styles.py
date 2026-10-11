"""Editing-style profiles learned from competitor videos (owner, 2026-10-11).

A profile is made in a project (its files are where the AI could look at them):

    files/style/<slug>/<video id>/measure.json, transcript.txt, frames/, sheets/   (style_probe)
    files/style/<slug>/numbers.json     medians over the videos, computed by code
    files/style/<slug>/profile.json     the AI's reading of the style + "measured" (= numbers.json)

and listed in data_dir/style/index.json, so an episode in any project can use it. A profile is
reference for techniques and rhythm - never a template, and never a licence to copy branding.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

REQUIRED = ("summary", "pacing", "visual_language", "typography", "color", "motion", "sound", "do", "dont")


class StyleError(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "style"


def check_profile(d) -> dict:
    """The AI's profile: every section present and non-empty, plain JSON, sensible sizes."""
    if not isinstance(d, dict):
        raise StyleError("profile.json must be a JSON object")
    missing = [k for k in REQUIRED if not d.get(k)]
    if missing:
        raise StyleError("profile.json is missing: " + ", ".join(missing))
    for k in ("do", "dont"):
        if not isinstance(d[k], list) or not all(isinstance(x, str) and x.strip() for x in d[k]):
            raise StyleError(f'"{k}" must be a list of short rules')
    if not isinstance(d.get("visual_language"), (dict, list)):
        raise StyleError('"visual_language" must list the kinds of shots with their shares')
    if len(json.dumps(d)) > 40_000:
        raise StyleError("profile.json is too long - keep it to what an editor needs (under 40 kB)")
    return d


def brief(profile: dict, limit: int = 2800) -> str:
    """A short plain-text version for the learning store and agent prompts."""
    m = profile.get("measured") or {}
    p, s = m.get("pace") or {}, m.get("sound") or {}
    nums = (f"measured: {p.get('cuts_per_min')} cuts/min, median shot {p.get('shot_median')} s "
            f"(p10 {p.get('shot_p10')}, p90 {p.get('shot_p90')}), first 30 s {p.get('first_30s_cuts_per_min')} cuts/min, "
            f"SFX on {round(100 * (s.get('cuts_with_a_hit') or 0))}% of cuts, music bed in "
            f"{round(100 * (s.get('music_bed_hint_share') or 0))}% of videos, {(m.get('words') or {}).get('wpm')} wpm.")
    text = f"{profile.get('summary', '')}\n{nums}\nDo: " + "; ".join(profile.get("do") or []) + \
           "\nDon't: " + "; ".join(profile.get("dont") or [])
    return text[:limit]


class Styles:
    def __init__(self, data_dir: Path, store):
        self.dir = data_dir / "style"
        self.index = self.dir / "index.json"
        self.store = store
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.index.read_text(encoding="utf-8")) if self.index.exists() else []
        except ValueError:
            return []

    def _save(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.index)

    def list(self) -> list[dict]:
        items = []
        for e in self._load():
            folder = self._folder(e)
            items.append({**e, "available": bool(folder and (folder / "profile.json").is_file())})
        return sorted(items, key=lambda e: -e["created"])

    def get(self, style_id: str) -> dict:
        for e in self.list():
            if e["id"] == style_id:
                return e
        raise StyleError("unknown style profile")

    def _folder(self, e: dict) -> Path | None:
        try:
            return self.store.files_dir(e["project"]) / e["path"]
        except Exception:                            # noqa: BLE001 - the project was deleted
            return None

    def folder(self, style_id: str) -> Path:
        f = self._folder(self.get(style_id))
        if not f or not f.is_dir():
            raise StyleError("this profile's files are gone (was its project deleted?)")
        return f

    def profile(self, style_id: str) -> dict:
        return json.loads((self.folder(style_id) / "profile.json").read_text(encoding="utf-8"))

    def numbers(self, style_id: str) -> dict:
        f = self.folder(style_id) / "numbers.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    def add(self, name: str, project: str, path: str, videos: list[dict]) -> dict:
        entry = {"id": secrets.token_hex(5), "name": name.strip()[:60] or "Style", "project": project, "path": path,
                 "videos": [{k: v.get(k) for k in ("id", "title", "channel")} for v in videos], "created": time.time(),
                 "lesson": None}
        with self._lock:
            items = [e for e in self._load() if not (e["project"] == project and e["path"] == path)]
            items.append(entry)
            self._save(items)
        return entry

    def update(self, style_id: str, **changes) -> dict:
        with self._lock:
            items = self._load()
            for e in items:
                if e["id"] == style_id:
                    e.update(changes)
                    self._save(items)
                    return e
        raise StyleError("unknown style profile")

    def remove(self, style_id: str, delete_files: bool = True) -> None:
        e = self.get(style_id)
        with self._lock:
            self._save([x for x in self._load() if x["id"] != style_id])
        folder = self._folder(e)
        if delete_files and folder and folder.is_dir():
            root = self.store.files_dir(e["project"]).resolve()
            if folder.resolve().is_relative_to(root / "style"):       # never outside the project's style folder
                shutil.rmtree(folder, ignore_errors=True)
