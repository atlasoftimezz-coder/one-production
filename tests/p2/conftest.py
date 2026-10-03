import json
import sys
from pathlib import Path

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

TOKEN = "test-session-token-p2-0123456789abcdef"
FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"
GOOD_KEY = "sk-ant-api03-GOODgoodGOODgoodGOODgood1234"


class MemoryKeyring(KeyringBackend):
    """Tests must never write into the user's real Windows Credential Manager."""
    priority = 1

    def __init__(self):
        self.store = {}

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, user)]


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(previous)


@pytest.fixture
def fake_cmds(tmp_path):
    """claude.cmd / codex.cmd shims, exactly like npm installs on Windows."""
    d = tmp_path / "bin"
    d.mkdir()
    for tool in ("claude", "codex"):
        (d / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{FAKE}" {tool} %*\r\n', encoding="utf-8")
    return d


def api_handler(request: httpx.Request) -> httpx.Response:
    key = request.headers.get("x-api-key", "")
    if not key.startswith("sk-ant-api03-GOOD"):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                     "message": "invalid x-api-key"}})
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"data": []})
    if key.endswith("LIMIT"):
        return httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit_error",
                                                                     "message": "rate limited"}})
    body = json.loads(request.content)
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 21, "output_tokens": 1}}},
        *({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w + " "}}
          for w in ["Fake", "API", "says", "hello", f"({body['model']})"]),
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})


@pytest.fixture
def make_client(tmp_path, fake_cmds, monkeypatch, request):
    monkeypatch.setenv("ONEPROD_RETRY_BACKOFF", "0.01,0.01,0.01")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "calls.txt"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "prompts.jsonl"))
    for k in ("FAKE_MODE", "FAKE_FAILS", "FAKE_EMAIL"):
        monkeypatch.delenv(k, raising=False)

    def make(reply_timeout=20):
        settings = Settings(data_dir=tmp_path / "data", claude_cmd=str(fake_cmds / "claude.cmd"),
                            codex_cmd=str(fake_cmds / "codex.cmd"), reply_timeout=reply_timeout,
                            anthropic_base_url="https://api.test")
        app = create_app(settings, token=TOKEN, api_transport=httpx.MockTransport(api_handler),
                         login_console=False)
        c = TestClient(app, base_url="http://127.0.0.1")
        # One event loop for the whole test, like the real server; otherwise subprocess pipe
        # handles outlive a per-request loop and Windows reports "Event loop is closed".
        c.__enter__()
        request.addfinalizer(lambda: c.__exit__(None, None, None))
        c.headers[TOKEN_HEADER] = TOKEN
        c.data_dir = tmp_path / "data"
        c.tmp = tmp_path
        return c
    return make


@pytest.fixture
def client(make_client):
    return make_client()


def calls(c) -> int:
    p = c.tmp / "calls.txt"
    return int(p.read_text()) if p.exists() else 0


def chat(c, pid, message, account_id):
    """Send a message and return the parsed NDJSON events."""
    r = c.post(f"/api/projects/{pid}/chat", json={"message": message, "account_id": account_id})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def text_of(events) -> str:
    return "".join(e["v"] for e in events if e["t"] == "text")


def error_of(events):
    return next((e for e in events if e["t"] == "error"), None)


def signed_in(c, provider, email="friend@example.com", monkeypatch=None):
    if monkeypatch:
        monkeypatch.setenv("FAKE_EMAIL", email)
    acc = c.post("/api/accounts", json={"provider": provider}).json()
    assert c.post(f"/api/accounts/{acc['id']}/login").status_code == 200
    import time
    for _ in range(50):
        a = c.post(f"/api/accounts/{acc['id']}/check").json()
        if a["status"] == "ok":
            return a
        time.sleep(0.1)
    raise AssertionError(f"{provider} account never became signed in: {a}")


@pytest.fixture
def project(client):
    return client.post("/api/projects", json={"name": "P2"}).json()["id"]
