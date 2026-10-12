"""Draft episode (owner, 2026-10-11: "first it should draft completely for first level of review"):
no stops for the owner - failed gates and doubts become flags in the review report, visuals are
planned from the competitor style, scripted in batches with no cards for this episode's own shots,
and the app renders the timeline. Only an impossible step stops the run.

Stand-in AI turns, voice job, Resolve and render; the real Fusion checks, gates and packaging."""
import asyncio
import json
import re

import pytest

from app.agent import episode as episode_mod
from app.agent.episode import EpisodeConfig, EpisodeRun
from app.agent.runs import Run
from tests.p10.media_fixtures import ff, needs_ffmpeg, video
from tests.p10.test_episode import ART, Deps as EpisodeDeps, SCRIPT, ok, run, setup, status  # noqa: F401 - fixture

pytestmark = needs_ffmpeg
MOTION = "def slam_in(S, text):\n    return S.pop(S.text(text, 0.5, 0.5, 0.08, S.c('ink')), 0.5, 0.5, 0)\n"
PROFILE = {"summary": "Fast kinetic type", "do": ["cut on claims"], "dont": ["slow fades"],
           "measured": {"pace": {"shot_median": 6.0, "first_30s_cuts_per_min": 14}}}


class Deps(EpisodeDeps):
    def __init__(self, *a, render_fails=False, motion=MOTION, **kw):
        super().__init__(*a, **kw)
        self.render_fails, self.motion = render_fails, motion
        self.auto_ran, self.renders = [], []

    def style_profiles(self, ids):
        return [{"id": i, "name": "Rival fast", "profile": PROFILE} for i in ids]

    def render_start(self, state, name):
        self.renders.append(name)
        return {"job": "j1", "file": name, "polls": 0}

    def render_status(self, state, job):
        job["polls"] += 1
        if self.render_fails:
            return {"failed": True, "error": "Render job failed: disk full"}
        if job["polls"] < 2:
            return {"progress": 50}
        video(self.files / job["file"], self.files / "vo.wav", 24.0)
        return {"done": True, "progress": 100}

    async def turn(self, deps, a, sub, prompt):
        base = self.files / "work" / "ep-1"
        if "Plan the visuals" in prompt:
            self.visual_turns += 1
            base.mkdir(parents=True, exist_ok=True)
            (base / "art.json").write_text(json.dumps(ART), encoding="utf-8")
            (base / "motion.py").write_text(self.motion, encoding="utf-8")
            shots = [{"idea": "title card"}] + [{"anchor": x, "idea": f"shot about {x}"} for x in self.anchors]
            (base / "shots.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")
            await asyncio.to_thread(self.fusion.setup, self.pid, "work/ep-1/shots.json", "work/ep-1/art.json",
                                    "vo.wav", "vo.srt", "", "Ep 1")
            return ok("Planned.", 3000, 2000)
        if "Build these shots" in prompt:
            for name in re.findall(r"^- (shot-\d{3}) ", prompt, re.M):
                script = f'from op_kit import *\nfrom ep_motion import *\nS = begin("{name}")\nresult = finish(S, slam_in(S, "X"))\n'
                assert sub.shot_scripts is not None
                done = sub.shot_scripts.auto(script)            # what the agent's permission callback does
                assert done is not None, "an own shot must run without a card in a draft"
                self.auto_ran.append(name)
                self.resolve.built.add(name)
            return ok("Built.", 4000, 3000)
        if "Check the visuals" in prompt:
            return ok("All good.", 1000, 500)
        return await super().turn(deps, a, sub, prompt)


def make(tmp_path, store, pid, deps, styles=("s1",), **cfg):
    spec = {"name": "Ep 1", "script": "script.md", "voice": "ref.wav", "voice_job": "", "draft": True, "styles": list(styles)}
    return EpisodeRun(pid, spec, Run(pid, store.files_dir(pid)), deps, EpisodeConfig(poll=0.01, **cfg),
                      state_file=tmp_path / "episode.json")


def test_a_draft_runs_to_the_end_with_no_stops(tmp_path, setup, monkeypatch):
    monkeypatch.setattr(episode_mod, "BATCH", 2)
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    snap = run(make(tmp_path, store, pid, deps).go())
    assert snap["state"] == "done", (snap["result"], snap["flags"], [s["detail"] for s in snap["stages"]])
    assert set(status(snap).values()) <= {"passed", "passed_with_notes"}
    # visuals: one plan turn with the competitor style, shot turns of BATCH shots, then a check turn
    workers = [c[4] for c in deps.calls if c[0] == "worker"]
    plan = workers[0]
    assert "Plan the visuals" in plan and "work/ep-1/style/1-rival-fast.json" in plan and "shots of about 6 s" in plan
    assert "motion.py" in plan and "14 cuts per minute" in plan
    assert [w.splitlines()[0][:22] for w in workers[1:3]] == ["Build these shots of t"] * 2
    assert sum("Build these shots" in w for w in workers) == 2 and "Check the visuals" in workers[3]
    assert deps.auto_ran == ["shot-001", "shot-002", "shot-003", "shot-004"]
    scripts = store.files_dir(pid) / "work" / "ep-1" / "scripts"
    assert sorted(p.name for p in scripts.glob("*.py")) == [f"shot-00{n}.py" for n in range(1, 5)]
    # the app rendered the draft itself and checked it; then the package
    assert deps.renders and deps.renders[0].startswith("work/ep-1/draft-") and snap["vo"]["video"] == deps.renders[0]
    assert status(snap)["edit"] == "passed" and status(snap)["package"] == "passed"
    assert "DRAFT" in [c[4] for c in deps.calls if c[0] == "master"][-1]           # the review report
    assert snap["vo"]["pace"]["target"] == 6.0 and snap["draft"] is True


def test_failed_gates_and_doubts_become_flags_not_stops(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, wer=0.2, title="x" * 120,
                review=lambda title, n: {"verdict": "revise", "notes": "the hook shot is too busy"})
    snap = run(make(tmp_path, store, pid, deps, gate_rounds=2).go())
    assert snap["state"] == "done", snap["result"]
    st = status(snap)
    assert st["voice"] == "passed_with_notes" and st["visuals"] == "passed_with_notes" and st["package"] == "passed_with_notes"
    why = " | ".join(f["why"] for f in snap["flags"])
    assert "read-check" in why and "the hook shot is too busy" in why and "title length" in why
    assert "Flagged for review" in [c[4] for c in deps.calls if c[0] == "master"][-1]


def test_a_broken_motion_library_is_flagged_and_shots_use_the_kit(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid, motion="import os\n")
    original = deps.turn

    async def kit_only(d, a, sub, prompt):
        if "Build these shots" in prompt:
            assert "ep_motion" not in prompt                   # the worker is told to use the kit only
            for name in re.findall(r"^- (shot-\d{3}) ", prompt, re.M):
                assert sub.shot_scripts.auto(f'from op_kit import *\nS = begin("{name}")\n') is not None
                deps.resolve.built.add(name)
            return ok("Built.", 10, 10)
        return await original(d, a, sub, prompt)
    deps.worker = kit_only
    snap = run(make(tmp_path, store, pid, deps, gate_rounds=2).go())
    assert snap["state"] == "done" and any("motion.py" in f["why"] for f in snap["flags"])


def test_only_impossible_steps_stop_a_draft(tmp_path, setup):
    store, pid = setup
    off = Deps(tmp_path, store, pid, ready=(False, "The Resolve MCP switch is off"))
    snap = run(make(tmp_path, store, pid, off).go())
    assert snap["state"] == "needs_you" and snap["result"]["stage"] == "visuals"
    other = store.create_project("Other")["id"]
    (store.files_dir(other) / "script.md").write_text(SCRIPT, encoding="utf-8")
    from tests.p10.media_fixtures import voice
    voice(store.files_dir(other) / "ref.wav", 120, 10)
    broken = Deps(tmp_path, store, other, render_fails=True)
    snap = run(make(tmp_path, store, other, broken).go())
    assert snap["state"] == "needs_you" and snap["result"]["stage"] == "edit" and "disk full" in snap["result"]["why"]


def rich(path):
    """A detailed still (test pattern): many colour shades, like a rendered frame."""
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=1:duration=1", "-frames:v", "1", path)
    return path


class QADeps(Deps):
    """Competitor frames available; stills are rich (or flat), the check turn writes qa.json."""

    def __init__(self, tmp_path, *a, our_stills="rich", scores=None, **kw):
        super().__init__(tmp_path, *a, **kw)
        refs = tmp_path / "competitor"
        refs.mkdir(exist_ok=True)
        self.refs = [str(rich(refs / f"f{n}.jpg")) for n in range(4)]
        self.our_stills, self.scores, self.checks = our_stills, scores or [{}], 0
        original = self.resolve.call

        def call(request, timeout):
            out = original(request, timeout)
            for p in out.get("stills") or []:
                rich(p) if self.our_stills == "rich" else None
            return out
        self.resolve.call = call

    def style_profiles(self, ids):
        return [{**s, "frames": self.refs} for s in super().style_profiles(ids)]

    async def turn(self, deps, a, sub, prompt):
        if "Check the visuals" in prompt:
            self.checks += 1
            override = self.scores[min(self.checks, len(self.scores)) - 1]
            shots = [f"shot-00{n}" for n in range(1, 5)]
            (self.files / "work" / "ep-1" / "qa.json").write_text(json.dumps({"shots": [
                {"shot": s, "score": override.get(s, 8), "why": "too empty" if override.get(s, 8) < 7 else "ok"} for s in shots]}),
                encoding="utf-8")
            return ok("Checked.", 1000, 500)
        return await super().turn(deps, a, sub, prompt)


def test_shots_under_the_qa_bar_go_back_once_then_pass(tmp_path, setup):
    store, pid = setup
    deps = QADeps(tmp_path, store, pid, scores=[{"shot-002": 5}, {}])
    snap = run(make(tmp_path, store, pid, deps).go())
    assert snap["state"] == "done", snap["flags"]
    checks = [c[4] for c in deps.calls if c[0] == "worker" and "Check the visuals" in c[4]]
    assert len(checks) == 2 and "scored under 7: shot-002 (5: too empty)" in checks[1]
    assert "Quality bar - the competitor" in checks[0] and "qa.json" in checks[0]
    sheets = sorted((store.files_dir(pid) / "work" / "ep-1" / "qa").glob("qa-*.jpg"))
    assert sheets and len(list((store.files_dir(pid) / "work" / "ep-1" / "style" / "refs").glob("*.jpg"))) == 4
    assert snap["vo"]["qa"]["scores"]["shot-002"] == 8 and snap["vo"]["qa"]["flat"] == []


def test_flat_shots_are_named_against_the_competitor_and_flagged(tmp_path, setup):
    store, pid = setup
    deps = QADeps(tmp_path, store, pid, our_stills="flat")
    snap = run(make(tmp_path, store, pid, deps, gate_rounds=2).go())
    assert snap["state"] == "done" and status(snap)["visuals"] == "passed_with_notes"
    why = " | ".join(f["why"] for f in snap["flags"])
    assert "measure flat next to the competitor" in why and "shot-001" in why and "generated asset" in why


def test_a_missing_qa_file_is_asked_for(tmp_path, setup):
    store, pid = setup
    deps = QADeps(tmp_path, store, pid)
    original = deps.turn

    async def no_qa_first(d, a, sub, prompt):
        if "Check the visuals" in prompt and deps.checks == 0:
            deps.checks += 1
            return ok("Checked, but forgot the file.", 10, 10)
        return await original(d, a, sub, prompt)
    deps.worker = no_qa_first
    snap = run(make(tmp_path, store, pid, deps).go())
    checks = [c[4] for c in deps.calls if c[0] == "worker" and "Check the visuals" in c[4]]
    assert snap["state"] == "done" and "write work/ep-1/qa.json" in checks[1]


def test_a_failed_render_is_retried_without_redoing_the_shots(tmp_path, setup):
    """Owner's first real draft (2026-10-11): 37 shots built, then Resolve refused the render settings."""
    store, pid = setup
    deps = Deps(tmp_path, store, pid, render_fails=True)
    snap = run(make(tmp_path, store, pid, deps).go())
    assert snap["state"] == "needs_you" and snap["result"]["stage"] == "edit"
    built = list(deps.auto_ran)
    deps.render_fails = False
    saved = json.loads((tmp_path / "episode.json").read_text(encoding="utf-8"))
    again = EpisodeRun.from_snapshot(pid, saved, Run(pid, store.files_dir(pid)), deps, EpisodeConfig(poll=0.01),
                                     state_file=tmp_path / "episode.json")
    done = run(again.go(saved["result"]["stage"]))
    assert done["state"] == "done" and done["draft"] and status(done)["edit"] == "passed"
    assert deps.auto_ran == built                                              # no shot was scripted again
    assert done["flags"] == snap["flags"]


def test_retry_route_needs_a_stopped_episode(client):
    pid = client.post("/api/projects", json={"name": "Ep"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/episode/retry").status_code == 409


def test_a_draft_stopped_at_its_render_takes_the_owners_own_render(client, monkeypatch):
    """The owner rendered the timeline in Resolve while the draft was stopped at the render stage."""
    pid = client.post("/api/projects", json={"name": "Ep"}).json()["id"]
    client.post(f"/api/projects/{pid}/files", files={"files": ("r.mp4", b"0")})
    state = client.app.state                                                     # write a stopped draft snapshot
    proj = client.data_dir / "projects" / pid
    snap = {"kind": "episode", "id": "x", "state": "needs_you", "spec": {"name": "Ep", "script": "s.md", "draft": True},
            "stages": [], "result": {"stage": "visuals", "why": "x"}, "vo": {}, "config": {}}
    (proj / "episode.json").write_text(json.dumps(snap), encoding="utf-8")
    r = client.post(f"/api/projects/{pid}/episode/render", json={"video": "r.mp4"})
    assert r.status_code == 409                                                  # stopped somewhere else: no
    assert state is not None


def test_no_style_chosen_falls_back_to_the_script_alone(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    snap = run(make(tmp_path, store, pid, deps, styles=()).go())
    plan = next(c[4] for c in deps.calls if c[0] == "worker")
    assert snap["state"] == "done" and "No competitor style was chosen" in plan and "shots of about 3.5 s" in plan
    assert "stock_search" not in plan                                       # no Pexels key: no footage


def test_preflight_and_route_checks(client):
    pid = client.post("/api/projects", json={"name": "Ep"}).json()["id"]
    pre = client.get(f"/api/projects/{pid}/episode/preflight").json()
    ids = {c["id"]: c for c in pre["checks"]}
    assert pre["ready"] is False and not ids["switches"]["ok"] and not ids["claude"]["ok"]
    assert ids["pexels"]["optional"] and ids["sfx"]["optional"]
    client.post(f"/api/projects/{pid}/files", files={"files": ("s.md", b"## A\nHello there.")})
    r = client.post(f"/api/projects/{pid}/episode", json={"name": "E", "script": "s.md", "voice": "v", "draft": True,
                                                         "styles": ["gone"]})
    assert r.status_code == 400


def test_with_a_pexels_key_the_plan_may_use_footage(tmp_path, setup):
    store, pid = setup
    deps = Deps(tmp_path, store, pid)
    deps.stock_ready = lambda: True
    run(make(tmp_path, store, pid, deps).go())
    plan = next(c[4] for c in deps.calls if c[0] == "worker")
    assert "stock_fetch" in plan and '"from": seconds into the clip' in plan
