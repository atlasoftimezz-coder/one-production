"""The terms check (plan R8, D7) as the app enforces it: app/terms.json, read from docs/terms-check.md.

Moving work to another account of the SAME subscription service when one hits its limit
("chaining") is allowed only if BOTH the owner switched it on (setting `allow_chaining`) AND the
recorded terms say "allowed" for that provider. Any "not_allowed" or "unclear" keeps it off.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).with_name("terms.json")


@lru_cache(maxsize=1)
def load(path: str = str(FILE)) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checked": None, "providers": {}}       # no record = nothing is allowed


def verdict(provider: str, question: str = "chaining", data: dict | None = None) -> str:
    return ((data or load()).get("providers", {}).get(provider) or {}).get(question) or "unclear"


def chaining_allowed(provider: str, owner_allows: bool, data: dict | None = None) -> bool:
    return bool(owner_allows) and verdict(provider, "chaining", data) == "allowed"


def summary(owner_allows: bool, data: dict | None = None) -> dict:
    d = data or load()
    return {"checked": d.get("checked"), "owner_allows_chaining": bool(owner_allows),
            "providers": {p: dict(v, chaining_effective=chaining_allowed(p, owner_allows, d))
                          for p, v in (d.get("providers") or {}).items()}}
