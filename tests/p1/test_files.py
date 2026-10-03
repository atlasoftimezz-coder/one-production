"""R4 storage: any file type, byte-exact round trip, safe names."""
import os

import pytest

KINDS = {
    "script.docx": b"PK\x03\x04" + os.urandom(4096),
    "voice.mp3": b"ID3\x04" + os.urandom(200_000),
    "thumb.png": b"\x89PNG\r\n\x1a\n" + os.urandom(50_000),
    "clip.mp4": b"\x00\x00\x00\x18ftypmp42" + os.urandom(3_000_000),
    "notes.md": "# Ünïcödé — ✓\n".encode("utf-8"),
}


def test_projects_create_and_list(client):
    a = client.post("/api/projects", json={"name": "Ghost towns"}).json()
    b = client.post("/api/projects", json={"name": ""}).json()
    assert a["name"] == "Ghost towns" and b["name"] == "Untitled"
    ids = [p["id"] for p in client.get("/api/projects").json()]
    assert a["id"] in ids and b["id"] in ids


def test_upload_download_roundtrip_is_byte_equal(client, project):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/files",
                    files=[("files", (n, data)) for n, data in KINDS.items()])
    assert r.status_code == 200
    listed = {f["name"]: f["size"] for f in client.get(f"/api/projects/{pid}/files").json()}
    for name, data in KINDS.items():
        assert listed[name] == len(data)
        got = client.get(f"/api/projects/{pid}/files/{name}")
        assert got.status_code == 200
        assert got.content == data, f"{name} changed in transit"


def test_duplicate_names_are_kept_not_overwritten(client, project):
    pid = project["id"]
    for content in (b"first", b"second", b"third"):
        client.post(f"/api/projects/{pid}/files", files={"files": ("same.txt", content)})
    names = sorted(f["name"] for f in client.get(f"/api/projects/{pid}/files").json())
    assert names == ["same (1).txt", "same (2).txt", "same.txt"]
    assert client.get(f"/api/projects/{pid}/files/same.txt").content == b"first"


@pytest.mark.parametrize("evil", ["../../escape.txt", "..\\..\\escape.txt", "C:\\Windows\\x.txt",
                                  "/etc/passwd", "CON.txt", "a<b>c?.txt"])
def test_upload_names_cannot_escape_the_project(client, project, evil):
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/files", files={"files": (evil, b"data")})
    assert r.status_code == 200
    saved = r.json()[0]["name"]
    files_dir = client.data_dir / "projects" / pid / "files"
    assert (files_dir / saved).is_file()
    assert (files_dir / saved).resolve().parent == files_dir.resolve()
    assert not (client.data_dir / "escape.txt").exists()


def test_download_cannot_read_outside_project(client, project):
    pid = project["id"]
    (client.data_dir / "secret.txt").write_text("top secret")
    for p in ("../../secret.txt", "..%2F..%2Fsecret.txt", "..\\..\\secret.txt"):
        r = client.get(f"/api/projects/{pid}/files/{p}")
        assert r.status_code == 404
        assert b"top secret" not in r.content


def test_unknown_project_is_404(client):
    for p in ("/api/projects/nope-000000/files", "/api/projects/../x/chat"):
        assert client.get(p).status_code == 404


def test_no_partial_files_left_behind(client, project):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/files", files={"files": ("big.bin", os.urandom(2_500_000))})
    leftovers = list((client.data_dir / "projects" / pid / "files").glob("*.part"))
    assert leftovers == []
