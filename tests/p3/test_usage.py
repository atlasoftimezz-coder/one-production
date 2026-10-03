"""R2: ledger, usage page numbers, thresholds, order, CSV, estimate accuracy."""
import csv
import io
import time

from app.usage import range_start

from .conftest import GOOD_KEY, chat, signed_in


def test_every_reply_is_recorded(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    chat(client, project, "one", claude["id"])
    chat(client, project, "two", claude["id"])
    chat(client, project, "three", codex["id"])
    u = client.get("/api/usage?range=today").json()
    cards = {c["id"]: c for c in u["cards"]}
    assert cards[claude["id"]]["requests"] == 2 and cards[claude["id"]]["out_tok"] == 60
    assert cards[codex["id"]]["requests"] == 1 and cards[codex["id"]]["in_tok"] == 50
    assert u["totals"]["requests"] == 3
    assert {p["project"] for p in u["by_project"]} == {project}
    models = {m["model"] for m in u["by_model"]}
    assert "claude-sonnet-5-5" in models


def test_claude_and_codex_usage_are_exact_after_a_reply(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    chat(client, project, "hi", claude["id"])
    chat(client, project, "hi", codex["id"])
    cards = {c["id"]: c for c in client.get("/api/usage").json()["cards"]}
    assert cards[claude["id"]]["usage_pct"] == 42.0 and cards[claude["id"]]["usage_kind"] == "exact"
    assert cards[codex["id"]]["usage_pct"] == 35.0 and cards[codex["id"]]["usage_kind"] == "exact"


def test_api_key_usage_is_estimated_against_a_budget(client, project):
    api = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    chat(client, project, "hi", api["id"])
    card = next(c for c in client.get("/api/usage").json()["cards"] if c["id"] == api["id"])
    assert card["usage_pct"] is None and card["usage_kind"] == "none"
    client.patch(f"/api/accounts/{api['id']}", json={"budget_tokens_5h": 300})
    card = next(c for c in client.get("/api/usage").json()["cards"] if c["id"] == api["id"])
    assert card["usage_kind"] == "estimated" and card["usage_pct"] == 10.0   # 21 in + 9 out of 300


def test_ranges(client):
    for r in ("5h", "today", "7d", "month"):
        assert client.get(f"/api/usage?range={r}").status_code == 200
    assert client.get("/api/usage?range=year").status_code == 400
    now = time.time()
    assert range_start("5h", now) == now - 5 * 3600
    assert range_start("today", now) <= now and range_start("month", now) <= range_start("today", now)


def test_thresholds_validate(client):
    acc = client.post("/api/accounts", json={"provider": "claude"}).json()
    assert acc["warn_pct"] == 70 and acc["switch_pct"] == 85
    ok = client.patch(f"/api/accounts/{acc['id']}", json={"warn_pct": 60, "switch_pct": 90}).json()
    assert ok["warn_pct"] == 60 and ok["switch_pct"] == 90
    assert client.patch(f"/api/accounts/{acc['id']}", json={"warn_pct": 95, "switch_pct": 90}).status_code == 400
    assert client.patch(f"/api/accounts/{acc['id']}", json={"budget_tokens_5h": 0}).status_code == 400


def test_switch_order(client):
    ids = [client.post("/api/accounts", json={"provider": p}).json()["id"] for p in ("claude", "codex", "claude")]
    new = [ids[2], ids[0], ids[1]]
    got = [a["id"] for a in client.post("/api/accounts/order", json={"ids": new}).json() if a["id"] != "demo"]
    assert got == new
    assert client.post("/api/accounts/order", json={"ids": new[:2]}).status_code == 400


def test_csv_export_is_redacted(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    chat(client, project, "hi", claude["id"])
    # a key smuggled into a stored field must not survive export
    import sqlite3
    db = sqlite3.connect(client.data_dir / "usage.sqlite")
    db.execute("UPDATE calls SET project=?", (f"oops {GOOD_KEY}",)); db.commit(); db.close()
    r = client.get("/api/usage/export.csv?range=month")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "time" and len(rows) == 2
    assert GOOD_KEY not in r.text and "[REDACTED" in r.text


def test_estimate_accuracy_flags_unreliable_estimates(client, project, memory_keyring):
    api = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    client.patch(f"/api/accounts/{api['id']}", json={"budget_tokens_5h": 1000})
    chat(client, project, "hi", api["id"])               # 30 tokens -> est 3%
    memory_keyring.store[("OneProduction", f"anthropic-api:{api['id']}")] = GOOD_KEY + "LIMIT"
    chat(client, project, "hi", api["id"])               # real limit hit while estimate says ~3%
    card = next(c for c in client.get("/api/usage").json()["cards"] if c["id"] == api["id"])
    acc = card["accuracy"]
    assert acc["hits"] == 1 and acc["reliable"] is False and acc["min_est_at_hit"] < 70
    hits = client.get("/api/usage/limit-hits").json()
    assert hits[0]["account"] == api["id"]


def test_demo_calls_recorded_but_no_card(client, project):
    chat(client, project, "hi", "demo")
    u = client.get("/api/usage").json()
    assert u["totals"]["requests"] == 1 and all(c["id"] != "demo" for c in u["cards"])
