"""Competitors tab routes: listing, reading, renaming, approving as guidance and deleting style profiles."""
import json

import pytest

from tests.p10.test_style_learn import PROFILE


@pytest.fixture
def project(client):
    return client.post("/api/projects", json={"name": "Styles"}).json()["id"]


def made_profile(client, pid):
    files = client.data_dir / "projects"
    folder = next(p for p in files.iterdir() if p.name == pid) / "files" / "style" / "rival"
    folder.mkdir(parents=True)
    prof = {**PROFILE, "measured": {"pace": {"cuts_per_min": 24, "shot_median": 2.1}, "sound": {"cuts_with_a_hit": 0.8}},
            "videos": [{"id": "FACTSVID001", "title": "T", "channel": "Rival"}]}
    (folder / "profile.json").write_text(json.dumps(prof), encoding="utf-8")
    (folder / "numbers.json").write_text(json.dumps(prof["measured"]), encoding="utf-8")
    return client.app.state.styles.add("Rival", pid, "style/rival", prof["videos"]), folder


def test_list_read_rename_approve_delete(client, project):
    entry, folder = made_profile(client, project)
    items = client.get("/api/styles").json()
    assert items[0]["name"] == "Rival" and items[0]["available"] and items[0]["measured"]["pace"]["cuts_per_min"] == 24
    full = client.get(f"/api/styles/{entry['id']}").json()
    assert full["profile"]["do"] == ["cut on every new claim"] and full["numbers"]["pace"]["shot_median"] == 2.1
    assert client.patch(f"/api/styles/{entry['id']}", json={"name": "Rival fast"}).json()["name"] == "Rival fast"

    approved = client.post(f"/api/styles/{entry['id']}/approve", json={"scope": "project"}).json()
    assert approved["lesson"]
    lessons = client.get(f"/api/projects/{project}/learning").json()
    found = [l for l in (lessons.get("lessons") if isinstance(lessons, dict) else lessons) if l["id"] == approved["lesson"]]
    assert found and "24 cuts/min" in found[0]["text"] and found[0]["state"] == "active"

    assert client.delete(f"/api/styles/{entry['id']}").json() == {"removed": entry["id"]}
    assert client.get("/api/styles").json() == [] and not folder.exists()
    lessons = client.get(f"/api/projects/{project}/learning").json()
    gone = [l for l in (lessons.get("lessons") if isinstance(lessons, dict) else lessons) if l["id"] == approved["lesson"]]
    assert not gone or gone[0]["state"] == "forgotten"


def test_unknown_style_and_bad_scope(client, project):
    assert client.get("/api/styles/nope").status_code == 404
    entry, _ = made_profile(client, project)
    assert client.post(f"/api/styles/{entry['id']}/approve", json={"scope": "planet"}).status_code == 400


def test_starting_needs_links_and_a_claude_account(client, project):
    r = client.post(f"/api/projects/{project}/style", json={"links_text": "no links here"})
    assert r.status_code == 400 and "link" in r.json()["detail"]
    r = client.post(f"/api/projects/{project}/style", json={"links_text": "https://youtu.be/FACTSVID001"})
    assert r.status_code == 400 and "Claude" in r.json()["detail"]
