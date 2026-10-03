"""Current usage without sending a message (fix for: usage page empty until first reply).

Claude: `claude -p /usage` is answered locally - no model turn, no cost.
Codex: every saved session file records exact rate limits; we read the newest one."""
import json
import time

from app.providers.claude_cli import parse_reset, parse_usage_text
from app.providers.codex_cli import read_rate_limits

from .conftest import calls, chat, signed_in


def test_parse_claude_usage_text():
    now = time.mktime((2026, 10, 3, 22, 0, 0, 0, 0, -1))
    w = parse_usage_text("Current session: 34% used · resets Oct 3, 11:50pm (Asia/Kolkata)\n"
                         "Current week (all models): 18% used · resets Oct 5, 7:30pm (Asia/Kolkata)", now)
    assert w["five_hour"]["utilization"] == 0.34 and w["seven_day"]["utilization"] == 0.18
    assert w["five_hour"]["resets_at"] == time.mktime((2026, 10, 3, 23, 50, 0, 0, 0, -1))
    assert parse_usage_text("nothing useful") == {}


def test_reset_dates_roll_into_next_year():
    dec31 = time.mktime((2026, 12, 31, 22, 0, 0, 0, 0, -1))
    assert time.localtime(parse_reset("Jan 2, 7:30pm", dec31)).tm_year == 2027


def test_refresh_reads_claude_usage_without_a_model_call(client, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    before = calls(client)
    accs = {a["id"]: a for a in client.post("/api/usage/refresh").json()}
    assert accs[claude["id"]]["usage_pct"] == 34.0 and accs[claude["id"]]["usage_kind"] == "exact"
    assert accs[claude["id"]]["rate_limit"]["windows"]["seven_day"]["utilization"] == 0.18
    assert calls(client) == before, "reading usage must not send a message"


def test_usage_page_has_numbers_before_any_message(client, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    client.post("/api/usage/refresh")
    card = next(c for c in client.get("/api/usage").json()["cards"] if c["id"] == claude["id"])
    assert card["requests"] == 0 and card["usage_pct"] == 34.0


def test_codex_is_run_without_ephemeral_and_its_limits_are_read(client, project, monkeypatch):
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_CODEX_PCT", "62")
    chat(client, project, "hi", codex["id"])
    args = json.loads((client.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()[-1])["args"]
    assert "--ephemeral" not in args
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == codex["id"])
    assert a["usage_pct"] == 62.0 and a["usage_kind"] == "exact"
    # and a refresh re-reads the same file for free
    accs = {x["id"]: x for x in client.post("/api/usage/refresh").json()}
    assert accs[codex["id"]]["usage_pct"] == 62.0


def test_codex_crossing_threshold_offers_switch(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_CODEX_PCT", "91")
    ev = chat(client, project, "hi", codex["id"])
    offer = next(e for e in ev if e["t"] == "switch_offer")
    assert offer["reason"] == "threshold" and offer["usage_pct"] == 91.0
    assert [e["id"] for e in offer["eligible"] if e["ok"]] == [claude["id"]]


def test_expired_window_counts_as_reset(client, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    from app.accounts import Accounts
    Accounts(client.data_dir).record_rate_limit(
        claude["id"], {"five_hour": {"utilization": 0.95, "resets_at": time.time() - 60}}, "allowed")
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == claude["id"])
    assert a["usage_pct"] == 0.0


def test_safety_stop_if_usage_command_ever_costs_a_turn(client, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "usage_spends")
    a = {x["id"]: x for x in client.post("/api/usage/refresh").json()}[claude["id"]]
    assert "model turn" in a["live_usage_off"]
    monkeypatch.delenv("FAKE_MODE")
    log = client.tmp / "prompts.jsonl"
    n = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    client.post("/api/usage/refresh")                   # must not call /usage again
    a = {x["id"]: x for x in client.get("/api/accounts").json()}[claude["id"]]
    assert a["live_usage_off"] and a["usage_pct"] is None


def test_codex_file_reader_handles_missing_and_garbage(tmp_path):
    assert read_rate_limits(tmp_path) is None
    d = tmp_path / "sessions" / "x"
    d.mkdir(parents=True)
    (d / "rollout-bad.jsonl").write_text('not json\n{"rate_limits": "nope"}\n', encoding="utf-8")
    assert read_rate_limits(tmp_path) is None
