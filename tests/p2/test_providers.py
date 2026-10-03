"""Each provider streams real replies, and each error class takes the right path (R9)."""
import json

from .conftest import GOOD_KEY, calls, chat, error_of, signed_in, text_of


# ---------------------------------------------------------------- demo
def test_demo_works_with_no_accounts(client, project):
    ev = chat(client, project, "hello demo", "demo")
    assert "hello demo" in text_of(ev) and ev[-1]["t"] == "done" and not error_of(ev)


# ---------------------------------------------------------------- claude
def test_claude_reply_streams_and_skips_thinking(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    ev = chat(client, project, "plan my video", acc["id"])
    texts = [e for e in ev if e["t"] == "text"]
    assert len(texts) > 3 and "hmm" not in text_of(ev)
    assert "Fake Claude says: plan my video" in text_of(ev)
    usage = next(e for e in ev if e["t"] == "usage")
    assert usage["output_tokens"] == 30 and usage["model"] == "claude-sonnet-5-5"


def test_claude_exact_usage_is_recorded_on_the_account(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    chat(client, project, "hi", acc["id"])
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == acc["id"])
    assert a["rate_limit"]["windows"]["five_hour"]["utilization"] == 0.42
    assert a["rate_limit"]["windows"]["seven_day"]["utilization"] == 0.10


def test_reply_saved_with_who_answered(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    chat(client, project, "remember", acc["id"])
    last = client.get(f"/api/projects/{project}/chat").json()[-1]
    assert last["role"] == "assistant" and last["account"] == acc["id"]
    assert last["usage"]["out"] == 30


def test_claude_limit_is_reported_and_not_retried(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "limit")
    ev = chat(client, project, "hi", acc["id"])
    assert error_of(ev)["kind"] == "limit"
    assert calls(client) == 1, "a usage limit must not be retried"
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == acc["id"])
    assert a["rate_limit"]["status"] == "rejected"


def test_claude_auth_error_marks_account_red(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "auth")
    ev = chat(client, project, "hi", acc["id"])
    assert error_of(ev)["kind"] == "auth"
    a = next(x for x in client.get("/api/accounts").json() if x["id"] == acc["id"])
    assert a["status"] == "error"


def test_signed_out_account_reports_auth(client, project):
    acc = client.post("/api/accounts", json={"provider": "claude"}).json()
    ev = chat(client, project, "hi", acc["id"])
    assert error_of(ev)["kind"] == "auth"


def test_transient_errors_retry_then_succeed(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "transient")
    monkeypatch.setenv("FAKE_FAILS", "2")
    ev = chat(client, project, "try again", acc["id"])
    assert not error_of(ev) and "try again" in text_of(ev)
    assert calls(client) == 3


def test_transient_errors_give_up_after_three_retries(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "transient")
    ev = chat(client, project, "hi", acc["id"])
    assert error_of(ev)["kind"] == "transient"
    assert calls(client) == 4          # first try + 3 retries


def test_failure_mid_reply_is_not_resent(client, project, monkeypatch):
    """Once text has streamed, re-sending would bill the prompt twice. Keep the partial."""
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "midstream")
    ev = chat(client, project, "long answer please", acc["id"])
    err = error_of(ev)
    assert err["kind"] == "transient" and err["interrupted"] is True
    assert text_of(ev).startswith("Fake Claude says:")
    assert calls(client) == 1
    saved = client.get(f"/api/projects/{project}/chat").json()[-1]
    assert saved["interrupted"] is True and saved["text"].startswith("Fake Claude")


def test_stuck_cli_is_killed_by_timeout(make_client, monkeypatch):
    c = make_client(reply_timeout=1.5)
    monkeypatch.setenv("ONEPROD_RETRY_BACKOFF", "0.01")
    pid = c.post("/api/projects", json={"name": "t"}).json()["id"]
    acc = signed_in(c, "claude", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "slow")
    ev = chat(c, pid, "hi", acc["id"])
    err = error_of(ev)
    assert err["kind"] == "transient" and "no response" in err["message"]


# ---------------------------------------------------------------- codex
def test_codex_reply_and_usage(client, project, monkeypatch):
    acc = signed_in(client, "codex", monkeypatch=monkeypatch)
    ev = chat(client, project, "outline please", acc["id"])
    assert "Fake Codex says: outline please" in text_of(ev)
    assert next(e for e in ev if e["t"] == "usage")["input_tokens"] == 50


def test_codex_limit(client, project, monkeypatch):
    acc = signed_in(client, "codex", monkeypatch=monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "limit")
    assert error_of(chat(client, project, "hi", acc["id"]))["kind"] == "limit"


def test_codex_signed_out(client, project):
    acc = client.post("/api/accounts", json={"provider": "codex"}).json()
    assert error_of(chat(client, project, "hi", acc["id"]))["kind"] == "auth"


# ---------------------------------------------------------------- anthropic api
def test_api_key_streams_and_reports_exact_tokens(client, project):
    acc = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    ev = chat(client, project, "hi", acc["id"])
    assert "Fake API says hello (claude-sonnet-5-5)" in text_of(ev)
    u = next(e for e in ev if e["t"] == "usage")
    assert u["input_tokens"] == 21 and u["output_tokens"] == 9


def test_api_rate_limit(client, project, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    memory_keyring.store[("OneProduction", f"anthropic-api:{acc['id']}")] = GOOD_KEY + "LIMIT"
    assert error_of(chat(client, project, "hi", acc["id"]))["kind"] == "limit"


# ---------------------------------------------------------------- safety
def test_prompt_goes_through_stdin_so_shell_characters_are_harmless(client, project, monkeypatch):
    """npm installs the CLIs as .cmd files; anything on their command line passes through
    cmd.exe. The prompt must travel on stdin so text like this does nothing."""
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    evil = 'hi" & echo PWNED > pwned.txt & rem "'
    ev = chat(client, project, evil, acc["id"])
    assert "PWNED" in text_of(ev)               # the text arrived as text...
    assert not list(client.tmp.rglob("pwned.txt")), "...and was never executed"
    logged = [json.loads(l) for l in (client.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert evil in logged[-1]["prompt"]
    assert all(evil not in a for a in logged[-1]["args"])


def test_cli_runs_in_an_empty_folder_not_the_project(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    chat(client, project, "hi", acc["id"])
    run = client.data_dir / "cache" / "run"
    assert run.is_dir() and not any(run.iterdir())


def test_turned_off_account_is_refused(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    client.patch(f"/api/accounts/{acc['id']}", json={"enabled": False})
    r = client.post(f"/api/projects/{project}/chat", json={"message": "hi", "account_id": acc["id"]})
    assert r.status_code == 400


def test_conversation_history_is_sent(client, project, monkeypatch):
    acc = signed_in(client, "claude", monkeypatch=monkeypatch)
    chat(client, project, "my channel is about ghost towns", acc["id"])
    chat(client, project, "what is my channel about?", acc["id"])
    logged = (client.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
    assert "ghost towns" in json.loads(logged[-1])["prompt"]
