"""R3 + D6: handoff on limit / threshold / backup / shutdown, and continuing on another account."""
import json
import time

from .conftest import GOOD_KEY, calls, chat, error_of, signed_in, text_of


def offer_of(events):
    return next((e for e in events if e["t"] == "switch_offer"), None)


def handoffs(c, pid):
    return c.get(f"/api/projects/{pid}/handoffs").json()


def read(c, pid, name):
    return c.get(f"/api/projects/{pid}/handoffs/{name}").text


# ---------------------------------------------------------------- triggers
def test_no_offer_below_threshold(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    ev = chat(client, project, "hi", claude["id"])      # 42% < 85%
    assert offer_of(ev) is None and ev[-1] == {"t": "done"}


def test_threshold_crossing_offers_switch_with_handoff(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_UTIL", "0.87")
    ev = chat(client, project, "write the cold open", claude["id"])
    offer = offer_of(ev)
    assert offer and offer["reason"] == "threshold" and offer["usage_pct"] == 87.0
    assert offer["usage_kind"] == "exact"
    assert [e["id"] for e in offer["eligible"] if e["ok"]] == [codex["id"]]
    assert offer["handoff"] in [h["name"] for h in handoffs(client, project)]
    assert ev[-1] == {"t": "done"}                       # offer comes before done


def test_threshold_is_per_account_and_editable(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    client.patch(f"/api/accounts/{claude['id']}", json={"warn_pct": 30, "switch_pct": 40})
    ev = chat(client, project, "hi", claude["id"])      # 42% >= 40%
    assert offer_of(ev)["reason"] == "threshold"


def test_limit_error_offers_switch_and_records_hit(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "limit")
    ev = chat(client, project, "hi", claude["id"])
    assert error_of(ev)["kind"] == "limit" and offer_of(ev)["reason"] == "limit"
    assert client.get("/api/usage/limit-hits").json()[0]["account"] == claude["id"]


def test_no_eligible_account_lists_reasons_and_reset_times(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    off = signed_in(client, "codex", monkeypatch=monkeypatch)
    client.patch(f"/api/accounts/{off['id']}", json={"enabled": False})
    other_claude = signed_in(client, "claude", "second@example.com", monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "limit")
    offer = offer_of(chat(client, project, "hi", claude["id"]))
    assert not any(e["ok"] for e in offer["eligible"])
    why = {e["id"]: e["why"] for e in offer["eligible"]}
    assert why[off["id"]] == "turned off"
    assert "same service" in why[other_claude["id"]]                 # plan R8 gate
    assert offer["from_resets_at"] and offer["from_resets_at"] > time.time()   # when it comes back


def test_api_key_is_eligible_after_a_subscription(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    api = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    monkeypatch.setenv("FAKE_MODE", "limit")
    offer = offer_of(chat(client, project, "hi", claude["id"]))
    assert [e["id"] for e in offer["eligible"] if e["ok"]] == [api["id"]]


def test_demo_never_switches_or_backs_up(client, project):
    for i in range(10):
        chat(client, project, f"msg {i}", "demo")
    assert handoffs(client, project) == []


def test_backup_handoff_every_ten_replies_real_account(client, project, monkeypatch):
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    for i in range(9):
        chat(client, project, f"msg {i}", codex["id"])
    assert handoffs(client, project) == []
    chat(client, project, "msg 9", codex["id"])
    names = [h["name"] for h in handoffs(client, project)]
    assert len(names) == 1 and names[0].endswith("-backup.md")
    assert (client.data_dir / "projects" / project / "handoff.md").exists()


# ---------------------------------------------------------------- handoff content
def test_handoff_content_and_redaction(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    signed_in(client, "codex", monkeypatch=monkeypatch)
    client.post(f"/api/projects/{project}/files", files={"files": ("script.docx", b"PK..")})
    chat(client, project, "Goal: a 10 minute video on Pripyat", claude["id"])
    chat(client, project, f"here is my key {GOOD_KEY} oops", claude["id"])
    monkeypatch.setenv("FAKE_MODE", "midstream")
    chat(client, project, "write the full script", claude["id"])
    # the reply was cut off; a manual switch writes a handoff we can inspect
    r = client.post(f"/api/projects/{project}/switch", json={"account_id": claude["id"]}).json()
    text = read(client, project, r["handoff"])
    assert "# Handoff" in text and "Goal: a 10 minute video on Pripyat" in text
    assert "script.docx" in text
    assert "Reply that was cut off" in text and "Fake Claude says:" in text
    assert "*(cut off)*" in text
    assert GOOD_KEY not in text and "[REDACTED:anthropic-key]" in text
    assert "5-hour" in text                                      # usage at switch


# ---------------------------------------------------------------- continuing
def test_continue_hands_over_to_next_account(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    chat(client, project, "my channel is about ghost towns", claude["id"])
    monkeypatch.setenv("FAKE_MODE", "limit")
    offer = offer_of(chat(client, project, "now write episode 2", claude["id"]))
    monkeypatch.delenv("FAKE_MODE")
    r = client.post(f"/api/projects/{project}/continue",
                    json={"to_account": codex["id"], "from_account": claude["id"],
                          "handoff": offer["handoff"], "trigger": "limit"})
    events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert "Fake Codex says:" in text_of(events)
    prompts = [json.loads(l) for l in (client.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    sent = prompts[-1]["prompt"]
    assert "taking over this conversation" in sent and "ghost towns" in sent and "now write episode 2" in sent
    log = client.get("/api/usage/switches").json()[0]
    assert log["from_acc"] == claude["id"] and log["to_acc"] == codex["id"] and log["trigger"] == "limit"
    assert log["usage_at_switch"] == 100.0 and log["handoff"] == offer["handoff"]
    hist = client.get(f"/api/projects/{project}/chat").json()
    assert any(m["role"] == "system" and "Continued on" in m["text"] for m in hist)


def test_interrupted_reply_is_continued_not_restarted(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    codex = signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "midstream")
    chat(client, project, "write a long script", claude["id"])
    monkeypatch.delenv("FAKE_MODE")
    off = client.post(f"/api/projects/{project}/switch", json={"account_id": claude["id"]}).json()
    client.post(f"/api/projects/{project}/continue", json={"to_account": codex["id"], "from_account": claude["id"],
                                                            "handoff": off["handoff"]})
    sent = json.loads((client.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()[-1])["prompt"]
    assert "continue it exactly from where it stops" in sent


def test_continue_refuses_ineligible_accounts(client, project, monkeypatch):
    claude = signed_in(client, "claude", monkeypatch=monkeypatch)
    claude2 = signed_in(client, "claude", "two@example.com", monkeypatch)
    off = client.post(f"/api/projects/{project}/switch", json={"account_id": claude["id"]}).json()
    r = client.post(f"/api/projects/{project}/continue", json={"to_account": claude2["id"], "from_account": claude["id"],
                                                                "handoff": off["handoff"]})
    assert r.status_code == 400 and "same service" in r.json()["detail"]
    r = client.post(f"/api/projects/{project}/continue", json={"to_account": claude2["id"], "from_account": claude["id"],
                                                                "handoff": "../../accounts.json"})
    assert r.status_code in (400, 404)


def test_handoff_paths_cannot_escape(client, project, monkeypatch):
    assert client.get(f"/api/projects/{project}/handoffs/..%2F..%2Faccounts.json").status_code == 404


def test_shutdown_writes_handoff(make_client, monkeypatch):
    c = make_client()
    pid = c.post("/api/projects", json={"name": "s"}).json()["id"]
    chat(c, pid, "unsaved work", "demo")
    c.close_app()                           # stopping the app runs the shutdown hook
    latest = c.data_dir / "projects" / pid / "handoff.md"
    assert latest.exists() and "unsaved work" in latest.read_text(encoding="utf-8")
    assert "the app was closed" in latest.read_text(encoding="utf-8")
