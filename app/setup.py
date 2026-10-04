"""The first-run wizard's memory (plan R13).

    setup.json   {"done": bool, "step": n, "chose_folder": bool}

The wizard opens by itself only on a genuinely new install: no setup.json, no projects and
no accounts. Anyone already using the app (like the studio PC) is never interrupted; they
can open it any time from System, "Run setup again".
"""
from __future__ import annotations

import json
import os
import shutil
import string
import sys
from pathlib import Path

STEPS = ["welcome", "scan", "modules", "install", "accounts", "voice", "test"]


class SetupState:
    def __init__(self, data_dir: Path, store, accounts):
        self.data_dir = data_dir
        self.file = data_dir / "setup.json"
        self.store = store
        self.accounts = accounts

    def _load(self) -> dict:
        if self.file.exists():
            try:
                return json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {}

    def save(self, **changes) -> dict:
        d = self._load()
        d.update({k: v for k, v in changes.items() if v is not None})
        self.file.write_text(json.dumps(d), encoding="utf-8")
        return self.public()

    def first_run(self) -> bool:
        return not self.file.exists() and not self.store.list_projects() and not self.accounts.list()

    def public(self) -> dict:
        d = self._load()
        free = shutil.disk_usage(self.data_dir).free if self.data_dir.exists() else 0
        return {"done": bool(d.get("done")), "step": int(d.get("step") or 0), "steps": STEPS,
                "chose_folder": bool(d.get("chose_folder")), "first_run": self.first_run(),
                "data_dir": str(self.data_dir), "data_free_gb": round(free / 1e9, 1)}


def drives() -> list[dict]:
    """Drives with free space, so a full C: isn't the only choice offered."""
    out = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    u = shutil.disk_usage(root)
                except OSError:
                    continue
                out.append({"drive": f"{letter}:", "free_gb": round(u.free / 1e9, 1),
                            "total_gb": round(u.total / 1e9, 1), "suggest": f"{letter}:\\OneProduction"})
    return out
