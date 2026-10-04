"""Export diagnostics (plan R16): one zip a friend can send when something breaks.

In it: the PC scan, versions, install and module state, the settings files, the last part of
the log and recent error messages (from chats, voiceovers and installs).
Never in it: chat text, project files, voices, keys or passwords. Every text goes through the
one redaction function, and email addresses are shortened to their first letter and domain.
"""
from __future__ import annotations

import io
import json
import platform
import re
import sys
import time
import zipfile
from pathlib import Path

from .version import __version__

EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
LOG_LINES = 2000

README = """One Production - diagnostics
============================

Made {when} by One Production {version}.

Inside: the PC scan, versions, setup/module/backup/update state, the settings files, the last
{lines} lines of the log, and recent error messages.

Not inside: chat text, project files, voices, API keys, passwords or session tokens. Keys and
tokens are replaced with [REDACTED:...] wherever they appear; email addresses are shortened.
"""


def mask_emails(text: str) -> str:
    return EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


def _tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recent_errors(data_dir: Path, store) -> dict:
    chats = []
    for p in store.list_projects():
        for m in store.history(p["id"]):
            if m.get("role") == "assistant" and m.get("error"):
                chats.append({"time": m.get("time"), "project": p["id"], "provider": m.get("provider"),
                              "kind": m["error"].get("kind"), "message": m["error"].get("message"),
                              "agent": bool(m.get("agent"))})
    voice = []
    for f in sorted((data_dir / "cache" / "voicejobs").glob("*.json")):
        j = _json_file(f) or {}
        if j.get("status") == "failed":
            voice.append({"time": j.get("created"), "error": j.get("error"), "step": j.get("step"),
                          "voice": j.get("voice")})
    chats.sort(key=lambda e: e.get("time") or 0)
    return {"chat_errors": chats[-50:], "voiceover_errors": voice[-20:]}


def build(data_dir: Path, settings_file: Path, store, redact, sections: dict) -> bytes:
    """sections: already-computed state (scan, modules, setup, backups, update, install, accounts)."""
    def clean(obj) -> str:
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=1, ensure_ascii=False, default=str)
        return mask_emails(redact(text))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README.format(when=time.strftime("%Y-%m-%d %H:%M"), version=__version__,
                                               lines=LOG_LINES))
        z.writestr("versions.json", clean({"one_production": __version__, "python": sys.version,
                                           "platform": platform.platform(), "machine": platform.machine()}))
        for name, value in sections.items():
            z.writestr(f"{name}.json", clean(value))
        z.writestr("recent-errors.json", clean(recent_errors(data_dir, store)))
        for f in ("modules.json", "setup.json", "backup.json", "version.json", "approvals.json"):
            v = _json_file(data_dir / f)
            if v is not None:
                z.writestr(f"data/{f}", clean(v))
        s = _json_file(settings_file)
        if s is not None:
            z.writestr("data/settings.json", clean(s))
        z.writestr("app.log", clean(_tail(data_dir / "logs" / "app.log", LOG_LINES)))
    return buf.getvalue()
