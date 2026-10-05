"""Codex via the Codex CLI, using the account's own login folder (CODEX_HOME).

Formats verified against codex-cli 0.157.1 (2026-10-03):
  item.completed {item.type: agent_message, text}  -> reply text (arrives whole, not streamed)
  turn.completed {usage: input_tokens, cached_input_tokens, output_tokens}
  error / turn.failed                               -> failure
The --json stream carries no rate-limit info, but every saved session file
(CODEX_HOME/sessions/**/rollout-*.jsonl) records exact limits:
  "rate_limits": {"primary": {"used_percent": 35.0, "window_minutes": 300, "resets_at": ...},
                  "secondary": {"used_percent": 10.0, "window_minutes": 10080, "resets_at": ...}}
So sessions are kept (no --ephemeral) and read back after each reply and on refresh.
"""
from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator

from .base import Event, Fail, RateLimit, Text, Usage, classify_text
from .cli import resolve, run_capture, run_lines

ARGS = ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-"]
NEWEST_FILES = 5


def _window_name(minutes) -> str:
    return {300: "five_hour", 10080: "seven_day"}.get(int(minutes or 0), f"{minutes}_min")


def read_rate_limits(codex_home: str | Path) -> dict | None:
    """Latest rate_limits recorded in this account's own session files. Pure file read."""
    root = Path(codex_home) / "sessions"
    if not root.is_dir():
        return None
    files = sorted(root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:NEWEST_FILES]:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rl = _find(d, "rate_limits")
            if not rl:
                continue
            windows = {}
            for key in ("primary", "secondary"):
                w = rl.get(key) or {}
                if w.get("used_percent") is not None:
                    windows[_window_name(w.get("window_minutes"))] = {
                        "utilization": float(w["used_percent"]) / 100, "resets_at": w.get("resets_at")}
            if windows:
                return {"windows": windows, "as_of": f.stat().st_mtime}
    return None


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], dict):
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r:
                return r
    return None


class CodexCLI:
    name = "codex"

    def __init__(self, command: str | None, work_dir: Path, timeout: float = 300):
        self.command = command
        self.work_dir = work_dir
        self.timeout = timeout

    def _exe(self) -> str | None:
        return resolve("codex", self.command)

    @staticmethod
    def env(account: dict) -> dict[str, str]:
        return {"CODEX_HOME": account["profile_dir"]}

    async def status(self, account: dict) -> dict:
        exe = self._exe()
        if not exe:
            return {"ok": False, "reason": "Codex CLI is not installed"}
        code, out, err = await run_capture([exe, "login", "status"], self.env(account),
                                           self.work_dir, timeout=30)
        text = (out + "\n" + err).strip()
        if "not logged in" in text.lower() or code != 0:
            return {"ok": False, "reason": "not logged in"}
        method = "ChatGPT" if "chatgpt" in text.lower() else ("API key" if "api key" in text.lower() else None)
        return {"ok": True, "email": None, "plan": None, "method": method}

    async def live_usage(self, account: dict) -> dict | None:
        return read_rate_limits(account["profile_dir"])

    def login_argv(self) -> list[str] | None:
        exe = self._exe()
        return [exe, "login"] if exe else None

    async def stream(self, account: dict, prompt: str, model: str | None,
                     effort: str | None = None) -> AsyncIterator[Event]:
        exe = self._exe()
        if not exe:
            yield Fail("fatal", "Codex CLI is not installed on this PC")
            return
        argv = [exe, *ARGS[:-1]]
        if model and model.replace("-", "").replace(".", "").isalnum():
            argv += ["--model", model]
        if effort and effort.isalnum():
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv.append(ARGS[-1])
        finished = False
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
                    t = d.get("type")
                    if t == "item.completed" and (d.get("item") or {}).get("type") == "agent_message":
                        yield Text((d["item"].get("text") or ""))
                    elif t == "turn.completed":
                        u = d.get("usage") or {}
                        yield Usage(input_tokens=u.get("input_tokens", 0),
                                    output_tokens=u.get("output_tokens", 0),
                                    cache_read_tokens=u.get("cached_input_tokens", 0), model=model)
                        finished = True
                        rl = read_rate_limits(account["profile_dir"])
                        if rl:
                            yield RateLimit(windows=rl["windows"])
                    elif t in ("error", "turn.failed"):
                        msg = str(d.get("message") or (d.get("error") or {}).get("message") or d)[:300]
                        yield Fail(classify_text(msg), msg)
                        return
                elif kind == "err" and not finished:
                    msg = value.strip() or "Codex stopped without an answer"
                    kind_ = classify_text(msg)
                    yield Fail(kind_ if kind_ != "fatal" else "transient", msg[-300:])
                    return
