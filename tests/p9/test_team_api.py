"""Team mode through the real app: routes, settings, the Agent SDK stand-in as master and worker,
ledger roles, spend caps on the usage page, chat history, Stop."""
import json
import time

from tests.p2.conftest import signed_in
from tests.p5.conftest import answer, claude_account

PLAN = json.dumps({"tasks": [{"title": "Notes", "brief": "Write notes.md with three ideas", "files": ["notes.md"]}]})


def start(c, goal="Plan episode 2", **config):
    body = {"goal": goal}
    if config:
        body["config"] = config
    return c.pool.submit(lambda: c.post(f"/api/projects/{c.pid}/team", json=body))


def lines(fut, timeout=30):
    r = fut.result(timeout)
    assert r.status_code == 200, r.text
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_team_settings_defaults_and_validation(agent):
    s = agent.get("/api/team/settings").json()
    assert s["settings"]["master_model"] == "opus" and s["settings"]["master_effort"] == "high"
    assert s["settings"]["worker_model"] == "sonnet" and s["settings"]["worker_effort"] == "medium"
    assert s["settings"]["max_workers"] == 3 and s["settings"]["review_rounds"] == 2
    assert s["settings"]["review_in"] == 40_000 and s["settings"]["review_out"] == 8_000
    assert s["settings"]["stage_budget"] == 120_000
    r = agent.patch("/api/team/settings", json={"worker_model": "haiku", "worker_effort": "auto", "max_workers": 2})
    assert r.status_code == 200
    assert r.json()["settings"]["worker_model"] == "haiku" and r.json()["settings"]["worker_effort"] is None
    assert agent.patch("/api/team/settings", json={"master_effort": "ultra"}).status_code == 400
    assert agent.patch("/api/team/settings", json={"max_workers": 5}).status_code == 400
    assert agent.patch("/api/team/settings", json={"master_account": "nope"}).status_code == 404


def test_team_needs_an_account(agent):
    r = agent.post(f"/api/projects/{agent.pid}/team", json={"goal": "x"})
    assert r.status_code == 400 and "needs at least one" in r.json()["detail"]


def test_team_run_end_to_end(agent):
    acc = claude_account(agent)
    agent.script.turns = [
        [("text", PLAN)],                                                         # master plans
        [("tool", "Write", {"file_path": "notes.md", "content": "1. debt\n2. rent\n3. food\n"}),
         ("text", "Wrote notes.md")],                                             # worker
        [("tool", "Write", {"file_path": "notes.md", "content": "master edit"}),
         ("text", '{"verdict": "pass", "notes": ""}')],                            # master reviews (tries to edit)
        [("text", "Final review: notes.md has three good ideas.")],               # master's final word
    ]
    ev = lines(start(agent))
    assert ev[-1]["t"] == "done"
    final = [e for e in ev if e["t"] == "team"][-1]["team"]
    assert final["state"] == "done" and final["tasks"][0]["status"] == "done"
    assert final["tasks"][0]["pins"] == [acc["id"]] and final["master"] == acc["id"]
    assert (agent.files / "notes.md").read_text(encoding="utf-8").startswith("1. debt")   # master's edit refused
    refused = [r for r in agent.script.results if r["tool"] == "Write" and r["error"]]
    assert refused and "reviews only" in refused[0]["text"]
    seen = agent.script.seen
    assert (seen[0]["model"], seen[0]["effort"]) == ("opus", "high")             # plan
    assert (seen[1]["model"], seen[1]["effort"]) == ("sonnet", "medium")         # worker
    assert (seen[2]["model"], seen[2]["effort"]) == ("opus", "high")             # review
    assert "WORKER" in seen[1]["system"]["append"] and "MASTER" in seen[0]["system"]["append"]
    assert all(s["setting_sources"] == [] for s in seen)                          # still isolated
    worker_events = [e for e in ev if e.get("w") == "t1"]
    assert any(e["t"] == "step" for e in worker_events)                          # steps tagged by task
    hist = agent.get(f"/api/projects/{agent.pid}/chat").json()
    assert hist[-2]["role"] == "user" and hist[-2]["text"] == "Plan episode 2" and hist[-2]["team"] is True
    assert hist[-1]["text"].startswith("Final review") and hist[-1]["team"]["tasks"][0]["status"] == "done"
    roles = {r["role"]: r["requests"] for r in agent.get("/api/usage?range=today").json()["by_role"]}
    assert roles == {"master": 3, "worker": 1}
    st = agent.get(f"/api/projects/{agent.pid}/team").json()
    assert st["running"] is False and st["team"]["state"] == "done"


def test_worker_question_card_is_answered_like_any_other(agent):
    claude_account(agent)
    agent.script.turns = [
        [("text", PLAN)],
        [("tool", "Bash", {"command": "dir"}), ("text", "listed")],
        [("text", '{"verdict": "pass"}')],
        [("text", "Final review.")],
    ]
    fut = start(agent)
    end = time.time() + 10
    ask = None
    while time.time() < end and not ask:
        asks = agent.get(f"/api/projects/{agent.pid}/agent").json()["asks"]
        ask = asks[0] if asks else None
        time.sleep(0.05)
    assert ask and ask["title"] == "Run command"
    answer(agent, ask, "once")
    ev = lines(fut)
    assert any(e["t"] == "ask" and e.get("w") == "t1" for e in ev)
    assert [e for e in ev if e["t"] == "team"][-1]["team"]["tasks"][0]["status"] == "done"


def test_stop_and_chat_blocked_while_team_works(agent):
    acc = claude_account(agent)
    agent.script.turns = [[("text", PLAN)], [("sleep", 30)]]
    fut = start(agent)
    end = time.time() + 10
    while time.time() < end:
        st = agent.get(f"/api/projects/{agent.pid}/team").json()
        if st["running"] and st["team"]["tasks"] and st["team"]["tasks"][0]["status"] == "running":
            break
        time.sleep(0.05)
    r = agent.post(f"/api/projects/{agent.pid}/chat", json={"message": "hi", "account_id": acc["id"]})
    assert r.status_code == 409                                    # one run per project
    assert agent.post(f"/api/projects/{agent.pid}/team/resume").json() == {"ok": True}
    assert agent.post(f"/api/projects/{agent.pid}/agent/stop").json() == {"stopped": True}
    ev = lines(fut)
    final = [e for e in ev if e["t"] == "team"][-1]["team"]
    assert final["state"] == "stopped" and final["tasks"][0]["status"] == "stopped" and final["locks"] == {}
    assert agent.get(f"/api/projects/{agent.pid}/chat").json()[-1]["text"] == "Team stopped."


def test_spend_caps_on_usage_page_and_they_block_the_team(agent):
    acc = claude_account(agent)
    r = agent.patch(f"/api/accounts/{acc['id']}", json={"caps": {"cap_day_tokens": 100, "cap_task_tokens": 5000}})
    assert r.status_code == 200
    assert agent.patch(f"/api/accounts/{acc['id']}", json={"caps": {"cap_day_usd": -1}}).status_code == 400
    card = next(c for c in agent.get("/api/usage?range=today").json()["cards"] if c["id"] == acc["id"])
    assert card["caps"] == {"cap_task_tokens": 5000, "cap_day_tokens": 100, "cap_day_usd": None}
    assert card["today"]["tokens"] == 0
    agent.script.turns = [[("text", PLAN)]]                        # the plan alone uses 130 tokens
    fut = start(agent, poll=0.1)
    end = time.time() + 10
    st = {}
    while time.time() < end:
        st = agent.get(f"/api/projects/{agent.pid}/team").json()
        if st["team"] and st["team"]["state"] == "paused":
            break
        time.sleep(0.05)
    assert st["team"]["state"] == "paused" and "daily cap of 100 tokens reached" in st["team"]["paused_why"]
    agent.patch(f"/api/accounts/{acc['id']}", json={"caps": {"cap_day_tokens": None}})     # user lifts the cap
    agent.script.turns = [[("text", "done")], [("text", '{"verdict": "pass"}')], [("text", "Final.")]]
    agent.post(f"/api/projects/{agent.pid}/team/resume")
    final = [e for e in lines(fut) if e["t"] == "team"][-1]["team"]
    assert final["state"] == "done"


def test_codex_can_be_a_worker_with_its_own_choice(agent):
    """With the Claude login at its limit, the work - and the master - move to Codex (another service)."""
    cl = claude_account(agent)
    cx = signed_in(agent, "codex")
    agent.patch(f"/api/accounts/{cx['id']}", json={"model": "gpt-fast"})
    agent.script.turns = [
        [("text", PLAN)],
        [("text", "half way"), ("fail", 429, "usage limit reached")],        # Claude worker hits its limit
    ]
    agent.codex["turns"] = [[("text", "finished the notes")],                 # Codex worker carries on
                            [("text", '{"verdict": "pass"}')],                 # Codex reviews (Claude is limited)
                            [("text", "Final.")]]
    final = [e for e in lines(start(agent)) if e["t"] == "team"][-1]["team"]
    t = final["tasks"][0]
    assert t["status"] == "done" and t["pins"] == [cl["id"], cx["id"]] and final["master"] == cx["id"]
    assert t["handoffs"] == ["team-t1-1.md"]
    assert agent.get(f"/api/projects/{agent.pid}/handoffs/team-t1-1.md").text.count("half way") == 1
    sent = [m for log in agent.codex["log"] for m in log["msgs"] if m.get("method") == "turn/start"]
    assert sent[0]["params"]["model"] == "gpt-fast"
    assert "hit its usage limit" in sent[0]["params"]["input"][0]["text"]
    assert "half way" in sent[0]["params"]["input"][0]["text"]
