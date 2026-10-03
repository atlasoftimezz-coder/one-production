"""Claude via the Claude Code CLI, using the account's own login folder.

Formats verified against Claude Code 2.1.284 (2026-10-03):
  stream_event / content_block_delta / text_delta   -> reply text (thinking deltas skipped)
  rate_limit_event.rate_limit_info.unifiedWindows   -> exact five_hour / seven_day utilisation
  result (is_error, api_error_status, usage, total_cost_usd)
"""
from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator

from .base import Event, Fail, RateLimit, Text, Usage, classify_status, classify_text
from .cli import resolve, run_capture, run_lines

MODELS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
ARGS = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", "", "--no-session-persistence", "--strict-mcp-config"]


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
        return {"CLAUDE_CONFIG_DIR": account["profile_dir"]}

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
        return {"ok": True, "email": d.get("email"), "plan": d.get("subscriptionType"),
                "org": d.get("orgName"), "method": d.get("authMethod")}

    def login_argv(self) -> list[str] | None:
        exe = self._exe()
        return [exe, "auth", "login"] if exe else None

    async def stream(self, account: dict, prompt: str, model: str | None) -> AsyncIterator[Event]:
        exe = self._exe()
        if not exe:
            yield Fail("fatal", "Claude Code is not installed on this PC")
            return
        argv = [exe, *ARGS]
        if model in MODELS:
            argv += ["--model", MODELS[model]]
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
