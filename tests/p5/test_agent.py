"""Agent mode: the chat works like Claude Code in the project folder (plan P5).

User, 2026-10-04: "it should work like claude code not claude chat". Decided: free inside the
project, ask outside; API keys are agents too.
"""
import json
import time
from pathlib import Path

from app.agent import policy

from tests.p2.conftest import GOOD_KEY as API_KEY

from .conftest import answer, claude_account, events, send, wait_ask


# ---------------------------------------------------------------- the policy itself
def test_policy_free_inside_ask_outside(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    d = lambda tool, **inp: policy.decide(tool, inp, root)[0]
    assert d("Read", file_path=str(root / "a.md")) == "allow"
    assert d("Write", file_path="notes/new.md") == "allow"                 # relative = inside
    assert d("Edit", file_path=str(tmp_path / "other.md")) == "ask"
    assert d("Write", file_path=str(root / ".." / "escape.md")) == "ask"    # .. can't sneak out
    assert d("Grep", pattern="x") == "allow"                                # no path = project
    assert d("Bash", command="dir") == "ask"
    assert d("PowerShell", command="Get-ChildItem") == "ask"
    assert d("WebFetch", url="https://x.test") == "ask"
    assert d("mcp__studio__list_voices") == "allow"
    assert d("mcp__studio__make_voiceover", file="a.md", voice="v.wav") == "gate"
    assert d("SomethingNew") == "ask"


# ---------------------------------------------------------------- through the app
def test_agent_streams_and_runs_in_the_project_folder_isolated(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("text", "Reading "), ("tool", "Read", {"file_path": str(agent.files / "ep1.md")}),
                           ("text", "it now.")]]
    ev = events(send(agent, acc, "What's in my script?"))
    assert [e["t"] for e in ev if e["t"] in ("text", "step", "step_done")] == \
        ["text", "step", "step_done", "text"]
    assert next(e for e in ev if e["t"] == "step")["title"] == "Read"
    seen = agent.script.seen[-1]
    assert Path(seen["cwd"]) == agent.files                                 # works in the project folder
    assert seen["setting_sources"] == [] and seen["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert seen["env"]["CLAUDE_CONFIG_DIR"].endswith(acc["id"])             # this account's own login
    assert seen["cli"].endswith("claude.exe") and seen["mcp"] == ["studio"]
    assert "make_voiceover" in seen["system"]["append"] and "ep1.md" in seen["system"]["append"]
    saved = agent.get(f"/api/projects/{agent.pid}/chat").json()[-1]
    assert saved["agent"] and saved["text"] == "Reading it now." and saved["steps"][0]["title"] == "Read"
    assert agent.get("/api/usage?range=today").json()["totals"]["requests"] == 1


def test_session_is_resumed_on_the_next_message(agent):
    acc = claude_account(agent)
    events(send(agent, acc, "first"))
    events(send(agent, acc, "second"))
    first, second = agent.script.seen[-2:]
    assert first["resume"] is None and "first" in first["prompt"]
    assert second["resume"] == "sess-1" and second["prompt"] == "second"     # memory lives in the session


def test_editing_inside_the_project_needs_no_approval(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("tool", "Write", {"file_path": "hook.md", "content": "A better hook."})]]
    ev = events(send(agent, acc, "write a hook"))
    assert not any(e["t"] == "ask" for e in ev)
    assert (agent.files / "hook.md").read_text(encoding="utf-8") == "A better hook."


def test_writing_outside_the_project_asks_and_deny_stops_it(agent, tmp_path):
    acc = claude_account(agent)
    target = tmp_path / "outside.txt"
    agent.script.turns = [[("tool", "Write", {"file_path": str(target), "content": "x"}), ("text", "ok")]]
    fut = send(agent, acc, "save it to my desktop")
    ask = wait_ask(agent)
    assert ask["title"] == "Write" and "outside the project" in ask["reason"]
    answer(agent, ask, "deny")
    events(fut)
    assert not target.exists()
    assert agent.script.results[-1]["error"] and "said no" in agent.script.results[-1]["text"]


def test_commands_ask_and_allow_for_session_is_remembered_in_memory_only(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("tool", "Bash", {"command": "ffprobe ep1.mp3"})],
                          [("tool", "Bash", {"command": "dir"})]]
    fut = send(agent, acc, "check the audio")
    ask = wait_ask(agent)
    assert ask["title"] == "Run command" and ask["detail"] == "ffprobe ep1.mp3"
    answer(agent, ask, "session")
    events(fut)
    ev = events(send(agent, acc, "list the files"))
    assert not any(e["t"] == "ask" for e in ev)                             # same session: not asked again
    assert agent.get(f"/api/projects/{agent.pid}/agent").json()["session_allow"] == ["Bash"]


def test_agent_starts_a_voiceover_through_the_gpu_gate(agent):
    acc = claude_account(agent)
    args = {"file": "ep1.md", "voice": "buster_ref48.wav", "pace_mode": "wpm", "target_wpm": 167}
    agent.script.turns = [[("tool", "mcp__studio__make_voiceover", args)],
                          [("tool", "mcp__studio__make_voiceover", args)]]
    fut = send(agent, acc, "Add 15 WPM")
    ask = wait_ask(agent)
    assert ask["gate"] and ask["title"] == "Make voiceover" and "167 wpm" in ask["detail"]
    answer(agent, ask, "always")
    ev = events(fut)
    job = next(e for e in ev if e["t"] == "voicejob")
    assert agent.fake.specs[-1]["target_wpm"] == 167 and agent.fake.specs[-1]["pace_mode"] == "wpm"
    req = next(m for m in agent.get(f"/api/projects/{agent.pid}/chat").json() if m.get("job") == job["id"])
    assert req["tool_request"] == "voiceover" and req["by_agent"]
    ev = events(send(agent, acc, "again"))                                 # "always" was remembered
    assert not any(e["t"] == "ask" for e in ev) and any(e["t"] == "voiceover" or e["t"] == "voicejob" for e in ev)


def test_agent_can_follow_a_voiceover_to_the_end(agent):
    acc = claude_account(agent)
    agent.post("/api/approvals/voiceover", json={})                        # harmless if absent
    agent.script.turns = [[("tool", "mcp__studio__make_voiceover", {"file": "ep1.md", "voice": "human_ref48.wav"})]]
    fut = send(agent, acc, "voice it")
    answer(agent, wait_ask(agent), "once")
    job_id = next(e for e in events(fut) if e["t"] == "voicejob")["id"]
    agent.script.turns = [[("tool", "mcp__studio__voiceover_status", {"job_id": job_id, "wait_seconds": 20})]]
    events(send(agent, acc, "how did it go?"))
    result = json.loads(agent.script.results[-1]["text"])
    assert result["status"] == "done" and result["result"]["wpm"] == 157


def test_stop_ends_the_run_and_keeps_what_was_said(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("text", "Working on it"), ("sleep", 30)]]
    fut = send(agent, acc, "long job")
    end = time.time() + 10
    while not agent.get(f"/api/projects/{agent.pid}/agent").json()["running"] and time.time() < end:
        time.sleep(0.05)
    time.sleep(0.2)
    assert agent.post(f"/api/projects/{agent.pid}/agent/stop").json()["stopped"]
    ev = events(fut)
    assert any(e["t"] == "error" and "Stopped" in e["message"] for e in ev)
    saved = agent.get(f"/api/projects/{agent.pid}/chat").json()[-1]
    assert saved["text"] == "Working on it" and saved["error"]["message"] == "Stopped."
    assert not agent.get(f"/api/projects/{agent.pid}/agent").json()["running"]


def test_reattach_replays_a_running_turn_and_busy_project_refuses_a_second(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("text", "Step one. "), ("sleep", 1.0), ("text", "Step two.")]]
    fut = send(agent, acc, "go")
    end = time.time() + 10
    while not agent.get(f"/api/projects/{agent.pid}/agent").json()["running"] and time.time() < end:
        time.sleep(0.05)
    time.sleep(0.2)
    second = agent.post(f"/api/projects/{agent.pid}/chat", json={"message": "and this", "account_id": acc["id"]})
    assert second.status_code == 409
    again = agent.get(f"/api/projects/{agent.pid}/agent/stream")            # like coming back to the project
    text = "".join(json.loads(l).get("v", "") for l in again.text.splitlines() if l.strip())
    assert text == "Step one. Step two."
    events(fut)


def test_limit_hit_is_classified_and_offers_a_switch(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("text", "Half a rep"), ("fail", 429, "Claude AI usage limit reached")]]
    ev = events(send(agent, acc, "keep going"))
    err = next(e for e in ev if e["t"] == "error")
    assert err["kind"] == "limit" and err["interrupted"]
    assert any(e["t"] == "switch_offer" and e["reason"] == "limit" for e in ev)


def test_rate_limits_from_the_agent_update_the_usage_page(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("limit", {"five_hour": {"utilization": 0.61, "resetsAt": time.time() + 3600}}),
                           ("text", "ok")]]
    events(send(agent, acc, "hi"))
    card = next(c for c in agent.get("/api/usage").json()["cards"] if c["id"] == acc["id"])
    assert card["usage_pct"] == 61.0 and card["usage_kind"] == "exact"


def test_api_key_accounts_are_agents_with_their_own_empty_config(agent):
    acc = agent.post("/api/accounts", json={"provider": "anthropic_api", "api_key": API_KEY}).json()
    events(send(agent, acc, "hello"))
    env = agent.script.seen[-1]["env"]
    assert env["ANTHROPIC_API_KEY"] == API_KEY                             # from Credential Manager, not disk
    assert Path(env["CLAUDE_CONFIG_DIR"]).name == f"agent-{acc['id']}"
    assert API_KEY not in (agent.data_dir / "projects").joinpath(agent.pid, "agent-sessions.json").read_text()
