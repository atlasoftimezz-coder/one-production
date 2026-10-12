"""Custom storage (owner, 2026-10-12): finished episodes copied to an internal folder and to Google Drive
(the owner's own Drive API client), and files imported from either into a project. Google is a stand-in."""
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import cloud_storage
from app.config import Settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

TOKEN = "test-session-token-0123456789abcdef"
CID = "1234-abc.apps.googleusercontent.com"


@pytest.fixture
def memory_keyring():
    import keyring
    from app.security.memkeyring import MemoryKeyring
    prev = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(prev)


class FakeDrive:
    """Just enough of Google's token endpoint and Drive v3: folders, resumable uploads, listing, download."""

    def __init__(self):
        self.files = {"root": {"id": "root", "name": "My Drive", "mimeType": cloud_storage.FOLDER_MIME, "parents": []},
                      "doc1": {"id": "doc1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
                               "parents": ["root"]},
                      "clip1": {"id": "clip1", "name": "ref clip.mp4", "mimeType": "video/mp4", "parents": ["root"],
                                "data": b"MP4DATA"}}
        self.sessions, self.puts, self.n = {}, 0, 0

    def new_id(self):
        self.n += 1
        return f"f{self.n}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cloud_storage.TOKEN_URL):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0] and form["client_secret"] == ["s3cret"]
                return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
            assert form["refresh_token"] == ["rt1"]
            return httpx.Response(200, json={"access_token": "at2", "expires_in": 3600})
        path, q = request.url.path, dict(request.url.params)
        if url not in self.sessions:                         # an upload session URL carries its own authority
            assert request.headers.get("authorization", "").startswith("Bearer at")
        if path.endswith("/about"):
            return httpx.Response(200, json={"user": {"emailAddress": "owner@gmail.com"},
                                             "storageQuota": {"limit": "5497558138880", "usage": "1000"}})
        if path == "/drive/v3/files" and request.method == "GET":
            parent = re.search(r"'([^']+)' in parents", q["q"]).group(1)
            name = re.search(r"name='([^']+)'", q["q"])
            hits = [f for f in self.files.values() if parent in f["parents"]
                    and (not name or (f["name"] == name.group(1) and f["mimeType"] == cloud_storage.FOLDER_MIME))]
            return httpx.Response(200, json={"files": [{k: v for k, v in f.items() if k in ("id", "name", "mimeType")}
                                                       | ({"size": str(len(f["data"]))} if "data" in f else {})
                                                       for f in hits]})
        if path == "/drive/v3/files" and request.method == "POST":
            body = json.loads(request.content)
            fid = self.new_id()
            self.files[fid] = {"id": fid, **body}
            return httpx.Response(200, json={"id": fid})
        if path == "/upload/drive/v3/files" and request.method == "POST":
            body = json.loads(request.content)
            sid = f"https://www.googleapis.com/upload/session/{self.new_id()}"
            self.sessions[sid] = {"meta": body, "data": b"", "size": int(request.headers["x-upload-content-length"])}
            return httpx.Response(200, headers={"location": sid})
        if url in self.sessions and request.method == "PUT":
            s = self.sessions[url]
            self.puts += 1
            s["data"] += request.content
            if len(s["data"]) < s["size"]:
                return httpx.Response(308, headers={"range": f"bytes=0-{len(s['data']) - 1}"})
            fid = self.new_id()
            self.files[fid] = {"id": fid, **s["meta"], "mimeType": "application/octet-stream", "data": s["data"]}
            return httpx.Response(200, json={"id": fid})
        m = re.match(r"/drive/v3/files/([^/]+)$", path)
        if m and m.group(1) in self.files:
            f = self.files[m.group(1)]
            if q.get("alt") == "media":
                return httpx.Response(200, content=f["data"])
            return httpx.Response(200, json={k: v for k, v in f.items() if k in ("id", "name", "mimeType")})
        return httpx.Response(404)

    def path_of(self, fid):
        out = []
        while fid != "root":
            f = self.files[fid]
            out.append(f["name"])
            fid = f["parents"][0]
        return "/".join(reversed(out))


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def client(tmp_path, memory_keyring, monkeypatch, drive):
    monkeypatch.setenv("ONEPROD_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(cloud_storage, "CHUNK", 4)                     # several chunks even for tiny files
    app = create_app(Settings(data_dir=tmp_path / "data"), token=TOKEN, drive_transport=httpx.MockTransport(drive))
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers[TOKEN_HEADER] = TOKEN
    c.data_dir = tmp_path / "data"
    return c


def episode(client):
    pid = client.post("/api/projects", json={"name": "Gold: v2"}).json()["id"]
    files = client.data_dir / "projects" / pid / "files"
    (files / "episode" / "gold-v2" / "thumbs").mkdir(parents=True)
    (files / "episode" / "gold-v2" / "video.mp4").write_bytes(b"0123456789")
    (files / "episode" / "gold-v2" / "thumbs" / "t1.png").write_bytes(b"PNG")
    return pid, files


def wait_jobs(client):
    for _ in range(200):
        jobs = client.get("/api/storage").json()["jobs"]
        if jobs and all(j["state"] != "running" for j in jobs):
            return jobs
        time.sleep(0.02)
    raise AssertionError("copies did not finish")


def test_internal_storage_takes_episodes_and_gives_files(client, tmp_path):
    store_dir = tmp_path / "NAS share"
    assert client.put("/api/storage/internal", json={"path": "relative\\path"}).status_code == 400
    st = client.put("/api/storage/internal", json={"path": str(store_dir), "auto": True}).json()
    assert st["internal"]["ok"] and st["internal"]["auto"]
    pid, files = episode(client)
    r = client.post(f"/api/projects/{pid}/storage/export", json={"paths": ["episode/gold-v2"], "name": "gold-v2",
                                                                 "locations": ["internal"]})
    assert r.status_code == 200
    job = wait_jobs(client)[0]
    assert job["state"] == "done" and job["files"] == 2
    out = store_dir / "Gold_ v2" / "gold-v2" / "gold-v2"
    assert (out / "video.mp4").read_bytes() == b"0123456789" and (out / "thumbs" / "t1.png").exists()
    # browse and import back into the project
    (store_dir / "refs").mkdir()
    (store_dir / "refs" / "voice.wav").write_bytes(b"RIFF")
    top = client.get("/api/storage/browse", params={"location": "internal"}).json()
    assert {"refs", "Gold_ v2"} <= {i["name"] for i in top["items"] if i["folder"]}
    inside = client.get("/api/storage/browse", params={"location": "internal", "where": "refs"}).json()
    assert inside["items"] == [{"id": "refs/voice.wav", "name": "voice.wav", "folder": False, "size": 4}]
    got = client.post(f"/api/projects/{pid}/storage/import", json={"location": "internal", "ids": ["refs/voice.wav",
                                                                                                     "../../secret.txt"]}).json()
    assert got["imported"] == ["imports/voice.wav"] and (files / "imports" / "voice.wav").read_bytes() == b"RIFF"
    assert got["skipped"]                                                      # nothing outside the storage folder
    assert client.get("/api/storage/browse", params={"location": "internal", "where": ".."}).status_code == 400
    assert client.post(f"/api/projects/{pid}/storage/export",
                       json={"paths": ["../../etc"], "name": "x", "locations": ["internal"]}).status_code == 400


def test_google_drive_connects_uploads_in_chunks_and_imports(client, drive, memory_keyring):
    import keyring
    from app.accounts import KEYRING_SERVICE
    assert client.post("/api/storage/drive/connect").status_code == 400            # no client yet
    assert client.put("/api/storage/drive", json={"client_id": "not-a-client"}).status_code == 400
    client.put("/api/storage/drive", json={"client_id": CID, "client_secret": "s3cret", "folder": "One Production",
                                           "auto": True})
    start = client.post("/api/storage/drive/connect").json()
    q = parse_qs(urlparse(start["url"]).query)
    assert "drive.file" in q["scope"][0] and "drive.readonly" in q["scope"][0] and q["access_type"] == ["offline"]
    assert client.get(f"/api/storage/drive/connect/{start['state']}").json() == {"connected": False}
    redirect = q["redirect_uri"][0]
    assert httpx.get(f"{redirect}?state={start['state']}&code=c0de").status_code == 200   # Google's redirect
    done = client.get(f"/api/storage/drive/connect/{start['state']}").json()
    assert done["connected"] and done["drive"]["email"] == "owner@gmail.com"
    assert keyring.get_password(KEYRING_SERVICE, "drive-refresh-token") == "rt1"
    assert "rt1" not in (client.data_dir.parent / "settings.json").read_text(encoding="utf-8")
    pid, files = episode(client)
    client.post(f"/api/projects/{pid}/storage/export", json={"paths": ["episode/gold-v2"], "name": "gold-v2",
                                                             "locations": ["drive"]})
    job = wait_jobs(client)[0]
    assert job["state"] == "done" and job["sent"] == job["bytes"] == 13, job
    uploaded = {drive.path_of(f["id"]): f["data"] for f in drive.files.values() if "data" in f and f["id"] != "clip1"}
    assert uploaded == {"One Production/Gold_ v2/gold-v2/gold-v2/video.mp4": b"0123456789",
                        "One Production/Gold_ v2/gold-v2/gold-v2/thumbs/t1.png": b"PNG"}
    assert drive.puts >= 4                                                          # resumable, chunk by chunk
    # browse My Drive and import a clip; a Google Doc is skipped with a reason
    items = client.get("/api/storage/browse", params={"location": "drive"}).json()["items"]
    assert {i["name"]: i.get("google_doc") for i in items if not i["folder"]} == {"Notes": True, "ref clip.mp4": False}
    got = client.post(f"/api/projects/{pid}/storage/import", json={"location": "drive", "ids": ["clip1", "doc1"]}).json()
    assert got["imported"] == ["imports/ref clip.mp4"] and (files / "imports" / "ref clip.mp4").read_bytes() == b"MP4DATA"
    assert "Google Docs" in got["skipped"][0]
    assert client.get("/api/storage/drive/quota").json()["limit"] == 5497558138880
    # disconnect forgets the login
    client.delete("/api/storage/drive")
    assert keyring.get_password(KEYRING_SERVICE, "drive-refresh-token") is None


def test_finished_episodes_go_to_every_storage_switched_on(tmp_path, memory_keyring):
    import keyring
    from app.accounts import KEYRING_SERVICE
    saved = {}
    s = Settings(data_dir=tmp_path / "d")
    st = cloud_storage.Storage(s, lambda k, v: saved.__setitem__(k, v), None)
    assert st.auto_locations() == []
    st.set_internal(str(tmp_path / "out"), True)
    st.set_drive(CID, "s3cret", "One Production", True)
    assert st.auto_locations() == ["internal"]                                      # Drive not connected yet
    keyring.set_password(KEYRING_SERVICE, cloud_storage.REFRESH_NAME, "rt1")
    assert st.auto_locations() == ["internal", "drive"] and saved["storage"]["drive"]["auto"] is True
