"""Script Studio loop with stand-in accounts: style guide -> script -> Opus review, until it passes
or a stop rule says "your call" (round cap, no better for 2 rounds, review budget)."""
import asyncio
import json
from pathlib import Path

from app import faceless
from app.agent.runs import Run
from app.agent.scriptflow import ScriptConfig, ScriptRun
from app.providers.base import Fail, Usage
from tests.p9.test_team import acc
from app.accounts import switch_allowed

RUBRIC = list(faceless.RUBRIC)


def card(score, must=()):
    s = score if isinstance(score, dict) else {k: score for k in RUBRIC}
    return {"text": json.dumps({"scores": s, "must_fix": list(must), "notes": f"notes for {score}",
                                "verdict": "revise"}), "usage": Usage(30_000, 900), "error": None, "windows": None}


class Deps:
    def __init__(self, tmp, accounts, reviews, *, transcripts=1):
        self.accs, self.t, self.reviews, self.calls = accounts, 1000.0, list(reviews), []
        self.limit_writer_on = None
        self.transcripts = transcripts
        self.dir = tmp / "handoffs"
        self.writes = 0

    def accounts(self): return self.accs
    def now(self): return self.t
    def resets_at(self, a): return self.t + 3600
    def over_cap(self, a): return None
    def task_cap(self, a): return None
    switch_allowed = staticmethod(switch_allowed)
    def usage_pct(self, a): return None
    def handoff_dir(self): return self.dir
    @staticmethod
    def redact(t): return t

    async def fetch(self, items, say):
        say("Transcript 1/1…")
        done = [{"title": "Lost city", "channel": "Mr Facts", "file": "mr-facts/lost-city.md", "wpm": 160, "minutes": 2,
                 "views": 5}] * self.transcripts
        return {"transcripts": done, "problems": [{"link": "x", "why": "no English captions"}],
                "summary": {"videos": len(done), "median_wpm": 160 if done else None}}

    async def call(self, a, sub, prompt, note, model, effort, role):
        self.calls.append({"role": role, "acc": a["id"], "model": model, "effort": effort, "prompt": prompt, "note": note})
        await asyncio.sleep(0)
        root = Path(sub.root)
        if role == "analyst":
            assert sub.lock_check("Write", {"file_path": "competitors/style-guide.md"}) is None
            (root / "competitors").mkdir(exist_ok=True)
            (root / "competitors" / "style-guide.md").write_text("Fast, curious, second person.", encoding="utf-8")
            return {"text": "Style guide written.", "usage": Usage(5000, 800), "error": None, "windows": None}
        if role == "writer":
            if self.limit_writer_on == a["id"]:
                self.limit_writer_on = None
                return {"text": "half a script", "usage": Usage(100, 50),
                        "error": Fail("limit", "usage limit reached"), "windows": None}
            self.writes += 1
            p = root / "scripts" / "the-lost-city.md"
            p.parent.mkdir(exist_ok=True)
            assert sub.lock_check("Write", {"file_path": str(p)}) is None
            p.write_text(f"# Draft {self.writes}\n[VISUAL: map]\n" + "word " * (100 * self.writes), encoding="utf-8")
            return {"text": "Script written.", "usage": Usage(20_000, 3000), "error": None, "windows": None}
        assert role == "reviewer"
        assert sub.lock_check("Write", {"file_path": "scripts/the-lost-city.md"})          # reviewer never edits
        return self.reviews.pop(0)


BRIEF = {"topic": "The Lost City", "format": "documentary", "minutes": 10, "niche": "history", "notes": ""}
SKILLS = {"analyst": "ANALYST-SKILLS", "writer": "WRITER-SKILLS", "reviewer": "REVIEWER-SKILLS"}


def make(tmp_path, deps, **cfg):
    root = tmp_path / "files"
    root.mkdir(exist_ok=True)
    run = Run("p1", root)
    sr = ScriptRun("p1", BRIEF, [{"kind": "video", "key": "x", "url": "u"}], run, deps,
                   ScriptConfig(**{"poll": 0.05, **cfg}), SKILLS, state_file=tmp_path / "script.json")
    return sr, run, root


def go(sr):
    return asyncio.run(sr.go())


def test_passes_first_round(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(9)])
    sr, run, root = make(tmp_path, deps)
    snap = go(sr)
    assert snap["state"] == "done" and snap["result"]["passed"] and snap["result"]["round"] == 1
    roles = [(c["role"], c["model"], c["effort"]) for c in deps.calls]
    assert roles == [("analyst", "sonnet", "medium"), ("writer", "sonnet", "medium"), ("reviewer", "opus", "high")]
    assert "WRITER-SKILLS" in deps.calls[1]["note"] and "REVIEWER-SKILLS" in deps.calls[2]["note"]
    assert "ANALYST-SKILLS" in deps.calls[0]["note"]
    assert "mr-facts/lost-city.md" in deps.calls[0]["prompt"]                      # analyst gets the transcripts
    assert "Fast, curious, second person." in deps.calls[1]["prompt"]                # writer gets the style guide
    assert "about 1600 spoken words" in deps.calls[1]["prompt"]                      # 10 min x 160 wpm
    assert '"anti_slop"' in deps.calls[2]["prompt"] and "# Draft 1" in deps.calls[2]["prompt"]
    assert snap["rounds"][0]["words"] == 100                                       # cues and headings not counted
    assert (root / "scripts" / "the-lost-city.v1.md").exists()
    assert snap["fetch"]["problems"] and json.loads((tmp_path / "script.json").read_text())["state"] == "done"
    assert any(e["t"] == "script" for e in run.events) and sr.locks.owner == {}


def test_loops_until_the_reviewer_is_satisfied(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)],
                [card(6, ["hook is slow"]), card(7, ["outro has no CTA"]), card(9)])
    snap = go(make(tmp_path, deps)[0])
    assert snap["state"] == "done" and snap["result"]["round"] == 3 and len(snap["rounds"]) == 3
    writers = [c["prompt"] for c in deps.calls if c["role"] == "writer"]
    assert "hook is slow" in writers[1] and "outro has no CTA" in writers[2]          # each round gets the card
    assert "Round 2: rewrite" in writers[1]


def test_one_low_score_or_a_must_fix_blocks_the_pass(tmp_path):
    almost = {k: 10 for k in RUBRIC} | {"authenticity": 7}
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(almost), card(9, ["one copied sentence"]), card(8)])
    snap = go(make(tmp_path, deps)[0])
    assert [r["passed"] for r in snap["rounds"]] == [False, False, True]


def test_round_cap_keeps_the_best_version(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(5), card(7), card(6)])
    sr, _, root = make(tmp_path, deps, rounds=3, stall=5)
    snap = go(sr)
    assert snap["state"] == "needs_you" and snap["result"]["round"] == 2 and not snap["result"]["passed"]
    assert "not passed after 3 rounds" in snap["result"]["why"]
    assert (root / "scripts" / "the-lost-city.md").read_text() == (root / "scripts" / "the-lost-city.v2.md").read_text()


def test_stops_when_it_stops_getting_better(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(7), card(6), card(7), card(9)])
    snap = go(make(tmp_path, deps, rounds=10, stall=2)[0])
    assert len(snap["rounds"]) == 3 and "no better after 2" in snap["result"]["why"]
    assert snap["result"]["round"] == 1


def test_review_budget_stops_the_loop(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(6), card(6), card(6)])
    snap = go(make(tmp_path, deps, stage_budget=50_000, stall=9)[0])
    assert len(snap["rounds"]) == 2 and "review budget used up" in snap["result"]["why"]


def test_unreadable_review_is_not_a_pass(tmp_path):
    bad = {"text": "Looks great to me!", "usage": Usage(100, 10), "error": None, "windows": None}
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [bad, card(9)])
    snap = go(make(tmp_path, deps)[0])
    assert snap["rounds"][0]["passed"] is False and snap["rounds"][0].get("unreadable")
    assert snap["result"]["passed"] and snap["result"]["round"] == 2


def test_writer_limit_moves_to_another_service_with_a_handoff(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)], [card(9)])
    deps.limit_writer_on = "claude-a"
    snap = go(make(tmp_path, deps)[0])
    t = next(x for x in snap["tasks"] if x["id"] == "script")
    assert t["pins"] == ["claude-a", "codex-a"] and t["handoffs"] == ["team-script-1.md"]
    resumed = [c for c in deps.calls if c["role"] == "writer"][-1]
    assert resumed["acc"] == "codex-a" and "half a script" in resumed["prompt"]
    assert resumed["model"] is None                                          # Codex keeps its own choice
    assert snap["state"] == "done"


def test_no_transcripts_means_no_ai_spent(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [], transcripts=0)
    snap = go(make(tmp_path, deps)[0])
    assert snap["state"] == "failed" and "no competitor transcript" in snap["result"]["why"] and deps.calls == []


def test_stop(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [card(5)] * 5)
    orig = deps.call

    async def slow(a, sub, prompt, note, model, effort, role):
        if role == "writer":
            await asyncio.sleep(10)
        return await orig(a, sub, prompt, note, model, effort, role)

    deps.call = slow
    sr, run, _ = make(tmp_path, deps)

    async def scenario():
        job = asyncio.create_task(sr.go())
        while sr.stage != "writing":
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        run.stopped = True
        job.cancel()
        return await asyncio.wait_for(job, 5)

    snap = asyncio.run(scenario())
    assert snap["state"] == "stopped" and sr.locks.owner == {}
