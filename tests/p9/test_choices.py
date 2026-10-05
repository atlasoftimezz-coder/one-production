"""Model + effort toggles reach every place a model is called: plain chat (Claude Code CLI,
Codex CLI, Anthropic API) and agent mode (Agent SDK, Codex app-server)."""
import json

import pytest

from app import models as M
from tests.p2.conftest import API_BODIES, GOOD_KEY, chat, signed_in
from tests.p5.conftest import claude_account, events, send


def test_choice_table():
    c = M.choices("claude")
    assert [m["id"] for m in c["models"]] == ["opus", "sonnet", "haiku", "fable"]
    assert next(m for m in c["models"] if m["id"] == "haiku")["efforts"] == []
    assert M.API_IDS == {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
                         "haiku": "claude-haiku-4-5-20251001", "fable": "claude-fable-5-1"}
    assert M.effort_for("claude", "haiku", "high") is None           # Haiku 4.5 rejects effort
    assert M.effort_for("anthropic_api", "opus", "xhigh") == "xhigh"
    assert M.effort_for("claude", "opus", None) is None
    with pytest.raises(ValueError):
        M.check("claude", "gpt-5", None)
    with pytest.raises(ValueError):
        M.check("claude", "opus", "ultra")
    with pytest.raises(ValueError):
        M.check("codex", "gpt; rm -rf", None)                          # never reaches a command line
    cx = M.codex_models([{"id": "a", "displayName": "A", "isDefault": True,
                          "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "ultra"}]},
                         {"id": "h", "hidden": True}])
    assert [m["id"] for m in cx] == ["a"]
    assert M.effort_for("codex", "a", "medium", cx) is None and M.effort_for("codex", "a", "ultra", cx) == "ultra"
    cc = M.choices("codex", cx)
    assert cc["models"][0]["id"] is None and cc["models"][0]["label"] == "Codex default (A)"
    assert cc["models"][0]["efforts"] == ["low", "ultra"]


def test_pickers_api_for_claude(client):
    acc = signed_in(client, "claude")
    r = client.get(f"/api/accounts/{acc['id']}/models").json()
    assert [m["id"] for m in r["models"]] == ["opus", "sonnet", "haiku", "fable"]
    assert r["model"] == "sonnet" and r["effort"] is None
    a = client.patch(f"/api/accounts/{acc['id']}", json={"model": "fable", "effort": "xhigh"}).json()
    assert a["model"] == "fable" and a["effort"] == "xhigh"
    assert client.patch(f"/api/accounts/{acc['id']}", json={"effort": "auto"}).json()["effort"] is None
    assert client.patch(f"/api/accounts/{acc['id']}", json={"effort": "ultra"}).status_code == 400
    assert client.patch(f"/api/accounts/{acc['id']}", json={"model": "gpt-5"}).status_code == 400
    assert client.get("/api/accounts/demo/models").json()["models"] == []


def test_claude_cli_chat_gets_model_and_effort(client, monkeypatch):
    monkeypatch.setenv("FAKE_ARGS", str(client.tmp / "args.jsonl"))
    acc = signed_in(client, "claude")
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "opus", "effort": "max"})
    chat(client, pid, "hi", acc["id"])
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "haiku", "effort": "high"})
    chat(client, pid, "hi again", acc["id"])
    turns = [a for a in map(json.loads, (client.tmp / "args.jsonl").read_text().splitlines())
             if "--output-format" in a and "stream-json" in a]
    first, second = turns[-2], turns[-1]
    assert first[first.index("--model") + 1] == "opus" and first[first.index("--effort") + 1] == "max"
    assert second[second.index("--model") + 1] == "haiku" and "--effort" not in second


def test_codex_cli_chat_gets_effort(client, monkeypatch):
    monkeypatch.setenv("FAKE_ARGS", str(client.tmp / "args.jsonl"))
    acc = signed_in(client, "codex")
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "gpt-fast", "effort": "ultra"})
    chat(client, pid, "hi", acc["id"])
    turn = [a for a in map(json.loads, (client.tmp / "args.jsonl").read_text().splitlines()) if a[:1] == ["exec"]][-1]
    assert turn[turn.index("--model") + 1] == "gpt-fast"
    assert 'model_reasoning_effort="ultra"' in turn


def test_api_key_chat_sends_output_config_effort(client):
    acc = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "opus", "effort": "high"})
    API_BODIES.clear()
    chat(client, pid, "hi", acc["id"])
    assert API_BODIES[-1]["model"] == "claude-opus-5-5"
    assert API_BODIES[-1]["output_config"] == {"effort": "high"}
    assert "thinking" not in API_BODIES[-1]                    # adaptive by default on current models
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "haiku"})
    chat(client, pid, "hi", acc["id"])
    assert API_BODIES[-1]["model"] == "claude-haiku-4-5-20251001" and "output_config" not in API_BODIES[-1]
    client.patch(f"/api/accounts/{acc['id']}", json={"model": "sonnet", "effort": "auto"})
    chat(client, pid, "hi", acc["id"])
    assert "output_config" not in API_BODIES[-1]


def test_agent_sdk_gets_model_and_effort(agent):
    acc = claude_account(agent)
    agent.patch(f"/api/accounts/{acc['id']}", json={"model": "opus", "effort": "xhigh"})
    events(send(agent, acc, "hello"))
    seen = agent.script.seen[-1]
    assert seen["model"] == "opus" and seen["effort"] == "xhigh"
    agent.patch(f"/api/accounts/{acc['id']}", json={"model": "haiku"})
    events(send(agent, acc, "again"))
    assert agent.script.seen[-1]["model"] == "haiku" and agent.script.seen[-1]["effort"] is None


def test_codex_agent_lists_models_and_sends_effort(agent):
    acc = signed_in(agent, "codex")
    r = agent.get(f"/api/accounts/{acc['id']}/models").json()
    assert [m["id"] for m in r["models"]] == [None, "gpt-fast"]          # hidden model left out
    assert r["models"][1]["efforts"] == ["low", "ultra"]
    assert agent.patch(f"/api/accounts/{acc['id']}", json={"model": "gpt-fast", "effort": "ultra"}).status_code == 200
    events(send(agent, acc, "hello"))
    sent = [m for log in agent.codex["log"] for m in log["msgs"] if m.get("method") == "turn/start"]
    assert sent[-1]["params"]["model"] == "gpt-fast" and sent[-1]["params"]["effort"] == "ultra"
    # an effort the chosen model doesn't offer is not sent
    agent.patch(f"/api/accounts/{acc['id']}", json={"effort": "medium"})
    events(send(agent, acc, "again"))
    sent = [m for log in agent.codex["log"] for m in log["msgs"] if m.get("method") == "turn/start"]
    assert "effort" not in sent[-1]["params"]
