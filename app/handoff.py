"""handoff.md - everything the next account needs to carry on (plan R3).

Written when an account hits its limit, when it crosses its switch threshold, every
BACKUP_EVERY turns, and on shutdown. Each one is kept in projects/<id>/handoffs/ and the
newest is also copied to projects/<id>/handoff.md. Everything passes through redact(), so
a key pasted into the chat never ends up in a handoff.

The handoff is assembled from the saved conversation, not written by a model: it costs
nothing, works when every account is out of quota, and cannot invent decisions.
"""
from __future__ import annotations

import time
from pathlib import Path

from .security.redact import redact

LAST_MESSAGES = 20
BACKUP_EVERY = 10          # assistant replies between automatic backups

REASONS = {
    "limit": "the account hit its usage limit",
    "threshold": "the account crossed its switch threshold",
    "backup": "automatic backup",
    "shutdown": "the app was closed",
    "manual": "you asked to switch",
}


def _fmt_windows(rl: dict | None) -> str:
    if not rl or not rl.get("windows"):
        return "not reported by this provider"
    names = {"five_hour": "5-hour", "seven_day": "7-day"}
    return ", ".join(f"{names.get(k, k)} {round((w.get('utilization') or 0) * 100)}%"
                     for k, w in rl["windows"].items())


def write(project_dir: Path, project: dict, history: list[dict], files: list[dict], *,
          reason: str, account: dict | None) -> Path:
    when = time.localtime()
    history = [m for m in history if m["role"] in ("user", "assistant")]   # drop app notes
    first_user = next((m["text"] for m in history if m["role"] == "user"), "")
    last_user = next((m["text"] for m in reversed(history) if m["role"] == "user"), "")
    last = history[-1] if history else None
    interrupted = last if last and last["role"] == "assistant" and last.get("interrupted") else None

    acc_line = "—"
    if account:
        acc_line = f"{account.get('label') or account.get('email') or account['id']} ({account['provider']})"

    lines = [
        f"# Handoff — {project['name']}",
        "",
        f"- **Written:** {time.strftime('%Y-%m-%d %H:%M:%S', when)}",
        f"- **Why:** {REASONS.get(reason, reason)}",
        f"- **From account:** {acc_line}",
        f"- **Usage at this point:** {_fmt_windows(account.get('rate_limit') if account else None)}",
        "",
        "## Goal",
        "",
        first_user or "(no messages yet)",
        "",
        "## Where we are",
        "",
        f"Last request: {last_user}" if last_user else "No request yet.",
        "",
        "Decisions are not summarised automatically — they are in the conversation below, word for word.",
        "",
        "## Files in this project",
        "",
        *([f"- {f['name']} ({f['size']} bytes)" for f in files] or ["- none"]),
        "",
    ]
    if interrupted:
        lines += [
            "## Reply that was cut off",
            "",
            "The previous account stopped part-way through this reply. Continue from where it stops; "
            "do not start it again.",
            "",
            "> " + interrupted["text"].replace("\n", "\n> "),
            "",
        ]
    lines += ["## Actions not yet confirmed", "",
              "None — this version of the app does not run tools on your behalf.", "",
              f"## Last {min(LAST_MESSAGES, len(history))} messages (verbatim)", ""]
    for m in history[-LAST_MESSAGES:]:
        who = "You" if m["role"] == "user" else "Assistant"
        tag = " *(cut off)*" if m.get("interrupted") else ""
        lines += [f"**{who}**{tag}:", "", m["text"], ""]

    text = redact("\n".join(lines))
    folder = project_dir / "handoffs"
    folder.mkdir(exist_ok=True)
    path = folder / f"handoff-{time.strftime('%Y%m%d-%H%M%S', when)}-{reason}.md"
    n = 1
    while path.exists():
        path = folder / f"handoff-{time.strftime('%Y%m%d-%H%M%S', when)}-{reason}-{n}.md"
        n += 1
    path.write_text(text, encoding="utf-8")
    (project_dir / "handoff.md").write_text(text, encoding="utf-8")
    return path


def continue_prompt(handoff_text: str, interrupted: bool) -> str:
    """The first message the next account receives."""
    ask = ("Your previous reply was cut off - continue it exactly from where it stops, without "
           "repeating what is already there." if interrupted else
           "Carry on with the last request in this conversation.")
    return ("You are taking over this conversation from another AI account that had to stop. "
            "Everything you need is in the handoff below.\n\n" + handoff_text + "\n\n" + ask)
