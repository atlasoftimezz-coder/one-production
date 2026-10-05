"""Codex logins as a real Codex agent through `codex app-server`, with the same policy and
approval cards as Claude (plan P5: "same task on Codex behaves the same")."""
import json
import time

from app.agent.codex_agent import command_as_tools

from .conftest import answer, codex_account, events, send, wait_ask

PS = r'"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -Command '


def sent(c, method):
    return [m for run in c.codex["log"] for m in run["msgs"] if m.get("method") == method]


def test_codex_reads_through_powershell_count_as_reads():
    read = [{"type": "read", "command": "Get-Content a.md", "name": "a.md", "path": "a.md"}]
    assert command_as_tools(PS + '"Get-Content a.md"', read) == [("Read", {"file_path": "a.md"})]
    mixed = read + [{"type": "unknown", "command": "Remove-Item a.md"}]
    assert command_as_tools("x", mixed) == [("Bash", {"command": "x"})]
    assert command_as_tools("dir", None) == [("Bash", {"command": "dir"})]


def test_codex_runs_in_the_project_with_studio_tools_and_isolated_home(agent):
    acc = codex_account(agent)
    agent.codex["turns"] = [[("text", "Hello from Codex."), ("usage", 1200, 80)]]
    ev = events(send(agent, acc, "hi"))
    run = agent.codex["log"][-1]
    assert run["argv"][-1] == "app-server" and run["env"]["CODEX_HOME"].endswith(acc["id"])
    assert run["cwd"] == str(agent.files)
    start = sent(agent, "thread/start")[-1]["params"]
    assert start["sandbox"] == "workspace-write" and start["approvalPolicy"] == "untrusted"
    assert {t["name"] for t in start["dynamicTools"]} == {"list_voices", "script_stats", "make_voiceover", "voiceover_status", "knowledge_sources", "knowledge_search", "knowledge_read", "learning_recall", "remember_direction", "propose_lesson", "resolve_status", "resolve_propose_plan", "quality_gate", "render_edit", "edit_status", "build_animation", "package_episode"}
    assert "ep1.md" in start["developerInstructions"] and "apply_patch" in start["developerInstructions"]
    assert "".join(e.get("v", "") for e in ev if e["t"] == "text") == "Hello from Codex."
    saved = agent.get(f"/api/projects/{agent.pid}/chat").json()[-1]
    assert saved["usage"]["in"] == 1200 and saved["usage"]["model"] == "gpt-fake"


def test_codex_resumes_its_thread(agent):
    acc = codex_account(agent)
    events(send(agent, acc, "one"))
    events(send(agent, acc, "two"))
    assert sent(agent, "thread/resume")[-1]["params"]["threadId"] == "th-1"
    assert sent(agent, "turn/start")[-1]["params"]["input"][0]["text"] == "two"


def test_codex_reads_inside_freely_commands_ask(agent):
    acc = codex_account(agent)
    read = [{"type": "read", "command": "Get-Content ep1.md", "name": "ep1.md", "path": "ep1.md"}]
    agent.codex["turns"] = [[("cmd", PS + '"Get-Content ep1.md"', read), ("cmd", PS + "dir", [{"type": "unknown", "command": "dir"}])]]
    fut = send(agent, acc, "read it then list")
    ask = wait_ask(agent)
    assert ask["title"] == "Run command" and ask["detail"] == "dir"            # wrapper hidden on the card
    answer(agent, ask, "deny")
    ev = events(fut)
    assert agent.codex["decisions"] == ["accept", "decline"]                  # the read never asked
    steps = [e for e in ev if e["t"] == "step"]
    assert steps[0]["title"] == "Read" and steps[0]["detail"] == "ep1.md"


def test_codex_file_change_outside_the_project_asks(agent, tmp_path):
    acc = codex_account(agent)
    agent.codex["turns"] = [[("patch", "inside.md", True), ("patch", str(tmp_path / "out.md"), False)]]
    fut = send(agent, acc, "write two files")
    ask = wait_ask(agent)
    assert "outside the project" in ask["reason"]
    answer(agent, ask, "deny")
    events(fut)
    assert agent.codex["decisions"] == ["decline"]                            # inside one never asked


def test_codex_calls_studio_tools_through_the_gpu_gate(agent):
    acc = codex_account(agent)
    agent.codex["turns"] = [[("tool", "make_voiceover", {"file": "ep1.md", "voice": "buster_ref48.wav",
                                                          "pace_mode": "wpm", "target_wpm": 170})]]
    fut = send(agent, acc, "voice it faster")
    ask = wait_ask(agent)
    assert ask["gate"] and "170 wpm" in ask["detail"]
    answer(agent, ask, "once")
    ev = events(fut)
    assert any(e["t"] == "voicejob" for e in ev)
    res = agent.codex["tool_results"][-1]
    assert res["success"] and json.loads(res["contentItems"][0]["text"])["status"] == "started"
    assert agent.fake.specs[-1]["target_wpm"] == 170


def test_codex_rate_limits_and_usage_limit(agent):
    acc = codex_account(agent)
    agent.codex["turns"] = [[("limits", 47), ("text", "ok")],
                            [("text", "partial"), ("fail", "You've hit your usage limit.", "usageLimitExceeded")]]
    events(send(agent, acc, "one"))
    card = next(c for c in agent.get("/api/usage").json()["cards"] if c["id"] == acc["id"])
    assert card["usage_pct"] == 47.0 and card["usage_kind"] == "exact"
    ev = events(send(agent, acc, "two"))
    err = next(e for e in ev if e["t"] == "error")
    assert err["kind"] == "limit" and any(e["t"] == "switch_offer" for e in ev)


def test_codex_stop_interrupts_the_turn(agent):
    acc = codex_account(agent)
    agent.codex["turns"] = [[("text", "Thinking hard"), ("sleep", 30)]]
    fut = send(agent, acc, "long")
    end = time.time() + 10
    while not agent.get(f"/api/projects/{agent.pid}/agent").json()["running"] and time.time() < end:
        time.sleep(0.05)
    time.sleep(0.3)
    agent.post(f"/api/projects/{agent.pid}/agent/stop")
    ev = events(fut)
    assert any(e["t"] == "error" and "Stopped" in e["message"] for e in ev)
    assert agent.get(f"/api/projects/{agent.pid}/chat").json()[-1]["text"] == "Thinking hard"
