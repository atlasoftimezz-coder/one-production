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
                            "cli": self.o.cli_path, "mcp": list(self.o.mcp_servers)})

    async def _tool(self, n, name, args):
        hook = self.o.hooks["PreToolUse"][0].hooks[0]
        out = await hook({"tool_name": name, "tool_input": args}, f"t{n}", None)
        decision = out["hookSpecificOutput"]["permissionDecision"]
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
                     agent_client=lambda options: FakeAgent(options, script))
    c = TestClient(app, base_url="http://127.0.0.1")
    c.__enter__()
    request.addfinalizer(lambda: c.__exit__(None, None, None))
    c.headers[TOKEN_HEADER] = TOKEN
    c.data_dir, c.script, c.fake, c.keyring = tmp_path / "data", script, fake_voice, kr
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
