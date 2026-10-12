"""The MCP page (owner, 2026-10-12): custom servers with an on/off switch, token headers kept in Credential
Manager (only the names in settings.json), and a Test button that connects and lists the tools."""
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mcp_servers
from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

TOKEN = "test-session-token-0123456789abcdef"
FAKE_STDIO = Path(__file__).parent / "fakes" / "fake_mcp_stdio.py"


@pytest.fixture
def memory_keyring():
    import keyring
    from app.security.memkeyring import MemoryKeyring
    prev = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(prev)


def remote(request: httpx.Request) -> httpx.Response:
    """A streamable-http MCP server: wants the token, answers tools/list as server-sent events."""
    if request.headers.get("Authorization") != "Bearer good":
        return httpx.Response(401)
    body = json.loads(request.content)
    if body.get("method") == "initialize":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
                              headers={"mcp-session-id": "s1"})
    if body.get("method") == "tools/list":
        assert request.headers.get("mcp-session-id") == "s1"
        data = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "search"}, {"name": "upload"}]}})
        return httpx.Response(200, text=f"event: message\ndata: {data}\n\n", headers={"content-type": "text/event-stream"})
    return httpx.Response(202)


@pytest.fixture
def client(tmp_path, memory_keyring, monkeypatch):
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    app = create_app(Settings(data_dir=tmp_path / "data"), token=TOKEN, mcp_transport=httpx.MockTransport(remote))
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers[TOKEN_HEADER] = TOKEN
    c.settings_file = tmp_path / "settings.json"
    return c


def test_header_values_go_to_credential_manager_and_off_servers_stay_out_of_the_agent(client, memory_keyring):
    import keyring
    from app.accounts import KEYRING_SERVICE
    r = client.put("/api/mcp-servers", json={"servers": {
        "notes": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer good"}},
        "files": {"type": "stdio", "command": "npx", "args": ["-y", "fs-server"], "enabled": False}}})
    assert r.status_code == 200
    saved = json.loads(client.settings_file.read_text(encoding="utf-8"))["mcp_servers"]
    assert saved["notes"]["headers"] == ["Authorization"] and "Bearer good" not in client.settings_file.read_text()
    assert saved["files"]["enabled"] is False
    assert keyring.get_password(KEYRING_SERVICE, "mcp-header:notes:authorization") == "Bearer good"
    agent = mcp_servers.for_agent(Settings(data_dir=client.settings_file.parent / "data", mcp_servers=saved))
    assert agent["servers"]["notes"]["headers"] == {"Authorization": "Bearer good"} and "files" not in agent["servers"]
    # saving the list again (the page sends names only) keeps the token; removing the server forgets it
    client.put("/api/mcp-servers", json={"servers": {"notes": saved["notes"]}})
    assert keyring.get_password(KEYRING_SERVICE, "mcp-header:notes:authorization") == "Bearer good"
    client.put("/api/mcp-servers", json={"servers": {}})
    assert keyring.get_password(KEYRING_SERVICE, "mcp-header:notes:authorization") is None


def test_the_test_button_lists_a_remote_servers_tools(client):
    client.put("/api/mcp-servers", json={"servers": {
        "notes": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer good"}},
        "wrong": {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer bad"}}}})
    assert client.post("/api/mcp-servers/notes/test").json() == {"ok": True, "tools": ["search", "upload"]}
    bad = client.post("/api/mcp-servers/wrong/test").json()
    assert not bad["ok"] and "refused the login" in bad["error"]
    assert client.post("/api/mcp-servers/nobody/test").status_code == 404


def test_the_test_button_starts_a_local_program_and_lists_its_tools(client):
    client.put("/api/mcp-servers", json={"servers": {
        "local": {"type": "stdio", "command": sys.executable, "args": [str(FAKE_STDIO)]},
        "missing": {"type": "stdio", "command": "no-such-program-xyz"}}})
    assert client.post("/api/mcp-servers/local/test").json() == {"ok": True, "tools": ["read_file", "list_dir"]}
    gone = client.post("/api/mcp-servers/missing/test").json()
    assert not gone["ok"] and "could not start" in gone["error"]


@pytest.mark.parametrize("servers, why", [
    ({"x": {"type": "http", "url": "https://a/b", "headers": {"Bad Header": "v"}}}, "header names"),
    ({"x": {"type": "http", "url": "https://a/b", "headers": "Authorization"}}, "list of header names"),
])
def test_bad_headers_are_refused(servers, why):
    with pytest.raises(mcp_servers.McpError, match=why):
        mcp_servers.check(servers, {})
