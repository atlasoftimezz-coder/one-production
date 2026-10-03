"""Turning a chat message into a provider call.

The reply streams to the browser as NDJSON - one JSON object per line:
    {"t":"text","v":"..."}                       a piece of the reply
    {"t":"usage", ...}                           tokens used (and cost if the client reports it)
    {"t":"limit", "windows":{...}}               provider-reported utilisation (Claude)
    {"t":"error","kind":"limit|transient|auth|fatal","message":"...","interrupted":bool}
    {"t":"switch_offer","reason":"limit|threshold|manual","handoff":"...","eligible":[...]}
    {"t":"done"}
"""
from __future__ import annotations

import json
from dataclasses import asdict

from .providers.base import Fail, RateLimit, Text, Usage, run_with_retries

SYSTEM = ("You are the assistant inside One Production, a local studio a YouTube creator uses to "
          "plan, script and produce videos. Be direct and practical. Use plain text, not markdown "
          "tables.")
HISTORY_TURNS = 20


def build_prompt(history: list[dict], message: str) -> str:
    """CLIs take one prompt, so the recent conversation is written out as a transcript."""
    lines = [SYSTEM, ""]
    recent = [m for m in history if m["role"] in ("user", "assistant")][-HISTORY_TURNS:]
    if recent:
        lines.append("Conversation so far:")
        for m in recent:
            who = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{who}: {m['text']}")
        lines.append("")
    lines += [f"User: {message}", "Assistant:"]
    return "\n".join(lines)


def ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def reply_events(provider, account: dict, prompt: str, model: str | None, on_ratelimit, on_auth):
    """Yield (ndjson_bytes, event) pairs; side effects (rate-limit snapshot, auth marking) via callbacks."""
    async for ev in run_with_retries(provider, account, prompt, model):
        if isinstance(ev, Text):
            yield ndjson({"t": "text", "v": ev.text}), ev
        elif isinstance(ev, Usage):
            yield ndjson({"t": "usage", **asdict(ev)}), ev
        elif isinstance(ev, RateLimit):
            on_ratelimit(ev)
            yield ndjson({"t": "limit", "windows": ev.windows, "status": ev.status}), ev
        elif isinstance(ev, Fail):
            if ev.kind == "auth":
                on_auth(ev)
            yield ndjson({"t": "error", "kind": ev.kind, "message": ev.message,
                          "interrupted": ev.interrupted}), ev
    # "done" is sent by the caller, after any switch offer
