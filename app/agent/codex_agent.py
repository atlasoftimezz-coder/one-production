"""Codex logins as a real Codex agent, through the official `codex app-server` (JSON-RPC over
stdio - the same protocol Codex's own editor extensions use).

    thread/start | thread/resume   cwd = project folder, workspace-write sandbox, "untrusted"
                                   approvals (commands ask; read-only ones Codex knows are safe
                                   run), studio tools as dynamic tools, our instructions
    turn/start                     the user's message
    server requests                command / file-change approvals -> the same policy and
                                   approval cards as Claude; item/tool/call -> studio tools
    notifications                  text deltas, item started/completed (steps), token usage,
                                   rate limits, turn/completed

Protocol checked against codex-cli 0.157.1 (`codex app-server generate-json-schema --experimental`).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from ..providers.base import Fail, Usage, classify_text
from ..providers.cli import resolve
from ..version import __version__
from . import policy
from .runs import Run, Runs
from .studio import Studio, call

CODEX_NOTE = """
For Codex in One Production: create and change files with apply_patch, not with shell commands
(Set-Content, echo >, Out-File...) - edits inside the project run without asking, shell commands
always need the user's OK. Reading files with shell commands is fine."""
DECISION = {"once": "accept", "always": "accept", "session": "acceptForSession", "deny": "decline"}
ACTION_TOOL = {"read": "Read", "listFiles": "LS", "search": "Grep"}


def command_as_tools(command: str, actions: list | None) -> list[tuple[str, dict]]:
    """Codex has no separate read tool: on Windows it reads with PowerShell. Its own parse of
    the command (commandActions) says what it does, so a read is judged like Claude's Read
    (free inside the project, asks outside) and anything else like a command (always asks)."""
    acts = [a for a in actions or [] if isinstance(a, dict)]
    if not acts or any(a.get("type") not in ACTION_TOOL for a in acts):
        return [("Bash", {"command": command or ""})]
    out = []
    for a in acts:
        tool = ACTION_TOOL[a["type"]]
        out.append((tool, {"file_path": a.get("path")} if tool == "Read" else {"path": a.get("path"),
                                                                                "pattern": a.get("query") or ""}))
    return out


class StdioTransport:
    """Newline-delimited JSON-RPC with a child process."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env={**os.environ, **self.env}, cwd=str(self.cwd),
            limit=16 * 1024 * 1024)

    async def send(self, msg: dict) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def recv(self) -> dict | None:
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line:
                try:
                    return json.loads(line)
                except ValueError:
                    continue

    async def close(self) -> None:
        if self.proc and self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
                # read to EOF so the pipe transports close now, not at garbage collection
                await asyncio.wait_for(self.proc.stdout.read(), 2)
            except (asyncio.TimeoutError, OSError, ValueError):
                pass


class CodexAgent:
    def __init__(self, command: str | None, *, transport_factory: Callable | None = None):
        self.command = command
        self.transport_factory = transport_factory or StdioTransport

    async def list_models(self, account: dict, cwd: Path, timeout: float = 30) -> list[dict]:
        """The models (and their efforts) this Codex login offers - `model/list`, no turn, no cost."""
        exe = resolve("codex", self.command)
        if not exe:
            raise RuntimeError("Codex is not installed")
        cwd.mkdir(parents=True, exist_ok=True)
        t = self.transport_factory([exe, "app-server"], {"CODEX_HOME": account["profile_dir"]}, cwd)

        async def ask(rid: int, method: str, params: dict) -> dict:
            await t.send({"id": rid, "method": method, "params": params})
            while True:
                msg = await t.recv()
                if msg is None:
                    raise RuntimeError("Codex stopped")
                if msg.get("id") == rid and "method" not in msg:
                    if "error" in msg:
                        raise RuntimeError(str((msg["error"] or {}).get("message") or msg["error"]))
                    return msg.get("result") or {}

        async def go() -> list[dict]:
            await t.start()
            await ask(1, "initialize", {"clientInfo": {"name": "one-production", "title": "One Production",
                                                       "version": __version__},
                                        "capabilities": {"experimentalApi": True}})
            await t.send({"method": "initialized"})
            found, cursor, rid = [], None, 1
            for _ in range(10):                      # pages
                rid += 1
                res = await ask(rid, "model/list", {"cursor": cursor} if cursor else {})
                found += res.get("data") or []
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            return found

        try:
            return await asyncio.wait_for(go(), timeout)
        finally:
            await t.close()

    async def run(self, run: Run, runs: Runs, account: dict, prompt: str, session_id: str | None,
                  instructions: str, studio: Studio, model: str | None, gate,
                  effort: str | None = None) -> dict:
        out: dict[str, Any] = {"session_id": session_id, "text": "", "usage": None, "windows": None,
                               "error": None, "steps": []}
        exe = resolve("codex", self.command)
        if not exe:
            out["error"] = Fail("fatal", "Codex is not installed")
            return out
        tools = {t.name: t for t in studio.tools()}
        t = self.transport_factory([exe, "app-server"], {"CODEX_HOME": account["profile_dir"]}, run.root)
        texts: list[str] = []
        steps: dict[str, dict] = {}
        paths: dict[str, list[str]] = {}          # fileChange item id -> paths (for approvals)
        usage = Usage(model=model)
        state = {"next": 0, "thread": session_id, "turn": None, "last_item": None}
        pending: dict[int, asyncio.Future] = {}

        async def request(method: str, params: dict) -> dict:
            state["next"] += 1
            rid = state["next"]
            fut = asyncio.get_running_loop().create_future()
            pending[rid] = fut
            await t.send({"id": rid, "method": method, "params": params})
            return await fut

        async def answer(rid, result: dict) -> None:
            await t.send({"id": rid, "result": result})

        async def on_server_request(msg: dict) -> None:
            method, p, rid = msg["method"], msg.get("params") or {}, msg["id"]
            if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
                cmd = p.get("command")
                if isinstance(cmd, list):
                    cmd = " ".join(cmd)
                ok = True
                for tool, args in command_as_tools(cmd, p.get("commandActions")):
                    allowed, _ = await runs.permit(run, tool, args)
                    ok = ok and allowed
                    if not ok:
                        break
                await answer(rid, {"decision": DECISION["once" if ok else "deny"]})
            elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
                files = paths.get(p.get("itemId"), []) or [p.get("grantRoot") or "(outside the project)"]
                ok = True
                for f in files:
                    allowed, _ = await runs.permit(run, "Write", {"file_path": f})
                    ok = ok and allowed
                await answer(rid, {"decision": DECISION["once" if ok else "deny"]})
            elif method == "item/tool/call":
                name, args = p.get("tool"), p.get("arguments") or {}
                tool = tools.get(name)
                if not tool:
                    await answer(rid, {"contentItems": [{"type": "inputText", "text": f"unknown tool {name}"}],
                                       "success": False})
                    return
                ok, why = await runs.permit(run, name, args, gate=gate)
                if not ok:
                    text, err = why, True
                else:
                    text, err = await call(tool, args)
                await answer(rid, {"contentItems": [{"type": "inputText", "text": text}], "success": not err})
            else:                                   # questions we don't support: say no politely
                await t.send({"id": rid, "error": {"code": -32601, "message": f"{method} not supported here"}})

        def on_item(item: dict, done: bool) -> None:
            kind, iid = item.get("type"), item.get("id")
            if kind == "agentMessage":
                if not done and texts and not texts[-1].endswith("\n"):
                    texts.append("\n\n"); run.emit({"t": "text", "v": "\n\n"})
                return
            if kind == "fileChange":
                paths[iid] = [c.get("path") for c in item.get("changes") or [] if c.get("path")]
            tool, args = _as_tool(item)
            if not tool:
                return
            if not done and iid not in steps:
                title, detail = policy.describe(tool, args, run.root)
                steps[iid] = {"id": iid, "tool": tool, "title": title, "detail": detail}
                run.emit({"t": "step", **steps[iid]})
            elif done:
                if iid not in steps:
                    title, detail = policy.describe(tool, args, run.root)
                    steps[iid] = {"id": iid, "tool": tool, "title": title, "detail": detail}
                    run.emit({"t": "step", **steps[iid]})
                s = steps[iid]
                s["ok"] = item.get("status") in (None, "completed") and item.get("exitCode") in (None, 0) \
                    and item.get("success") is not False
                s["summary"] = _summary(item)
                run.emit({"t": "step_done", "id": iid, "ok": s["ok"], "summary": s["summary"]})

        finished = asyncio.Event()

        async def reader() -> None:
            while True:
                msg = await t.recv()
                if msg is None:
                    for f in pending.values():
                        if not f.done():
                            f.set_exception(RuntimeError("Codex stopped unexpectedly"))
                    finished.set()
                    return
                if "method" in msg and "id" in msg:                 # a request from the server
                    asyncio.get_running_loop().create_task(on_server_request(msg))
                elif "id" in msg:                                     # a response to us
                    f = pending.pop(msg["id"], None)
                    if f and not f.done():
                        if "error" in msg:
                            f.set_exception(RuntimeError((msg["error"] or {}).get("message", "error")))
                        else:
                            f.set_result(msg.get("result") or {})
                else:
                    on_notification(msg.get("method"), msg.get("params") or {})

        def on_notification(method: str, p: dict) -> None:
            if method == "item/agentMessage/delta":
                texts.append(p.get("delta", "")); run.emit({"t": "text", "v": p.get("delta", "")})
            elif method in ("item/started", "item/completed"):
                on_item(p.get("item") or {}, method == "item/completed")
            elif method == "thread/tokenUsage/updated":
                last = (p.get("tokenUsage") or {}).get("last") or {}
                usage.input_tokens += last.get("inputTokens", 0)
                usage.output_tokens += last.get("outputTokens", 0)
                usage.cache_read_tokens += last.get("cachedInputTokens", 0)
            elif method == "account/rateLimits/updated":
                w = _windows(p.get("rateLimits") or {})
                if w:
                    out["windows"] = w
                    run.emit({"t": "limit", "windows": w, "status": "allowed"})
            elif method == "turn/completed":
                turn = p.get("turn") or {}
                if turn.get("status") == "failed":
                    err = turn.get("error") or {}
                    message = err.get("message") or "Codex failed"
                    info = json.dumps(err.get("codexErrorInfo") or "")
                    kind = "limit" if "usageLimit" in info or "UsageLimit" in info else classify_text(message)
                    out["error"] = Fail(kind, message[:500], interrupted=bool(texts))
                elif turn.get("status") == "interrupted":
                    out["error"] = Fail("fatal", "Stopped.", interrupted=bool(texts))
                finished.set()
            elif method == "error":
                e = p.get("error") or p
                message = e.get("message") or "Codex error"
                if not p.get("willRetry"):
                    out["error"] = out["error"] or Fail(classify_text(message), message[:500], interrupted=bool(texts))

        read_task = None
        try:
            await t.start()
            read_task = asyncio.get_running_loop().create_task(reader())
            await request("initialize", {"clientInfo": {"name": "one-production", "title": "One Production",
                                                        "version": __version__},
                                         "capabilities": {"experimentalApi": True}})
            await t.send({"method": "initialized"})
            common = {"cwd": str(run.root), "sandbox": "workspace-write", "approvalPolicy": "untrusted",
                      "developerInstructions": instructions + CODEX_NOTE, "model": model or None}
            dyn = [{"type": "function", "name": x.name, "description": x.description, "inputSchema": x.schema}
                   for x in tools.values()]
            res = None
            if session_id:
                try:
                    res = await request("thread/resume", {"threadId": session_id, **common})
                except RuntimeError:
                    res = None                      # thread gone (archived/cleaned up): start fresh
            if res is None:
                res = await request("thread/start", {**common, "dynamicTools": dyn})
            state["thread"] = ((res.get("thread") or {}).get("id")) or state["thread"]
            usage.model = res.get("model") or usage.model
            out["session_id"] = state["thread"]
            ts = {"threadId": state["thread"], "input": [{"type": "text", "text": prompt, "text_elements": []}]}
            if model:
                ts["model"] = model
            if effort:
                ts["effort"] = effort            # a ReasoningEffort the model advertises (model/list)
            turn = await request("turn/start", ts)
            state["turn"] = (turn.get("turn") or {}).get("id")
            await finished.wait()
        except asyncio.CancelledError:
            if state["turn"]:
                try:
                    await asyncio.wait_for(t.send({"id": 10**6, "method": "turn/interrupt",
                                                   "params": {"threadId": state["thread"], "turnId": state["turn"]}}), 2)
                except Exception:                  # noqa: BLE001
                    pass
            out["error"] = Fail("fatal", "Stopped.", interrupted=bool(texts))
        except Exception as e:                     # noqa: BLE001
            msg_ = str(e) or e.__class__.__name__
            out["error"] = out["error"] or Fail(classify_text(msg_), msg_[:500], interrupted=bool(texts))
        finally:
            if read_task:
                read_task.cancel()
            await t.close()
        out["text"] = "".join(texts).strip()
        out["steps"] = list(steps.values())
        out["usage"] = usage
        return out


def _as_tool(item: dict) -> tuple[str | None, dict]:
    """Codex thread item -> (tool name in policy.py terms, arguments) for describe()."""
    kind = item.get("type")
    if kind == "commandExecution":
        return command_as_tools(item.get("command") or "", item.get("commandActions"))[0]
    if kind == "fileChange":
        first = next(iter(item.get("changes") or []), {})
        return ("Write" if first.get("kind") in ("add", {"type": "add"}) else "Edit"), {"file_path": first.get("path", "")}
    if kind == "dynamicToolCall":
        return item.get("tool"), item.get("arguments") or {}
    if kind == "webSearch":
        return "WebSearch", {"query": item.get("query") or ""}
    if kind == "mcpToolCall":
        return f"{item.get('server')}:{item.get('tool')}", item.get("arguments") or {}
    return None, {}


def _summary(item: dict) -> str:
    s = item.get("aggregatedOutput") or ""
    if not s and item.get("contentItems"):
        s = " ".join(c.get("text", "") for c in item["contentItems"] if isinstance(c, dict))
    s = " ".join(str(s).split())
    return s[:160] + ("…" if len(s) > 160 else "")


def _windows(snapshot: dict) -> dict | None:
    out = {}
    for key in ("primary", "secondary"):
        w = snapshot.get(key)
        if not w or w.get("usedPercent") is None:
            continue
        name = {300: "five_hour", 10080: "seven_day"}.get(w.get("windowDurationMins"), f"{w.get('windowDurationMins')}_min")
        out[name] = {"utilization": w["usedPercent"] / 100, "resets_at": w.get("resetsAt")}
    return out or None
