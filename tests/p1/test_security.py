"""R6: session token, Host check (DNS rebinding), Origin check (other web pages)."""
import pytest

from .conftest import TOKEN

API_ROUTES = [("get", "/api/projects"), ("post", "/api/projects")]


@pytest.mark.parametrize("method,path", API_ROUTES)
def test_api_without_token_is_401(anon, method, path):
    r = anon.post(path, json={}) if method == "post" else anon.get(path)
    assert r.status_code == 401


def test_api_with_wrong_token_is_401(anon):
    r = anon.get("/api/projects", headers={"X-OneProd-Token": "wrong-token-value"})
    assert r.status_code == 401


def test_api_with_token_works(client):
    assert client.get("/api/projects").status_code == 200


def test_token_never_returned_by_any_endpoint(client, project):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/files", files={"files": ("a.txt", b"x")})
    client.post(f"/api/projects/{pid}/chat", json={"message": "hello"})
    paths = ["/", "/health", "/api/projects", f"/api/projects/{pid}/files",
             f"/api/projects/{pid}/chat", f"/api/projects/{pid}/files/a.txt"]
    for p in paths:
        r = client.get(p)
        assert TOKEN not in r.text, f"token leaked by {p}"
        assert TOKEN not in str(r.headers), f"token leaked in headers of {p}"


@pytest.mark.parametrize("host", ["evil.test", "evil.test:8730", "192.168.1.6:8730", "attacker.com"])
def test_foreign_host_rejected_everywhere(client, host):
    for path in ("/", "/health", "/api/projects"):
        r = client.get(path, headers={"Host": host})
        assert r.status_code == 400, f"{host} accepted on {path}"


@pytest.mark.parametrize("host", ["127.0.0.1:8730", "localhost:8730", "127.0.0.1"])
def test_local_host_accepted(client, host):
    assert client.get("/api/projects", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.test", "https://attacker.com", "null"])
def test_foreign_origin_rejected_even_with_token(client, origin):
    r = client.post("/api/projects", json={"name": "x"}, headers={"Origin": origin})
    assert r.status_code == 403


def test_own_origin_accepted(client):
    r = client.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://127.0.0.1:8730"})
    assert r.status_code == 200


def test_no_cors_headers_offered(client):
    r = client.options("/api/projects", headers={"Origin": "http://evil.test",
                                                 "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_api_docs_are_disabled(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404
