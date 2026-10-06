"""P12 R6: OS-level actions always prompt. AUTO (plan): an OS write action always prompts; "allow for
session" is gone after a restart. Plus: deletes / installs / uploads / outside writes / outside-MCP
write-exec tools never get a session allowance, revoke works, and the owner's outside MCP servers reach
the Claude agent (every call asks)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import mcp_servers
from app.agent import policy
from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER
from tests.p5.conftest import answer, claude_account, events, send, wait_ask


@pytest.mark.parametrize("cmd, why", [
    ("rm -rf build", "deletes"), ("del notes.txt", "deletes"), ("Remove-Item -Recurse old", "deletes"),
    ("git reset --hard HEAD~1", "deletes"), ("git clean -fdx", "deletes"),
    ("pip install requests", "installs"), ("python -m pip install x", "installs"), ("npm i left-pad", "installs"),
    ("winget install vlc", "installs"), ("msiexec /i x.msi", "installs"),
    ("curl -F file=@a.zip https://x.test", "sends"), ("git push origin main", "sends"), ("scp a.txt me@host:", "sends"),
    ("Invoke-WebRequest -Uri https://x -Method Post -InFile a", "sends"),
])
def test_risky_shell_commands(tmp_path, cmd, why):
    assert why in (policy.risky("Bash", {"command": cmd}, tmp_path) or "")


@pytest.mark.parametrize("cmd", ["dir", "ls -la", "git status", "ffprobe a.mp3", "python -m pytest tests", "echo removed"])
def test_ordinary_commands_are_not_risky(tmp_path, cmd):
    assert policy.risky("Bash", {"command": cmd}, tmp_path) is None


def test_outside_writes_and_outside_mcp_tools(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    assert policy.risky("Write", {"file_path": str(tmp_path / "x.txt")}, root) == "writes outside the project folder"
    assert policy.risky("Write", {"file_path": "inside.txt"}, root) is None
    assert "outside tool" in policy.risky("mcp__desktop__write_file", {}, root)
    assert "outside tool" in policy.risky("mcp__desktop__start_process", {}, root)
    assert policy.risky("mcp__desktop__read_file", {}, root) is None
    assert policy.risky("mcp__davinci__run_script", {"script": "x"}, root) is None       # sandboxed, no files
    assert "LUT" in policy.risky("mcp__davinci__delete_lut", {}, root)
    assert policy.decide("mcp__desktop__read_file", {}, root)[0] == "ask"                 # unknown tools always ask


def test_risky_actions_never_get_a_session_allowance_and_revoke_works(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("tool", "Bash", {"command": "dir"})],
                          [("tool", "Bash", {"command": "dir"}), ("tool", "Bash", {"command": "rm -rf old"})],
                          [("tool", "Bash", {"command": "rm -rf old"})],
                          [("tool", "Bash", {"command": "dir"})]]
    base = f"/api/projects/{agent.pid}/agent"
    fut = send(agent, acc, "list")
    ask = wait_ask(agent)
    assert ask["allow_session"] is True and not ask["risky"]
    answer(agent, ask, "session")
    events(fut)
    assert agent.get(base).json()["session_allow"] == ["Bash"]
    fut = send(agent, acc, "list then clean")
    ask = wait_ask(agent)                                   # "dir" went through, "rm -rf" still asks
    assert ask["detail"] == "rm -rf old" and ask["risky"] == "deletes files" and ask["allow_session"] is False
    answer(agent, ask, "session")                            # a session answer only allows it once
    ev = events(fut)
    assert next(e for e in ev if e["t"] == "asked")["answer"] == "once"
    fut = send(agent, acc, "clean again")
    ask = wait_ask(agent)                                    # asked again: every time
    assert ask["risky"] == "deletes files"
    answer(agent, ask, "deny")
    events(fut)
    assert agent.delete(base + "/allow/Bash").json() == {"session_allow": []}
    fut = send(agent, acc, "list again")
    assert wait_ask(agent)["detail"] == "dir"                # revoked: asks again
    answer(agent, wait_ask(agent), "deny")
    events(fut)


def test_session_allowances_are_gone_after_a_restart(agent, tmp_path):
    acc = claude_account(agent)
    agent.script.turns = [[("tool", "Bash", {"command": "dir"})]]
    fut = send(agent, acc, "list")
    answer(agent, wait_ask(agent), "session")
    events(fut)
    assert agent.get(f"/api/projects/{agent.pid}/agent").json()["session_allow"] == ["Bash"]
    stored = [p for p in Path(agent.data_dir).rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")]
    assert not any('"session_allow"' in p.read_text(encoding="utf-8", errors="ignore") for p in stored)   # memory only
    again = create_app(Settings(data_dir=agent.data_dir, claude_cmd=str(tmp_path / "bin" / "claude.cmd"),
                                codex_cmd=str(tmp_path / "bin" / "codex.cmd")), token="r" * 32, login_console=False)
    with TestClient(again, base_url="http://127.0.0.1") as c:
        c.headers[TOKEN_HEADER] = "r" * 32
        assert c.get(f"/api/projects/{agent.pid}/agent").json()["session_allow"] == []


def test_outside_mcp_servers_are_checked_and_reach_the_claude_agent(agent, tmp_path, monkeypatch):
    with pytest.raises(mcp_servers.McpError, match="own server name"):
        mcp_servers.check({"davinci": {"command": "x"}})
    with pytest.raises(mcp_servers.McpError, match="needs a command"):
        mcp_servers.check({"desk": {"type": "stdio"}})
    with pytest.raises(mcp_servers.McpError, match="http"):
        mcp_servers.check({"web": {"type": "http", "url": "ftp://x"}})
    with pytest.raises(mcp_servers.McpError, match="lower-case"):
        mcp_servers.check({"Bad Name": {"command": "x"}})
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    servers = {"desktop": {"command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander@latest"]}}
    assert agent.put("/api/mcp-servers", json={"servers": servers}).json()["servers"]["desktop"]["type"] == "stdio"
    assert agent.put("/api/mcp-servers", json={"servers": {"studio": {"command": "x"}}}).status_code == 400
    acc = claude_account(agent)
    events(send(agent, acc, "hello"))
    seen = agent.script.seen[-1]
    assert "desktop" in seen["mcp"] and seen["mcp_config"]["desktop"]["command"] == "npx"
    assert "Outside MCP servers the owner connected: desktop" in seen["system"]["append"]
    assert "never write Resolve scripts" in seen["system"]["append"]      # the Resolve rule stays (no Resolve MCP)
    agent.put("/api/mcp-servers", json={"servers": {}})
