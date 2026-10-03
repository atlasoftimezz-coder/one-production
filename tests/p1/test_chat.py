"""Chat streams its reply in pieces and keeps history per project."""
import json
import time
import urllib.request

from .test_launch import _start


def test_reply_streams_in_pieces_on_a_real_server(tmp_path):
    # FastAPI's TestClient buffers whole responses, so streaming can only be proven against
    # the real server: the first bytes must arrive well before the last ones.
    proc, port, token = _start(tmp_path)
    try:
        def call(method, path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-OneProd-Token": token,
                                                  "Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=10)

        pid = json.load(call("POST", "/api/projects", {"name": "stream"}))["id"]
        start = time.perf_counter()
        resp = call("POST", f"/api/projects/{pid}/chat", {"message": "hello there"})
        first = resp.read1(64)
        t_first = time.perf_counter() - start
        rest = resp.read()
        t_all = time.perf_counter() - start
        raw = (first + rest).decode()
        # Since P2 the reply is NDJSON: one {"t": "text", "v": ...} line per piece.
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        text = "".join(e["v"] for e in events if e["t"] == "text")
        assert "hello there" in text
        assert events[-1] == {"t": "done"}
        assert t_all - t_first > 0.2, f"no streaming: first byte {t_first:.2f}s, last {t_all:.2f}s"
    finally:
        proc.kill(); proc.wait(10)


def test_history_is_saved_per_project(client):
    a = client.post("/api/projects", json={"name": "A"}).json()["id"]
    b = client.post("/api/projects", json={"name": "B"}).json()["id"]
    client.post(f"/api/projects/{a}/chat", json={"message": "only in A"})
    ha = client.get(f"/api/projects/{a}/chat").json()
    hb = client.get(f"/api/projects/{b}/chat").json()
    assert [m["role"] for m in ha] == ["user", "assistant"]
    assert ha[0]["text"] == "only in A"
    assert hb == []


def test_history_survives_restart(client, project, tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from .conftest import TOKEN

    pid = project["id"]
    client.post(f"/api/projects/{pid}/chat", json={"message": "remember me"})
    fresh = TestClient(create_app(Settings(data_dir=client.data_dir), token=TOKEN),
                       base_url="http://127.0.0.1", headers={"X-OneProd-Token": TOKEN})
    assert fresh.get(f"/api/projects/{pid}/chat").json()[0]["text"] == "remember me"
