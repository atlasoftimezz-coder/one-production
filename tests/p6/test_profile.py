"""Local profile and optional PIN lock (plan R7)."""
import json

import app.profile as profile_mod


def test_no_pin_means_no_lock(client):
    p = client.get("/api/profile").json()
    assert p == {"name": "", "has_pin": False, "locked": False}
    assert client.get("/api/projects").status_code == 200
    assert client.patch("/api/profile", json={"name": "Sam"}).json()["name"] == "Sam"


def test_pin_is_never_stored_and_must_be_digits(client):
    assert client.post("/api/profile/pin", json={"new": "12a4"}).status_code == 400
    assert client.post("/api/profile/pin", json={"new": "123"}).status_code == 400
    assert client.post("/api/profile/pin", json={"new": "482916"}).json()["has_pin"] is True
    raw = (client.data_dir / "profile.json").read_text(encoding="utf-8")
    assert "482916" not in raw and json.loads(raw)["pin"]["hash"]


def test_app_starts_locked_and_only_the_pin_opens_it(make_client):
    first = make_client()
    first.post("/api/profile/pin", json={"new": "482916"})
    c = make_client(data_dir=first.data_dir)                         # next start of the app
    assert c.get("/api/profile").json()["locked"] is True
    assert c.get("/api/projects").status_code == 423
    assert c.post("/api/profile/unlock", json={"pin": "000000"}).status_code == 403
    assert c.post("/api/profile/unlock", json={"pin": "482916"}).json()["locked"] is False
    assert c.get("/api/projects").status_code == 200
    c.post("/api/profile/lock")
    assert c.get("/api/projects").status_code == 423


def test_without_the_session_token_the_lock_reveals_nothing(make_client):
    first = make_client()
    first.post("/api/profile/pin", json={"new": "482916"})
    c = make_client(data_dir=first.data_dir)
    r = c.get("/api/profile", headers={"X-OneProd-Token": "wrong"})
    assert r.status_code == 401                                      # token check comes first


def test_wrong_pins_slow_down(make_client, monkeypatch):
    first = make_client()
    first.post("/api/profile/pin", json={"new": "482916"})
    c = make_client(data_dir=first.data_dir)
    now = [1_000_000.0]
    monkeypatch.setattr(profile_mod.time, "time", lambda: now[0])
    for _ in range(5):
        assert c.post("/api/profile/unlock", json={"pin": "111111"}).status_code == 403
    r = c.post("/api/profile/unlock", json={"pin": "482916"})       # even the right PIN waits now
    assert r.status_code == 429 and "seconds" in r.json()["detail"]
    now[0] += 61
    assert c.post("/api/profile/unlock", json={"pin": "482916"}).status_code == 200


def test_change_and_remove_need_the_current_pin(client):
    client.post("/api/profile/pin", json={"new": "482916"})
    assert client.post("/api/profile/pin", json={"current": "000000", "new": "5555"}).status_code == 400
    assert client.post("/api/profile/pin", json={"current": "482916", "new": "5555"}).status_code == 200
    assert client.post("/api/profile/pin", json={"current": "5555", "new": None}).json()["has_pin"] is False
