"""Backups of project data (plan R11): zip, keep 14, restore from the app, nothing erased."""
import json
import time
import zipfile
from pathlib import Path

import pytest

from app.backups import Backups, Busy
from tests.p2.conftest import GOOD_KEY

from .conftest import FILES, project_with_files, tree


def backup(c, **body):
    r = c.post("/api/backups", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def restore(c, name, pid):
    return c.post(f"/api/backups/{name}/restore", json={"project": pid})


def pdir(c, pid):
    return c.data_dir / "projects" / pid


def test_backup_delete_restore_is_byte_equal(client):
    pid = project_with_files(client)
    before = tree(pdir(client, pid))
    assert "files/voice.wav" in before and "chat.jsonl" in before
    b = backup(client)
    assert b["name"].startswith("oneprod-") and b["kind"] == "manual"
    assert Path(client.get("/api/backups").json()["settings"]["folder_resolved"], b["name"]).is_file()
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    listed = client.get("/api/backups").json()["backups"][0]["projects"][0]
    assert listed["id"] == pid and listed["exists"] is False and listed["files"] == len(before)
    r = restore(client, b["name"], pid)
    assert r.status_code == 200, r.text
    assert tree(pdir(client, pid)) == before                         # every byte, CRLF and accents too
    assert pid in [p["id"] for p in client.get("/api/projects").json()]
    assert client.get(f"/api/projects/{pid}/files/notes.md").content == FILES["notes.md"]


def test_no_secret_ever_reaches_a_backup(client):
    pid = project_with_files(client)
    client.post(f"/api/projects/{pid}/chat", json={"message": f"my key is {GOOD_KEY}", "account_id": "demo"})
    client.post(f"/api/projects/{pid}/files", files={"files": ("keys.txt", f"api_key = {GOOD_KEY}\n".encode())})
    b = backup(client)
    zpath = Path(client.get("/api/backups").json()["settings"]["folder_resolved"]) / b["name"]
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            assert GOOD_KEY.encode() not in z.read(n), n                 # the secret scan
        chat = z.read(f"projects/{pid}/chat.jsonl").decode()
        man = json.loads(z.read("manifest.json"))
    assert "[REDACTED:anthropic-key]" in chat
    files = {f["path"]: f for f in man["projects"][0]["files"]}
    assert files["chat.jsonl"]["redacted"] and files["files/keys.txt"]["redacted"]
    assert not files["files/notes.md"]["redacted"]


def test_media_can_be_left_out_and_restore_keeps_current_media(client):
    pid = project_with_files(client)
    b = backup(client, include_media=False)
    info = client.get("/api/backups").json()["backups"][0]["projects"][0]
    assert info["skipped"] == 2                                      # voice.mp3 + voice.wav
    zpath = Path(client.get("/api/backups").json()["settings"]["folder_resolved"]) / b["name"]
    with zipfile.ZipFile(zpath) as z:
        assert not any(n.endswith((".mp3", ".wav")) for n in z.namelist())
    r = restore(client, b["name"], pid).json()                       # project still there: media kept
    assert sorted(r["kept_media"]) == ["files/voice.mp3", "files/voice.wav"] and r["missing_media"] == []
    assert (pdir(client, pid) / "files" / "voice.wav").read_bytes() == FILES["voice.wav"]
    client.delete(f"/api/projects/{pid}")
    r = restore(client, b["name"], pid).json()                       # deleted first: media can't come back
    assert sorted(r["missing_media"]) == ["files/voice.mp3", "files/voice.wav"]


def test_restore_over_a_newer_version_moves_that_version_to_trash(client):
    pid = project_with_files(client)
    b = backup(client)
    (pdir(client, pid) / "files" / "notes.md").write_text("rewritten by the agent", encoding="utf-8")
    r = restore(client, b["name"], pid).json()
    assert (pdir(client, pid) / "files" / "notes.md").read_bytes() == FILES["notes.md"]
    previous = Path(r["previous"])
    assert previous.parent == client.data_dir / "trash"
    assert (previous / "files" / "notes.md").read_text(encoding="utf-8") == "rewritten by the agent"


def test_only_the_newest_backups_are_kept(client):
    project_with_files(client)
    assert client.patch("/api/backups/settings", json={"keep": 3}).status_code == 200
    names = [backup(client)["name"] for _ in range(5)]
    kept = [b["name"] for b in client.get("/api/backups").json()["backups"]]
    assert kept == list(reversed(names))[:3]


def test_damaged_backup_is_refused_and_nothing_changes(client):
    pid = project_with_files(client)
    b = backup(client)
    folder = Path(client.get("/api/backups").json()["settings"]["folder_resolved"])
    src, bad = folder / b["name"], folder / b["name"].replace("manual", "daily")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n.endswith("notes.md"):
                data = data.replace(b"Hook", b"Hack")                 # bit rot
            zout.writestr(n, data)
    before = tree(pdir(client, pid))
    r = restore(client, bad.name, pid)
    assert r.status_code == 400 and "damaged" in r.json()["detail"]
    assert tree(pdir(client, pid)) == before                         # current copy untouched
    assert not list((client.data_dir / "projects").glob(".restore-*"))


def test_a_crafted_backup_cannot_write_outside_the_project(client):
    pid = project_with_files(client)
    folder = Path(client.get("/api/backups").json()["settings"]["folder_resolved"])
    folder.mkdir(parents=True, exist_ok=True)
    evil = folder / "oneprod-20260101-000000-manual.zip"
    man = {"projects": [{"id": pid, "name": "x", "skipped": [], "jobs": [],
                         "files": [{"path": "../../escaped.txt", "size": 1, "sha256": "0" * 64}]}]}
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("manifest.json", json.dumps(man))
        z.writestr(f"projects/{pid}/../../escaped.txt", b"x")
    r = restore(client, evil.name, pid)
    assert r.status_code == 400 and "unsafe" in r.json()["detail"]
    assert not (client.data_dir / "escaped.txt").exists() and not (client.data_dir / "projects" / "escaped.txt").exists()
    assert restore(client, "..\\..\\x.zip", pid).status_code == 400


def test_backup_folder_can_be_chosen(client, tmp_path):
    project_with_files(client)
    assert client.patch("/api/backups/settings", json={"folder": "relative\\folder"}).status_code == 400
    target = tmp_path / "D-drive" / "Backups" / "one-production"
    s = client.patch("/api/backups/settings", json={"folder": str(target)}).json()
    assert s["folder_resolved"] == str(target)
    b = backup(client)
    assert (target / b["name"]).is_file()
    s = client.patch("/api/backups/settings", json={"folder": ""}).json()     # back to the default
    assert s["folder_resolved"] == str(client.data_dir / "backups")


def test_voiceover_cards_come_back_with_the_project(client):
    pid = project_with_files(client)
    client.post(f"/api/projects/{pid}/files", files={"files": ("s.txt", b"## Water\nWater is life.\n")})
    job = client.post(f"/api/projects/{pid}/voice", json={"file": "s.txt", "voice": "human_ref48.wav",
                                                         "approve": "once"}).json()
    end = time.time() + 20
    while client.get(f"/api/voicejobs/{job['id']}").json()["status"] != "done" and time.time() < end:
        time.sleep(0.05)
    b = backup(client)
    client.delete(f"/api/projects/{pid}")
    for f in (client.data_dir / "cache" / "voicejobs").glob(job["id"] + "*"):
        f.unlink()                                                   # as on a fresh install
    assert restore(client, b["name"], pid).status_code == 200
    assert client.get(f"/api/voicejobs/{job['id']}").json()["status"] == "done"


def test_restore_waits_for_a_running_voiceover(client):
    pid = project_with_files(client)
    b = backup(client)
    client.fake.hold = True
    client.post(f"/api/projects/{pid}/files", files={"files": ("s.txt", b"## Water\nWater is life.\n")})
    client.post(f"/api/projects/{pid}/voice", json={"file": "s.txt", "voice": "human_ref48.wav", "approve": "once"})
    time.sleep(0.3)
    r = restore(client, b["name"], pid)
    assert r.status_code == 409 and "voiceover" in r.json()["detail"]
    client.fake.hold = False


def test_one_backup_at_a_time(tmp_path):
    class NoStore:
        root = tmp_path

        def list_projects(self):
            return []
    b = Backups(tmp_path, NoStore(), lambda t: t)
    b._lock.acquire()
    with pytest.raises(Busy):
        b.create()


def test_daily_backup_runs_by_itself_once_a_day(make_client):
    c = make_client(backup_delay=0.05, backup_tick=0.1)
    project_with_files(c)
    end = time.time() + 10
    daily = []
    while time.time() < end and not daily:
        daily = [b for b in c.get("/api/backups").json()["backups"] if b["kind"] == "daily"]
        time.sleep(0.1)
    assert len(daily) == 1
    time.sleep(0.6)                                                  # several more ticks: not due again
    assert len([b for b in c.get("/api/backups").json()["backups"] if b["kind"] == "daily"]) == 1
    assert c.get("/api/backups").json()["settings"]["last_daily"] > 0
