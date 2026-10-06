"""P12 post-publish learning loop: a YouTube Studio audience-retention export of the published episode ->
the steepest drops (not the intro) mapped to chapter, Fusion shot and spoken words -> at most 3 lesson
CANDIDATES (drafted by the AI, or rule-based without an account) -> nothing is used until the owner
approves; forget works. Stand-in Claude CLI (FAKE_REPLY), a synthetic episode on disk."""
import json

import pytest

from app.insights import InsightError, drops, parse_lessons, parse_retention
from tests.p2.conftest import signed_in

SECONDS = 200.0
CUES = [(0, 20, "Hydrogen was first, and there was plenty of it."),
        (20, 60, "Helium and lithium followed in the first minutes."),
        (60, 100, "Now a long list of every isotope ever measured, one by one."),
        (100, 160, "Then the stars lit up and started forging carbon."),
        (160, 200, "And that is where we come from.")]


def stamp(sec):
    return f"00:{int(sec) // 60:02d}:{int(sec) % 60:02d},000"


def curve(drop_at=(70, 120)):
    """100 rows of 'Video position (%)' + absolute retention; a cliff in each region of drop_at (seconds)."""
    rows, v = ["Video position (%),Absolute audience retention (%),Relative audience retention"], 100.0
    for pct in range(0, 101):
        sec = pct / 100 * SECONDS
        v -= 3.0 if sec <= 10 else 0.2                       # every video drops in the first seconds
        if any(a <= sec < a + 4 for a in drop_at):
            v -= 6.0 if sec < 100 else 4.0
        rows.append(f"{pct},{v:.1f},0.5")
    return "\n".join(rows)


def episode(c, name="Elements"):
    pid = c.post("/api/projects", json={"name": name}).json()["id"]
    root = c.tmp / "data" / "projects" / pid
    files, base = root / "files", "work/elements"
    (files / base).mkdir(parents=True)
    (files / "out" / "elements-bundle").mkdir(parents=True)
    (files / base / "vo.srt").write_text("\n\n".join(f"{n}\n{stamp(a)} --> {stamp(b)}\n{t}" for n, (a, b, t) in enumerate(CUES, 1)),
                                         encoding="utf-8")
    shots = [{"name": f"shot-{n:03d}", "start": int(a * 60), "frames": int((b - a) * 60), "idea": idea, "anchor": "", "text": ""}
             for n, (a, b, idea) in enumerate([(0, 60, "tile grid of the first elements"), (60, 100, "scrolling isotope table"),
                                               (100, 160, "3D star forging carbon"), (160, 200, "zoom out to Earth")], 1)]
    (files / base / "fusion.json").write_text(json.dumps({"fps": 60, "seconds": SECONDS, "shots": shots}), encoding="utf-8")
    (files / "out" / "elements-bundle" / "metadata.json").write_text(json.dumps(
        {"title": "Where the elements come from", "chapters": [{"time": "0:00", "title": "The first atoms"},
                                                               {"time": "1:00", "title": "Isotopes"},
                                                               {"time": "1:40", "title": "Star forges"}]}), encoding="utf-8")
    (root / "episode.json").write_text(json.dumps({
        "kind": "episode", "state": "done", "spec": {"name": name}, "base": base,
        "vo": {"wav": f"{base}/vo.wav", "srt": f"{base}/vo.srt"},
        "stages": [{"id": "package", "files": ["out/elements-bundle"]}]}), encoding="utf-8")
    (files / "retention.csv").write_text(curve(), encoding="utf-8")
    return pid


def lessons(c, pid, state=None):
    out = c.get(f"/api/projects/{pid}/learning").json()["lessons"]
    return [l for l in out if state is None or l["state"] == state]


def test_parse_and_drops():
    pts = parse_retention(curve(), SECONDS)
    assert len(pts) == 101 and pts[0] == (0.0, 97.0) and pts[-1][0] == SECONDS
    found = drops(pts)
    assert [round(d["start"]) for d in found][:2] == [68, 118]          # steepest first, never the intro
    assert all(d["start"] >= 14 for d in found)
    # seconds or m:ss positions, 0-1 ratios, a BOM, other header wording
    alt = "﻿Time,Audience watching\n0:00,1.0\n0:30,0.8\n0:40,0.5\n1:00,0.45\n"
    assert parse_retention(alt, 0) == [(0.0, 100.0), (30.0, 80.0), (40.0, 50.0), (60.0, 45.0)]
    with pytest.raises(InsightError, match="columns"):
        parse_retention("a,b\n1,2\n3,4\n5,6\n", 100)
    assert parse_lessons('noise {"lessons": [{"title": "t", "text": "x"}]} end') == [{"title": "t", "text": "x"}]
    assert parse_lessons("not json") == []


def test_ai_drafts_candidates_mapped_to_the_moment_and_owner_approves(client, monkeypatch):
    c = client
    pid = episode(c)
    signed_in(c, "claude")
    reply = {"lessons": [{"title": "Cut list-style passages", "text": "Replace the one-by-one isotope list (1:10) with one picked example.", "drop": "1:10"},
                         {"title": "Show the star sooner", "text": "Open the star-forge chapter on the 3D star, not on talk.", "drop": "2:00"},
                         {"title": "x", "text": "y"}, {"title": "fourth", "text": "never saved"}]}
    monkeypatch.setenv("FAKE_REPLY", json.dumps(reply))
    r = c.post(f"/api/projects/{pid}/insights", json={"retention": "retention.csv"})
    assert r.status_code == 200, r.text
    out = r.json()
    rep = out["report"]
    first = rep["drops"][0]
    assert first["at"] == "1:08" and first["chapter"] == "Isotopes" and first["shot"]["name"] == "shot-002"
    assert "isotope ever measured" in first["words"]
    assert rep["drops"][1]["chapter"] == "Star forges" and rep["drops"][1]["shot"]["idea"] == "3D star forging carbon"
    assert out["drafted_by"] == "claude" and len(out["lessons"]) == 3              # at most 3
    assert (c.tmp / "data" / "projects" / pid / "files" / rep["report"]).is_file()
    prompt = [json.loads(l) for l in (c.tmp / "prompts.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    assert "1:08" in json.dumps(prompt) and "scrolling isotope table" in json.dumps(prompt)
    cands = lessons(c, pid, "candidate")
    assert len(cands) == 3 and not lessons(c, pid, "active")                        # nothing used yet
    assert all(l["evidence"]["type"] == "retention_insight" for l in cands)
    cut = next(l for l in cands if l["title"] == "Cut list-style passages")
    assert cut["evidence"]["drop"] == "1:10"
    c.post(f"/api/projects/{pid}/learning/{cut['id']}/approve")
    other = next(l for l in cands if l["title"] == "Show the star sooner")
    c.post(f"/api/projects/{pid}/learning/{other['id']}/forget")
    assert [l["title"] for l in lessons(c, pid, "active")] == ["Cut list-style passages"]
    # the same analysis again does not resurrect the forgotten lesson
    c.post(f"/api/projects/{pid}/insights", json={"retention": "retention.csv"})
    assert "Show the star sooner" not in [l["title"] for l in lessons(c, pid) if l["state"] != "forgotten"]


def test_rule_based_without_an_account_or_when_the_ai_fails(client, monkeypatch):
    c = client
    pid = episode(c)
    out = c.post(f"/api/projects/{pid}/insights", json={"retention": "retention.csv"}).json()
    assert out["drafted_by"] == "rules" and "No signed-in account" in out["note"]
    assert out["lessons"][0]["title"] == "Viewers left at 1:08" and "Isotopes" in out["lessons"][0]["text"]
    signed_in(c, "claude")
    monkeypatch.setenv("FAKE_REPLY", "I think the video was great overall.")
    out = c.post(f"/api/projects/{pid}/insights", json={"retention": "retention.csv"}).json()
    assert out["drafted_by"] == "rules" and "no usable lessons" in out["note"]


def test_errors(client):
    c = client
    pid = c.post("/api/projects", json={"name": "Empty"}).json()["id"]
    assert "no finished episode" in c.post(f"/api/projects/{pid}/insights", json={"retention": "x.csv"}).json()["detail"]
    pid = episode(c, "Flat")
    flat = "Video position (%),Absolute audience retention (%)\n" + "\n".join(f"{p},{90 - p * 0.1:.1f}" for p in range(101))
    (c.tmp / "data" / "projects" / pid / "files" / "flat.csv").write_text(flat, encoding="utf-8")
    assert "not a file" in c.post(f"/api/projects/{pid}/insights", json={"retention": "nope.csv"}).json()["detail"]
    out = c.post(f"/api/projects/{pid}/insights", json={"retention": "flat.csv"}).json()
    assert out["report"]["drops"] == [] and out["lessons"] == [] and "No clear drop" in out["note"]
    assert lessons(c, pid) == []
