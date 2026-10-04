"""R1: any number of accounts, each isolated, secrets never on disk."""
from pathlib import Path

from .conftest import GOOD_KEY, signed_in


def test_only_demo_at_start(client):
    accs = client.get("/api/accounts").json()
    assert [a["id"] for a in accs] == ["demo"]


def test_cli_accounts_get_their_own_folders(client):
    a = client.post("/api/accounts", json={"provider": "claude"}).json()
    b = client.post("/api/accounts", json={"provider": "claude"}).json()
    c = client.post("/api/accounts", json={"provider": "codex"}).json()
    folders = [p.name for p in (client.data_dir / "profiles").iterdir()]
    assert sorted(folders) == sorted([a["id"], b["id"], c["id"]])
    assert "profile_dir" not in a, "absolute paths must not reach the browser"


def test_two_claude_logins_with_different_emails_stay_apart(client, monkeypatch):
    first = signed_in(client, "claude", "owner@example.com", monkeypatch)
    second = signed_in(client, "claude", "work@company.com", monkeypatch)
    codex = signed_in(client, "codex", "owner@example.com", monkeypatch)
    accs = {a["id"]: a for a in client.post("/api/accounts/check").json()}
    assert accs[first["id"]]["email"] == "owner@example.com"
    assert accs[second["id"]]["email"] == "work@company.com"
    assert accs[codex["id"]]["status"] == "ok" and accs[codex["id"]]["method"] == "ChatGPT"
    assert accs[first["id"]]["plan"] == "pro"


def test_new_login_starts_signed_out(client):
    acc = client.post("/api/accounts", json={"provider": "claude"}).json()
    checked = client.post(f"/api/accounts/{acc['id']}/check").json()
    assert checked["status"] == "error" and checked["reason"] == "not logged in"


def test_api_key_goes_to_keyring_never_to_disk(client, memory_keyring):
    acc = client.post("/api/accounts", json={"provider": "anthropic_api", "label": "Main API",
                                             "api_key": GOOD_KEY}).json()
    assert acc["status"] == "ok" and acc["key_hint"] == "…" + GOOD_KEY[-4:]
    assert ("OneProduction", f"anthropic-api:{acc['id']}") in memory_keyring.store
    for f in client.data_dir.rglob("*"):
        if f.is_file():
            assert GOOD_KEY not in f.read_text(encoding="utf-8", errors="ignore"), f"key leaked into {f}"
    for a in client.get("/api/accounts").json():
        assert GOOD_KEY not in str(a)


def test_bad_api_key_is_rejected_and_nothing_saved(client, memory_keyring):
    r = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": "sk-ant-api03-WRONGwrongWRONG1234"})
    assert r.status_code == 400 and "didn't work" in r.json()["detail"]
    assert memory_keyring.store == {}
    assert [a["id"] for a in client.get("/api/accounts").json()] == ["demo"]


def test_remove_deletes_folder_and_key(client, memory_keyring):
    cli = client.post("/api/accounts", json={"provider": "codex"}).json()
    api = client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY}).json()
    client.delete(f"/api/accounts/{cli['id']}")
    client.delete(f"/api/accounts/{api['id']}")
    assert not (client.data_dir / "profiles" / cli["id"]).exists()
    assert memory_keyring.store == {}
    assert [a["id"] for a in client.get("/api/accounts").json()] == ["demo"]


def test_rename_disable_and_model(client):
    acc = client.post("/api/accounts", json={"provider": "claude"}).json()
    r = client.patch(f"/api/accounts/{acc['id']}", json={"label": "Main", "enabled": False, "model": "opus"}).json()
    assert r["label"] == "Main" and r["enabled"] is False and r["model"] == "opus"
    assert client.patch(f"/api/accounts/{acc['id']}", json={"model": "gpt-9"}).status_code == 400


def test_unknown_provider_and_account(client):
    assert client.post("/api/accounts", json={"provider": "myspace"}).status_code == 400
    assert client.post("/api/accounts/nope/check").status_code == 404
    assert client.delete("/api/accounts/nope").status_code == 404


def test_registry_file_holds_no_secrets(client):
    client.post("/api/accounts", json={"provider": "anthropic_api", "api_key": GOOD_KEY})
    text = (client.data_dir / "accounts.json").read_text(encoding="utf-8")
    assert GOOD_KEY not in text
