"""Claude logins signed in with a token from `claude setup-token` instead of the browser window."""
import json

from app.accounts import Accounts, claude_env

from .conftest import GOOD_KEY, chat

TOKEN = "sk-ant-oat01-" + "TOKENtoken" * 8 + "9xYz"


def prompts(c) -> list[dict]:
    p = c.tmp / "prompts.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_token_signs_a_claude_login_in_without_a_browser(client, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "claude", "label": "Token", "oauth_token": TOKEN}).json()
    assert acc["status"] == "ok" and acc["method"] == "sign-in token"
    assert acc["token_hint"] == "…" + TOKEN[-4:]
    assert memory_keyring.store[("OneProduction", f"claude-token:{acc['id']}")] == TOKEN


def test_token_never_reaches_disk_or_the_browser(client, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "claude", "oauth_token": TOKEN}).json()
    for f in client.data_dir.rglob("*"):
        if f.is_file():
            assert TOKEN not in f.read_text(encoding="utf-8", errors="ignore"), f"token leaked into {f}"
    assert TOKEN not in json.dumps(client.get("/api/accounts").json())
    assert TOKEN not in json.dumps(client.post(f"/api/accounts/{acc['id']}/check").json())


def test_chat_runs_on_the_token_and_other_accounts_never_get_it(client, project, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FROM-THE-PCS-OWN-ENVIRONMENT-xxxxxxxxxxxxxxxx")
    tok = client.post("/api/accounts", json={"provider": "claude", "oauth_token": TOKEN}).json()
    plain = client.post("/api/accounts", json={"provider": "claude"}).json()
    events = chat(client, project, "hello there", tok["id"])
    assert not any(e["t"] == "error" for e in events), events
    assert prompts(client)[-1]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    # A login without a token stays signed out, even with a token in the PC's own environment.
    checked = client.post(f"/api/accounts/{plain['id']}/check").json()
    assert checked["status"] == "error" and checked["reason"] == "not logged in"
    assert claude_env(Accounts(client.data_dir).get(plain["id"]))["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_api_key_in_the_token_box_and_token_in_the_key_box_are_explained(client, memory_keyring):
    r = client.post("/api/accounts", json={"provider": "claude", "oauth_token": GOOD_KEY})
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    r = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": TOKEN})
    assert r.status_code == 400 and "sign-in token" in r.json()["detail"]
    r = client.post("/api/accounts", json={"provider": "claude", "oauth_token": "hello"})
    assert r.status_code == 400 and "setup-token" in r.json()["detail"]
    r = client.post("/api/accounts", json={"provider": "codex", "oauth_token": TOKEN})
    assert r.status_code == 400
    assert memory_keyring.store == {}
    assert [a["id"] for a in client.get("/api/accounts").json()] == ["demo"]


def test_add_change_and_remove_a_token_on_an_existing_login(client, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "claude"}).json()
    r = client.put(f"/api/accounts/{acc['id']}/token", json={"token": TOKEN}).json()
    assert r["status"] == "ok" and r["token_hint"] == "…" + TOKEN[-4:]
    assert client.put(f"/api/accounts/{acc['id']}/token", json={"token": "nope"}).status_code == 400
    r = client.delete(f"/api/accounts/{acc['id']}/token").json()
    assert r["token_hint"] is None and r["status"] == "error"
    assert memory_keyring.store == {}
    assert client.put("/api/accounts/demo/token", json={"token": TOKEN}).status_code == 400
    assert client.put("/api/accounts/nope/token", json={"token": TOKEN}).status_code == 404


def test_removing_the_account_removes_its_token(client, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "claude", "oauth_token": TOKEN}).json()
    client.delete(f"/api/accounts/{acc['id']}")
    assert memory_keyring.store == {}
