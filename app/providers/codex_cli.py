"""Codex via the Codex CLI, using the account's own login folder (CODEX_HOME).

Formats verified against codex-cli 0.157.1 (2026-10-03):
  item.completed {item.type: agent_message, text}  -> reply text (arrives whole, not streamed)
  turn.completed {usage: input_tokens, cached_input_tokens, output_tokens}
  error / turn.failed                               -> failure
No rate-limit information is emitted, so Codex usage is estimated from tokens (plan R2).
"""
from __future__ import annotations

import json
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator

from .base import Event, Fail, Text, Usage, classify_text
from .cli import resolve, run_capture, run_lines

ARGS = ["exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "-"]


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

    def login_argv(self) -> list[str] | None:
        exe = self._exe()
        return [exe, "login"] if exe else None

    async def stream(self, account: dict, prompt: str, model: str | None) -> AsyncIterator[Event]:
        exe = self._exe()
        if not exe:
            yield Fail("fatal", "Codex CLI is not installed on this PC")
            return
        argv = [exe, *ARGS[:-1]]
        if model and model.replace("-", "").replace(".", "").isalnum():
            argv += ["--model", model]
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
                    elif t in ("error", "turn.failed"):
                        msg = str(d.get("message") or (d.get("error") or {}).get("message") or d)[:300]
                        yield Fail(classify_text(msg), msg)
                        return
                elif kind == "err" and not finished:
                    msg = value.strip() or "Codex stopped without an answer"
                    kind_ = classify_text(msg)
                    yield Fail(kind_ if kind_ != "fatal" else "transient", msg[-300:])
                    return
