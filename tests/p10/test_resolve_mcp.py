"""DaVinci Resolve's own MCP for the studio's Claude agent (owner, 2026-10-06: "I cant access davinci
inside one production, I have turned on davinci claude mcp and configured").

Root cause: the studio agent is isolated (own config folder, strict MCP config with only the studio
server), so an MCP added to the owner's own Claude Code never reaches it, and its instructions
forbade running Resolve. Now the owner switches Resolve's MCP on in the Resolve panel: reads run
freely, every change shows an approval card with the code, full-PC scripts are never allowed."""
from app import resolve_mcp
from app.agent import policy
from app.config import Settings
from app.resolve_runner import same_setting
from tests.p5.conftest import answer, claude_account, events, send, wait_ask

SCRIPT = "project = resolve.GetProjectManager().CreateProject('Test')\nprint(project.GetName())"


def fake_exe(tmp_path) -> str:
    p = tmp_path / "ResolveMCP.exe"
    p.write_bytes(b"MZ stand-in")
    return str(p)


def test_nothing_reaches_the_agent_until_the_owner_switches_it_on(tmp_path):
    exe = fake_exe(tmp_path)
    off = Settings(data_dir=tmp_path, resolve_mcp_cmd=exe)
    assert resolve_mcp.for_agent(off) == {"servers": {}, "disallowed": [], "instructions": ""}
    missing = Settings(data_dir=tmp_path, resolve_mcp=True, resolve_mcp_cmd=str(tmp_path / "nope.exe"))
    assert resolve_mcp.for_agent(missing)["servers"] == {}
    on = resolve_mcp.for_agent(Settings(data_dir=tmp_path, resolve_mcp=True, resolve_mcp_cmd=exe))
    assert on["servers"] == {"davinci": {"type": "stdio", "command": exe, "args": []}}
    assert on["disallowed"] == ["mcp__davinci__run_script_unsafe"]
    assert "work in Resolve directly" in on["instructions"] and "run_script" in on["instructions"]


def test_reads_are_free_changes_ask_full_pc_scripts_never(tmp_path):
    d = lambda tool, **inp: policy.decide(tool, inp, tmp_path)[0]
    for t in resolve_mcp.READ_ONLY:
        assert d(f"mcp__davinci__{t}") == "allow", t
    for t in ("run_script", "launch_resolve", "update_dctl", "generate_lut", "delete_dctl", "delete_lut"):
        assert d(f"mcp__davinci__{t}") == "ask", t
    assert d("mcp__davinci__run_script_unsafe", script="x") == "deny"
    title, detail = policy.describe("mcp__davinci__run_script", {"script": SCRIPT}, tmp_path)
    assert title == "Run a script in DaVinci Resolve" and detail == SCRIPT      # the owner reads all of it


def test_agent_gets_resolve_only_when_on_and_every_script_asks(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    acc = claude_account(agent)
    events(send(agent, acc, "hello"))
    seen = agent.script.seen[-1]
    assert seen["mcp"] == ["studio"] and seen["disallowed"] == []
    assert "never write Resolve scripts" in seen["system"]["append"]
    assert "work in Resolve directly" not in seen["system"]["append"]

    exe = fake_exe(tmp_path)
    agent.app.state.resolve.settings.resolve_mcp_cmd = exe
    r = agent.patch("/api/resolve", json={"mcp": True}).json()
    assert r["mcp"] is True and r["mcp_exe"] == exe and r["mcp_ready"] is True
    assert '"resolve_mcp": true' in (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert agent.get("/api/resolve").json()["mcp"] is True

    agent.script.turns = [[("tool", "mcp__davinci__get_resolve_status", {}),
                           ("tool", "mcp__davinci__run_script", {"script": SCRIPT}),
                           ("tool", "mcp__davinci__run_script_unsafe", {"script": "import os"}),
                           ("text", "done")]]
    fut = send(agent, acc, "make a Test project in Resolve")
    ask = wait_ask(agent)                                   # status went through without a card
    assert ask["title"] == "Run a script in DaVinci Resolve" and ask["detail"] == SCRIPT
    answer(agent, ask, "once")
    ev = events(fut)
    assert sum(e["t"] == "ask" for e in ev) == 1            # the unsafe one never even asked
    seen = agent.script.seen[-1]
    assert seen["resume"] is None              # fresh session: a resumed one kept its plan-only habit
    assert seen["mcp"][0] == "davinci" and "studio" in seen["mcp"]
    assert seen["mcp_config"]["davinci"] == {"type": "stdio", "command": exe, "args": []}
    assert seen["disallowed"] == ["mcp__davinci__run_script_unsafe"]
    # the plan-only rule is swapped out, not contradicted (a real agent kept the old rule otherwise)
    assert "work in Resolve directly" in seen["system"]["append"]
    assert "never write Resolve scripts" not in seen["system"]["append"]
    results = agent.script.results[-3:]
    assert not results[0]["error"] and not results[1]["error"]
    assert results[2]["error"] and "full access" in results[2]["text"]

    assert agent.patch("/api/resolve", json={"mcp": False}).json()["mcp"] is False
    events(send(agent, acc, "and now?"))
    assert agent.script.seen[-1]["mcp"] == ["studio"]
    assert agent.script.seen[-1]["resume"] is not None          # back to the session it had without Resolve


def test_frame_rate_reads_back_as_a_number():
    """Resolve 21.1 reads timelineFrameRate "30" back as 30.0 - three of the owner's plans failed on it."""
    assert same_setting(30.0, "30") and same_setting("30.0", "30") and same_setting("23.976", "23.976")
    assert not same_setting(25.0, "30") and same_setting("1920", "1920") and not same_setting(None, "30")
