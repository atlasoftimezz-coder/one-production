"""P12 Google sign-in (plan R7): the owner's own OAuth desktop client, PKCE + a one-shot loopback
listener, the ID token verified with (a stand-in) Google, only allowed emails get in, nothing but the
client ID / allowed list / secret (memory keyring here) is kept. The "browser" is simulated by calling
the loopback redirect the way Google would."""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

CLIENT = "1234-abc.apps.googleusercontent.com"
SEEN: dict = {}


def google(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/token":
        form = parse_qs(request.content.decode())
        SEEN["token_form"] = {k: v[0] for k, v in form.items()}
        if form["code"][0] == "bad-code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"id_token": "IDT." + form["code"][0], "access_token": "at"})
    if request.url.path == "/tokeninfo":
        who = request.url.params["id_token"].split(".", 1)[1]
        claims = {"aud": CLIENT, "iss": "https://accounts.google.com", "email": f"{who}@gmail.com",
                  "email_verified": "true", "exp": str(int(time.time()) + 3600)}
        if who == "otherapp":
            claims["aud"] = "999-other.apps.googleusercontent.com"
        if who == "unverified":
            claims["email_verified"] = "false"
        if who == "expired":
            claims["exp"] = str(int(time.time()) - 10)
        return httpx.Response(200, json=claims)
    return httpx.Response(404)


@pytest.fixture
def app_client(tmp_path, fake_cmds, monkeypatch):
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    app = create_app(Settings(data_dir=tmp_path / "data", claude_cmd=str(fake_cmds / "claude.cmd"),
                              codex_cmd=str(fake_cmds / "codex.cmd")), token="g" * 32, login_console=False,
                     google_transport=httpx.MockTransport(google))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers[TOKEN_HEADER] = "g" * 32
        c.data_dir = tmp_path / "data"
        yield c


def browser_returns(url: str, code: str, state: str | None = None) -> int:
    """What Google does after consent: redirect the browser to our loopback listener."""
    q = parse_qs(urlparse(url).query)
    target = q["redirect_uri"][0] + "?" + f"code={code}&state={state or q['state'][0]}"
    try:
        return urllib.request.urlopen(target, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def wait(c, state):
    for _ in range(50):
        s = c.get(f"/api/profile/google/status?state={state}").json()
        if s["status"] != "waiting":
            return s
        time.sleep(0.05)
    return s


def setup(c, required=True, allowed=("owner@gmail.com",)):
    r = c.put("/api/profile/google", json={"client_id": CLIENT, "client_secret": "s3cret", "allowed": list(allowed), "required": required})
    assert r.status_code == 200, r.text
    return r.json()


def test_setup_is_checked_and_keeps_no_tokens(app_client, memory_keyring):
    c = app_client
    assert "apps.googleusercontent.com" in c.put("/api/profile/google", json={"client_id": "nope", "client_secret": "x", "allowed": ["a@b.c"]}).json()["detail"]
    assert "allowed email" in c.put("/api/profile/google", json={"client_id": CLIENT, "client_secret": "x", "allowed": []}).json()["detail"]
    assert "client secret" in c.put("/api/profile/google", json={"client_id": CLIENT, "allowed": ["a@b.c"]}).json()["detail"]
    p = setup(c, required=False, allowed=["Owner@Gmail.com"])
    assert p["google"] == {"configured": True, "required": False, "client_id": CLIENT, "allowed": ["owner@gmail.com"]}
    assert memory_keyring.store[("OneProduction", "google-oauth-secret")] == "s3cret"
    stored = (c.data_dir / "profile.json").read_text(encoding="utf-8")
    assert "s3cret" not in stored and CLIENT in stored
    assert c.delete("/api/profile/google").json()["google"]["configured"] is False
    assert ("OneProduction", "google-oauth-secret") not in memory_keyring.store


def test_required_sign_in_locks_the_app_and_an_allowed_email_opens_it(app_client):
    c = app_client
    setup(c, required=True)
    c.post("/api/profile/lock")
    assert c.get("/api/projects").status_code == 423                       # locked: nothing else works
    start = c.post("/api/profile/google/start").json()
    q = parse_qs(urlparse(start["url"]).query)
    assert start["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert q["client_id"] == [CLIENT] and q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email"]
    assert q["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert c.get(f"/api/profile/google/status?state={start['state']}").json() == {"status": "waiting"}
    assert browser_returns(start["url"], "owner", state="forged") == 400        # a wrong state is refused
    assert browser_returns(start["url"], "owner") == 200
    s = wait(c, start["state"])
    assert s["status"] == "done" and s["email"] == "owner@gmail.com" and s["profile"]["locked"] is False
    assert c.get("/api/projects").status_code == 200
    # PKCE: the verifier sent to Google hashes to the challenge in the consent URL
    verifier = SEEN["token_form"]["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [challenge] and SEEN["token_form"]["client_secret"] == "s3cret"
    assert "IDT." not in (c.data_dir / "profile.json").read_text(encoding="utf-8")     # tokens dropped


@pytest.mark.parametrize("code, why", [("stranger", "not on the allowed list"), ("otherapp", "different app"),
                                       ("unverified", "not verified"), ("expired", "expired"), ("bad-code", "refused")])
def test_anything_else_stays_locked(app_client, code, why):
    c = app_client
    setup(c, required=True, allowed=["owner@gmail.com", "otherapp@gmail.com", "unverified@gmail.com", "expired@gmail.com"])
    c.post("/api/profile/lock")
    start = c.post("/api/profile/google/start").json()
    browser_returns(start["url"], code)
    s = wait(c, start["state"])
    assert s["status"] == "failed" and why in s["error"]
    assert c.get("/api/projects").status_code == 423


def test_not_set_up_and_unknown_state(app_client):
    c = app_client
    assert "not set up" in c.post("/api/profile/google/start").json()["detail"]
    assert c.get("/api/profile/google/status?state=nope").json()["status"] == "failed"
    assert c.get("/api/profile").json()["locked"] is False                 # no PIN, not required: open
