"""Script Studio through the real app: settings, the competitor list, the scripted Agent SDK as
analyst / writer / reviewer, history, usage roles - and the FacelessOS text never lands on disk."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.p5.conftest import claude_account

from .test_competitors import FAKE, fake_facelessos

MARK = "SKILLTEXT-7f3a"
LINKS = "Competitors: https://www.youtube.com/@mrfacts and youtu.be/NOCAPTION01, also https://example.com"


@pytest.fixture
def studio(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("ONEPROD_YTDLP", str(FAKE))
    fos = tmp_path / "Faceless os"
    fake_facelessos(fos, MARK)
    agent.fos = fos
    return agent


def start(c, **body):
    data = {"topic": "The Lost City", "format": "true_crime", "minutes": 8, "niche": "history", "links_text": LINKS, **body}
    return c.pool.submit(lambda: c.post(f"/api/projects/{c.pid}/script", json=data))


def lines(fut, timeout=30):
    r = fut.result(timeout)
    assert r.status_code == 200, r.text
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_settings(studio):
    s = studio.get("/api/script/settings").json()
    assert s["settings"]["rounds"] == 5 and s["settings"]["pass_score"] == 8 and s["settings"]["stall"] == 2
    assert s["settings"]["master_model"] == "opus" and s["settings"]["worker_model"] == "sonnet"
    assert not s["facelessos"]["ok"] and "true_crime" in s["formats"] and "anti_slop" in s["rubric"]
    assert studio.patch("/api/script/settings", json={"facelessos_dir": str(studio.fos / "nothing")}).status_code == 400
    r = studio.patch("/api/script/settings", json={"facelessos_dir": f'"{studio.fos}"', "rounds": 7}).json()
    assert r["facelessos"]["ok"] and r["facelessos"]["skills_dir"].endswith("skills") and r["settings"]["rounds"] == 7
    assert studio.patch("/api/script/settings", json={"pass_score": 11}).status_code == 400
    assert studio.patch("/api/script/settings", json={"worker_effort": "ultra"}).status_code == 400


def test_needs_folder_links_and_an_account(studio):
    r = studio.post(f"/api/projects/{studio.pid}/script", json={"topic": "x", "links_text": LINKS})
    assert r.status_code == 400 and "FacelessOS folder" in r.json()["detail"]
    studio.patch("/api/script/settings", json={"facelessos_dir": str(studio.fos)})
    r = studio.post(f"/api/projects/{studio.pid}/script", json={"topic": "x", "links_text": "no links here"})
    assert r.status_code == 400 and "no YouTube links" in r.json()["detail"]
    r = studio.post(f"/api/projects/{studio.pid}/script", json={"topic": "x", "links_text": LINKS})
    assert r.status_code == 400 and "account" in r.json()["detail"]


def test_script_end_to_end(studio):
    claude_account(studio)
    studio.patch("/api/script/settings", json={"facelessos_dir": str(studio.fos)})
    passing = json.dumps({"scores": {k: 9 for k in ("hook", "structure", "retention", "rehooks", "anti_slop",
                                                    "authenticity", "style_match", "length", "outro")},
                          "must_fix": [], "notes": "ready", "verdict": "pass"})
    revise = json.dumps({"scores": {"hook": 5}, "must_fix": ["the hook takes 40 seconds"], "notes": "faster"})
    studio.script.turns = [
        [("tool", "Write", {"file_path": "competitors/style-guide.md", "content": "Fast and curious."}),
         ("text", "Style guide written.")],                                                        # analyst
        [("tool", "Write", {"file_path": "scripts/the-lost-city.md", "content": "[VISUAL: ruins]\nSlow hook. " * 3}),
         ("text", "Draft 1 done.")],                                                               # writer
        [("tool", "Write", {"file_path": "scripts/the-lost-city.md", "content": "hacked"}), ("text", revise)],   # reviewer
        [("tool", "Write", {"file_path": "scripts/the-lost-city.md", "content": "Fast hook. " * 50}),
         ("text", "Draft 2 done.")],                                                               # writer again
        [("text", passing)],                                                                       # reviewer passes
    ]
    ev = lines(start(studio))
    snap = [e for e in ev if e["t"] == "script"][-1]["script"]
    assert snap["state"] == "done", (snap["result"], snap["tasks"], snap["fetch"])
    assert snap["result"]["passed"] and snap["result"]["round"] == 2
    assert snap["fetch"]["done"] == 4 and snap["fetch"]["problems"][0]["why"] == "no English captions"
    files = studio.files
    assert (files / "competitors" / "_numbers.md").exists() and (files / "competitors" / "style-guide.md").exists()
    assert (files / "scripts" / "the-lost-city.md").read_text().startswith("Fast hook.")
    assert (files / "scripts" / "the-lost-city.v1.md").read_text().startswith("[VISUAL: ruins]")
    refused = [r for r in studio.script.results if r["tool"] == "Write" and r["error"]]
    assert refused and "reviews only" in refused[0]["text"]                              # the reviewer can't edit
    seen = studio.script.seen
    assert [(s["model"], s["effort"]) for s in seen] == [("sonnet", "medium"), ("sonnet", "medium"), ("opus", "high"),
                                                         ("sonnet", "medium"), ("opus", "high")]
    appends = [s["system"]["append"] for s in seen]
    assert all(MARK in a for a in appends)                                              # skills reach every role
    assert "true-crime-skill.md" in appends[1] and "authenticity-audit-skill.md" in appends[2]
    assert "the hook takes 40 seconds" in seen[3]["prompt"]                              # the card goes back
    hist = studio.get(f"/api/projects/{studio.pid}/chat").json()
    assert hist[-2]["script"] is True and "2 competitor link(s)" in hist[-2]["text"]
    assert hist[-1]["text"].startswith("Script ready: scripts/the-lost-city.md - round 2")
    assert hist[-1]["script"]["rounds"][0]["must_fix"] == ["the hook takes 40 seconds"]
    roles = {r["role"]: r["requests"] for r in studio.get("/api/usage?range=today").json()["by_role"]}
    assert roles == {"analyst": 1, "writer": 2, "reviewer": 2}
    assert studio.get(f"/api/projects/{studio.pid}/script").json()["script"]["state"] == "done"
    for url in ("scripts%2Fthe-lost-city.md", "scripts/the-lost-city.md"):            # the Files panel's download
        r = studio.get(f"/api/projects/{studio.pid}/files/{url}")
        assert r.status_code == 200 and r.text.startswith("Fast hook.")
    assert studio.get(f"/api/projects/{studio.pid}/files/..%2F..%2Fproject.json").status_code == 404

    # the paid FacelessOS text never lands in the data folder, a backup or diagnostics
    for p in studio.data_dir.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".json", ".jsonl", ".txt", ".log"):
            assert MARK not in p.read_text(encoding="utf-8", errors="ignore"), p
    assert studio.post("/api/backups", json={}).status_code == 200
    zips = sorted((studio.data_dir / "backups").glob("*.zip"))
    with zipfile.ZipFile(zips[-1]) as z:
        assert all(MARK not in z.read(n).decode("utf-8", "ignore") for n in z.namelist())
    r = studio.get("/api/diagnostics")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert all(MARK not in z.read(n).decode("utf-8", "ignore") for n in z.namelist())


def test_competitor_list_from_a_project_file(studio):
    claude_account(studio)
    studio.patch("/api/script/settings", json={"facelessos_dir": str(studio.fos), "rounds": 1})
    studio.post(f"/api/projects/{studio.pid}/files", files={"files": ("rivals.csv", b"name,url\nFacts,https://youtu.be/FACTSVID002\n")})
    studio.script.turns = [[("text", "Style guide written.")], [("text", "the script itself in the reply")],
                           [("text", json.dumps({"scores": {"hook": 6}, "must_fix": ["more"]}))]]
    snap = [e for e in lines(start(studio, links_text="", links_file="rivals.csv")) if e["t"] == "script"][-1]["script"]
    assert snap["fetch"]["done"] == 1 and snap["state"] == "needs_you"
    assert (studio.files / "competitors" / "style-guide.md").read_text() == "Style guide written."   # reply saved
    assert (studio.files / "scripts" / "the-lost-city.md").read_text() == "the script itself in the reply"
    assert "not passed after 1 rounds" in snap["result"]["why"]
    assert studio.get(f"/api/projects/{studio.pid}/chat").json()[-1]["text"].startswith("Best version kept")
