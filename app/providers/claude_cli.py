"""Claude via the Claude Code CLI, using the account's own login folder.

Formats verified against Claude Code 2.1.284 (2026-10-03):
  stream_event / content_block_delta / text_delta   -> reply text (thinking deltas skipped)
  rate_limit_event.rate_limit_info.unifiedWindows   -> exact five_hour / seven_day utilisation
  result (is_error, api_error_status, usage, total_cost_usd)
  `claude -p /usage` answers locally (num_turns 0, cost 0) with lines like
      Current session: 34% used · resets Oct 3, 11:50pm (Asia/Kolkata)
      Current week (all models): 18% used · resets Oct 5, 7:30pm (Asia/Kolkata)
"""
from __future__ import annotations

import json
import re
import time
from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from ..accounts import claude_env
from ..models import EFFORTS
from .base import Event, Fail, RateLimit, Text, Usage, classify_status, classify_text
from .cli import resolve, run_capture, run_lines

MODELS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "fable": "fable"}
USAGE_ARGS = ["-p", "/usage", "--output-format", "json", "--tools", "", "--no-session-persistence",
              "--strict-mcp-config"]
_SESSION = re.compile(r"Current session:\s*([\d.]+)%\s*used(?:\s*·\s*resets\s+([^\n(]+))?", re.I)
_WEEK = re.compile(r"Current week \(all models\):\s*([\d.]+)%\s*used(?:\s*·\s*resets\s+([^\n(]+))?", re.I)


def parse_reset(text: str | None, now: float | None = None) -> float | None:
    """'Oct 3, 11:50pm' (local time, no year) -> epoch seconds."""
    if not text:
        return None
    now = now or time.time()
    year = datetime.fromtimestamp(now).year
    for fmt in ("%b %d, %I:%M%p", "%b %d, %I%p", "%b %d %I:%M%p"):
        try:
            dt = datetime.strptime(f"{year} {text.strip()}", f"%Y {fmt}")
        except ValueError:
            continue
        ts = dt.timestamp()
        if ts < now - 86400:            # 'Jan 2' read on Dec 31 belongs to next year
            ts = dt.replace(year=year + 1).timestamp()
        return ts
    return None


def parse_usage_text(text: str, now: float | None = None) -> dict:
    windows = {}
    for name, rx in (("five_hour", _SESSION), ("seven_day", _WEEK)):
        m = rx.search(text or "")
        if m:
            windows[name] = {"utilization": float(m.group(1)) / 100, "resets_at": parse_reset(m.group(2), now)}
    return windows
ARGS = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"]


class ClaudeCLI:
    name = "claude"

    def __init__(self, command: str | None, work_dir: Path, timeout: float = 300):
        self.command = command
        self.work_dir = work_dir
        self.timeout = timeout

    def _exe(self) -> str | None:
        return resolve("claude", self.command)

    @staticmethod
    def env(account: dict) -> dict[str, str]:
        # The data folder usually sits under the user's home, and Claude Code reads CLAUDE.md
        # (and .claude/CLAUDE.md) from every parent folder - so the user's own global
        # instructions would steer the studio assistant. Memory files are switched off.
        return {**claude_env(account), "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

    async def status(self, account: dict) -> dict:
        exe = self._exe()
        if not exe:
            return {"ok": False, "reason": "Claude Code is not installed"}
        code, out, err = await run_capture([exe, "auth", "status", "--json"], self.env(account),
                                           self.work_dir, timeout=30)
        try:
            d = json.loads(out)
        except ValueError:
            return {"ok": False, "reason": (err or out or "no answer").strip()[:200]}
        if not d.get("loggedIn"):
            return {"ok": False, "reason": "not logged in"}
        method = d.get("authMethod")
        if method == "oauth_token":     # CLAUDE_CODE_OAUTH_TOKEN: Claude Code can't tell whose it is
            method = "sign-in token"
        return {"ok": True, "email": d.get("email"), "plan": d.get("subscriptionType"),
                "org": d.get("orgName"), "method": method}

    async def live_usage(self, account: dict) -> dict | None:
        """Current utilisation without sending a message. Safety stop: if this ever runs a
        model turn (num_turns > 0 or a cost), report that instead of trusting it, so the
        app can stop calling it rather than quietly spending the user's quota."""
        exe = self._exe()
        if not exe:
            return None
        code, out, err = await run_capture([exe, *USAGE_ARGS], self.env(account), self.work_dir, timeout=60)
        try:
            d = json.loads(out)
        except ValueError:
            return None
        if (d.get("num_turns") or 0) > 0 or (d.get("total_cost_usd") or 0) > 0:
            return {"error": "the /usage command used a model turn - stopped calling it"}
        windows = parse_usage_text(str(d.get("result") or ""))
        return {"windows": windows, "as_of": time.time()} if windows else None

    def login_argv(self) -> list[str] | None:
        exe = self._exe()
        return [exe, "auth", "login"] if exe else None

    async def stream(self, account: dict, prompt: str, model: str | None,
                     effort: str | None = None) -> AsyncIterator[Event]:
        exe = self._exe()
        if not exe:
            yield Fail("fatal", "Claude Code is not installed on this PC")
            return
        argv = [exe, *ARGS]
        if model in MODELS:
            argv += ["--model", MODELS[model]]
        if effort in EFFORTS:
            argv += ["--effort", effort]
        got_result = False
        async with aclosing(run_lines(argv, prompt, self.env(account), self.work_dir,
                                      self.timeout)) as lines:
            async for kind, value in lines:
                if kind == "timeout":
                    yield Fail("transient", value)
                    return
                if kind == "out":
                    try:
                        d = json.loads(value)
                    except ValueError:
                        continue
                    for ev in self._parse(d):
                        if isinstance(ev, Fail):
                            yield ev
                            return
                        yield ev
                    if d.get("type") == "result":
                        got_result = True
                elif kind == "err" and not got_result:
                    msg = value.strip() or "Claude Code stopped without an answer"
                    yield Fail(classify_text(msg) if classify_text(msg) != "fatal" else "transient", msg[:300])
                    return

    @staticmethod
    def _parse(d: dict):
        t = d.get("type")
        if t == "stream_event":
            ev = d.get("event") or {}
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                yield Text(delta.get("text", ""))
        elif t == "rate_limit_event":
            info = d.get("rate_limit_info") or {}
            windows = {name: {"utilization": w.get("utilization"), "resets_at": w.get("resetsAt")}
                       for name, w in (info.get("unifiedWindows") or {}).items()}
            yield RateLimit(windows=windows, status=info.get("status", "allowed"))
        elif t == "result":
            u = d.get("usage") or {}
            model = next(iter(d.get("modelUsage") or {}), None)
            yield Usage(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                        cache_read_tokens=u.get("cache_read_input_tokens", 0),
                        cache_write_tokens=u.get("cache_creation_input_tokens", 0),
                        cost_usd=d.get("total_cost_usd"), model=model)
            if d.get("is_error"):
                msg = str(d.get("result") or d.get("subtype") or "Claude returned an error")
                status = d.get("api_error_status")
                kind = classify_status(status) if status else classify_text(msg)
                yield Fail(kind, msg[:300])
