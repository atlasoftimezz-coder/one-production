"""P5 fixtures: One Production with agent mode on, a scripted stand-in for the Claude Agent
SDK client, the stand-in voice app from P4, and the fake CLIs from P2 for sign-in.

FakeAgent plays a script the test sets, going through exactly what the real SDK would:
the PreToolUse hook, then can_use_tool when the hook says "ask", then the tool itself
(studio tools are the real handlers; Write really writes, so tests can check the disk).
"""
import asyncio
import concurrent.futures
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p2.conftest import FAKE, GOOD_KEY, MemoryKeyring, api_handler
from tests.p4.conftest import FakeVoiceApp, TOKEN

FAKES4 = Path(__file__).parents[1] / "p4" / "fakes"


# ---- message shapes, matched by class name like the real SDK's
@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list


@dataclass
class UserMessage:
    content: list


@dataclass
class StreamEvent:
    event: dict
    session_id: str = "s"
    uuid: str = "u"
    parent_tool_use_id: str | None = None


@dataclass
class RateLimitInfo:
    status: str = "allowed"
    raw: dict = field(default_factory=dict)


@dataclass
class RateLimitEvent:
    rate_limit_info: RateLimitInfo


@dataclass
class ResultMessage:
    session_id: str
    is_error: bool = False
    result: str | None = None
    errors: list | None = None
    subtype: str = "success"
    api_error_status: int | None = None
    usage: dict = field(default_factory=lambda: {"input_tokens": 40, "output_tokens": 90})
    model_usage: dict = field(default_factory=lambda: {"claude-sonnet-5-5": {}})
    total_cost_usd: float = 0.004


def delta(text):
    return StreamEvent({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


class Script:
    """What the next agent turn does. Steps:
        ("text", "...")                     streamed reply text
        ("tool", name, input)               a tool call (permission + execution like Claude Code)
        ("limit", windows_dict)             a rate-limit event
        ("sleep", seconds)                  keeps the run busy (for Stop / re-attach tests)
        ("fail", status, message)           ends the turn with an error result
    """
    def __init__(self):
        self.turns: list[list] = []
        self.seen: list[dict] = []           # options + prompt of every turn
        self.results: list[dict] = []        # tool results the agent got back


class FakeAgent:
    def __init__(self, options, script: Script):
        self.o, self.s = options, script
        self.prompt = None

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.prompt = prompt
        self.s.seen.append({"prompt": prompt, "cwd": self.o.cwd, "env": dict(self.o.env), "resume": self.o.resume,
                            "setting_sources": self.o.setting_sources, "system": self.o.system_prompt,
                            "cli": self.o.cli_path, "mcp": list(self.o.mcp_servers),
                            "model": self.o.model, "effort": self.o.effort})

    async def _tool(self, n, name, args):
        hook = self.o.hooks["PreToolUse"][0].hooks[0]
        out = await hook({"tool_name": name, "tool_input": args}, f"t{n}", None)
        decision = out["hookSpecificOutput"]["permissionDecision"]
        if decision == "deny":                       # like Claude Code: the tool never runs
            return out["hookSpecificOutput"]["permissionDecisionReason"], True
        if decision == "ask":
            res = await self.o.can_use_tool(name, args, None)
            if type(res).__name__ == "PermissionResultDeny":
                return res.message, True
        if name in self.o._studio_tools:
            r = await self.o._studio_tools[name].handler(args)
            return r["content"][0]["text"], bool(r.get("is_error"))
        if name == "Write":
            p = Path(args["file_path"])
            p = p if p.is_absolute() else Path(self.o.cwd) / p
            p.parent.mkdir(parents=True, exist_ok=True)              # like Claude Code's Write
            p.write_text(args["content"], encoding="utf-8")
            return f"wrote {p.name}", False
        return f"{name} ok", False

    async def receive_response(self):
        steps = self.s.turns.pop(0) if self.s.turns else [("text", "Done.")]
        sid = self.o.resume or "sess-1"
        for n, st in enumerate(steps):
            if st[0] == "text":
                yield delta(st[1])
            elif st[0] == "tool":
                _, name, args = st
                yield AssistantMessage([ToolUseBlock(f"t{n}", name, args)])
                text, err = await self._tool(n, name, args)
                self.s.results.append({"tool": name, "text": text, "error": err})
                yield UserMessage([ToolResultBlock(f"t{n}", text, err)])
            elif st[0] == "limit":
                yield RateLimitEvent(RateLimitInfo(raw={"unifiedWindows": st[1]}))
            elif st[0] == "sleep":
                await asyncio.sleep(st[1])
            elif st[0] == "fail":
                yield ResultMessage(sid, is_error=True, api_error_status=st[1], result=st[2], subtype="error")
                return
        yield ResultMessage(sid)


@pytest.fixture
def agent(tmp_path, monkeypatch, request):
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    request.addfinalizer(lambda: keyring.set_keyring(previous))
    bin_ = tmp_path / "bin"
    exe = bin_ / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")                                  # what the SDK would launch
    for tool in ("claude", "codex"):
        (bin_ / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{FAKE}" {tool} %*\r\n', encoding="utf-8")
    for k in ("FAKE_MODE", "FAKE_FAILS", "FAKE_UTIL", "FAKE_REPLY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls.txt"))
    monkeypatch.setattr("app.voice.POLL", 0.05)
    voice_dir = tmp_path / "vo-app"
    voice_dir.mkdir()
    for f in ("server.py", "make_srt.py"):
        (voice_dir / f).write_bytes((FAKES4 / f).read_bytes())
    fake_voice = FakeVoiceApp(tmp_path / "vo-outputs")
    script = Script()
    settings = Settings(data_dir=tmp_path / "data", claude_cmd=str(bin_ / "claude.cmd"),
                        codex_cmd=str(bin_ / "codex.cmd"), reply_timeout=20, login_console=False,
                        anthropic_base_url="https://api.test",
                        voice_app_dir=str(voice_dir), voice_python=sys.executable)
    app = create_app(settings, token=TOKEN, voice_transport=httpx.MockTransport(fake_voice.handler),
                     api_transport=httpx.MockTransport(api_handler),                    # never the real API
                     agent_client=lambda options: FakeAgent(options, script),
                     codex_transport=lambda argv, env, cwd: FakeCodex(argv, env, cwd))
    FakeCodex.shared = {}
    c = TestClient(app, base_url="http://127.0.0.1")
    c.__enter__()
    request.addfinalizer(lambda: c.__exit__(None, None, None))
    c.headers[TOKEN_HEADER] = TOKEN
    c.data_dir, c.script, c.fake, c.keyring = tmp_path / "data", script, fake_voice, kr
    c.codex = FakeCodex.shared
    c.get("/api/voice/status")
    c.pid = c.post("/api/projects", json={"name": "Debt"}).json()["id"]
    c.post(f"/api/projects/{c.pid}/files", files={"files": ("ep1.md", b"## Debt\nCancel the debt today.\n")})
    c.files = c.data_dir / "projects" / c.pid / "files"
    c.pool = concurrent.futures.ThreadPoolExecutor(2)
    request.addfinalizer(lambda: c.pool.shutdown(wait=False, cancel_futures=True))
    return c


def claude_account(c, email="me@example.com"):
    from tests.p2.conftest import signed_in
    import os
    os.environ["FAKE_EMAIL"] = email
    try:
        return signed_in(c, "claude")
    finally:
        os.environ.pop("FAKE_EMAIL", None)


def send(c, acc, message):
    """Start a message in the background (the stream only ends when the run does)."""
    return c.pool.submit(lambda: c.post(f"/api/projects/{c.pid}/chat",
                                        json={"message": message, "account_id": acc["id"]}))


def events(fut, timeout=20):
    r = fut.result(timeout)
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def wait_ask(c, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        asks = c.get(f"/api/projects/{c.pid}/agent").json()["asks"]
        if asks:
            return asks[0]
        time.sleep(0.05)
    raise AssertionError("no approval card appeared")


def answer(c, ask, how):
    r = c.post(f"/api/projects/{c.pid}/agent/answer", json={"id": ask["id"], "answer": how})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- Codex app-server stand-in
class FakeCodex:
    """Speaks the app-server JSON-RPC like codex-cli 0.157.1. The test sets shared["turns"]
    (lists of steps) and reads shared["log"] (every message we sent) and shared["decisions"].
        ("text", "...")                     item/agentMessage/delta
        ("cmd", command, actions)           commandExecution + approval request
        ("patch", path, inside)             fileChange; asks only when outside the project
        ("tool", name, args)                item/tool/call (dynamic studio tool)
        ("usage", inp, out)                 thread/tokenUsage/updated
        ("limits", used_5h_percent)         account/rateLimits/updated
        ("sleep", s) / ("fail", message, info)
    """
    shared: dict = {}

    def __init__(self, argv, env, cwd):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.st = FakeCodex.shared
        self.st.setdefault("log", []).append({"argv": argv, "env": env, "cwd": str(cwd), "msgs": []})
        self.me = self.st["log"][-1]
        self.waiting: dict = {}
        self.task = None

    async def start(self):
        self.task = asyncio.get_running_loop().create_task(self._serve())

    async def send(self, msg):
        self.me["msgs"].append(msg)
        await self.inbox.put(msg)

    async def recv(self):
        return await self.outbox.get()

    async def close(self):
        if self.task:
            self.task.cancel()

    async def _note(self, method, params):
        await self.outbox.put({"method": method, "params": params})

    async def _ask(self, rid, method, params):
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        await self.outbox.put({"id": rid, "method": method, "params": params})
        return await fut

    async def _serve(self):
        turn_task = None
        while True:
            msg = await self.inbox.get()
            if "method" not in msg:                         # a response to one of our requests
                fut = self.waiting.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg.get("result") or msg.get("error"))
                continue
            m, rid, p = msg["method"], msg.get("id"), msg.get("params") or {}
            if m == "initialize":
                await self.outbox.put({"id": rid, "result": {"userAgent": "fake"}})
            elif m in ("thread/start", "thread/resume"):
                tid = p.get("threadId") or "th-1"
                await self.outbox.put({"id": rid, "result": {"thread": {"id": tid}, "model": "gpt-fake"}})
            elif m == "model/list":
                await self.outbox.put({"id": rid, "result": {"data": self.st.get("models", [
                    {"id": "gpt-fast", "displayName": "GPT Fast", "isDefault": True, "hidden": False,
                     "defaultReasoningEffort": "low",
                     "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": ""},
                                                   {"reasoningEffort": "ultra", "description": ""}]},
                    {"id": "gpt-secret", "displayName": "Hidden", "hidden": True,
                     "supportedReasoningEfforts": []}]), "nextCursor": None}})
            elif m == "turn/start":
                await self.outbox.put({"id": rid, "result": {"turn": {"id": "tu-1"}}})
                turn_task = asyncio.get_running_loop().create_task(self._turn())
            elif m == "turn/interrupt":
                if turn_task:
                    turn_task.cancel()
                await self._note("turn/completed", {"threadId": "th-1", "turn": {"id": "tu-1", "status": "interrupted"}})

    async def _turn(self):
        steps = self.st["turns"].pop(0) if self.st.get("turns") else [("text", "Done.")]
        ids = {"threadId": "th-1", "turnId": "tu-1"}
        for n, st in enumerate(steps):
            iid = f"it{n}"
            if st[0] == "text":
                await self._note("item/agentMessage/delta", {**ids, "itemId": iid, "delta": st[1]})
            elif st[0] == "cmd":
                _, command, actions = st
                item = {"type": "commandExecution", "id": iid, "command": command, "commandActions": actions,
                        "status": "inProgress"}
                await self._note("item/started", {**ids, "item": item, "startedAtMs": 0})
                r = await self._ask(100 + n, "item/commandExecution/requestApproval",
                                    {**ids, "itemId": iid, "command": command, "commandActions": actions})
                self.st.setdefault("decisions", []).append(r["decision"])
                ok = r["decision"] in ("accept", "acceptForSession")
                await self._note("item/completed", {**ids, "completedAtMs": 0, "item": {
                    **item, "status": "completed" if ok else "declined", "exitCode": 0 if ok else None,
                    "aggregatedOutput": "ok" if ok else ""}})
            elif st[0] == "patch":
                _, path, inside = st
                item = {"type": "fileChange", "id": iid, "status": "inProgress",
                        "changes": [{"path": path, "kind": {"type": "add"}, "diff": "+x"}]}
                await self._note("item/started", {**ids, "item": item, "startedAtMs": 0})
                ok = True
                if not inside:
                    r = await self._ask(100 + n, "item/fileChange/requestApproval",
                                        {**ids, "itemId": iid, "startedAtMs": 0})
                    self.st.setdefault("decisions", []).append(r["decision"])
                    ok = r["decision"] == "accept"
                await self._note("item/completed", {**ids, "completedAtMs": 0,
                                                    "item": {**item, "status": "completed" if ok else "declined"}})
            elif st[0] == "tool":
                _, name, args = st
                r = await self._ask(100 + n, "item/tool/call", {**ids, "callId": iid, "tool": name, "arguments": args})
                self.st.setdefault("tool_results", []).append(r)
            elif st[0] == "usage":
                await self._note("thread/tokenUsage/updated", {**ids, "tokenUsage": {
                    "last": {"inputTokens": st[1], "outputTokens": st[2], "cachedInputTokens": 0},
                    "total": {"inputTokens": st[1], "outputTokens": st[2], "cachedInputTokens": 0}}})
            elif st[0] == "limits":
                await self._note("account/rateLimits/updated", {"rateLimits": {
                    "primary": {"usedPercent": st[1], "windowDurationMins": 300, "resetsAt": int(time.time()) + 3600},
                    "secondary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": int(time.time()) + 86400}}})
            elif st[0] == "sleep":
                await asyncio.sleep(st[1])
            elif st[0] == "fail":
                await self._note("turn/completed", {"threadId": "th-1", "turn": {
                    "id": "tu-1", "status": "failed", "error": {"message": st[1], "codexErrorInfo": st[2]}}})
                return
        await self._note("turn/completed", {"threadId": "th-1", "turn": {"id": "tu-1", "status": "completed"}})


def codex_account(c):
    from tests.p2.conftest import signed_in
    return signed_in(c, "codex")
