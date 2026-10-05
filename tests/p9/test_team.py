"""P9 team mode, driven with stand-in accounts and agents (no real calls, no real clock).

Plan gate (P9 AUTO): 3 workers, limit forced on one account -> re-pins and finishes; no eligible
account -> pauses, resumes after a mocked reset; two workers never write one file; the third
review round escalates instead of looping; a spend cap pauses.
"""
import asyncio
import json

import pytest

from app.accounts import switch_allowed
from app.agent.runs import Run
from app.agent.team import Locks, Team, TeamConfig
from app.providers.base import Fail, Usage


def acc(id_, provider, order):
    return {"id": id_, "provider": provider, "label": id_, "order": order, "enabled": True, "status": "ok",
            "model": None, "effort": None}


class Deps:
    """Stand-in for the app: accounts, a clock, and scripted master / worker turns."""

    def __init__(self, tmp_path, accounts, tasks, *, review=None, worker=None):
        self.accs = accounts
        self.t = 1000.0
        self.tasks = tasks                          # the master's plan
        self.review = review or (lambda task_title, n: {"verdict": "pass"})
        self.worker = worker                        # async (deps, acc, sub, prompt) -> res | None
        self.calls = []                             # (role, account, model, effort, prompt)
        self.limit_on = {}                          # account -> how many worker turns fail with a limit
        self.caps = {}                              # account -> over-cap reason (after planning)
        self.planned = False
        self.task_caps = {}
        self.reviews = {}
        self.running = 0
        self.peak = 0
        self.gpu_running = 0
        self.gpu_peak = 0
        self.dir = tmp_path / "handoffs"

    # what Team asks for
    def accounts(self):
        return [a for a in self.accs if a["enabled"]]

    def now(self):
        return self.t

    def resets_at(self, a):
        return self.t + 3600

    def over_cap(self, a):
        return self.caps.get(a["id"]) if self.planned else None

    def task_cap(self, a):
        return self.task_caps.get(a["id"])

    switch_allowed = staticmethod(switch_allowed)

    def usage_pct(self, a):
        return None

    def handoff_dir(self):
        return self.dir

    @staticmethod
    def redact(text):
        return text.replace("sk-ant-api03-SECRET", "[REDACTED:api_key]")

    async def call(self, a, sub, prompt, note, model, effort, role):
        self.calls.append((role, a["id"], model, effort, prompt))
        await asyncio.sleep(0)
        if role == "master":
            if "Split this into" in prompt:
                self.planned = True
                return ok(json.dumps({"tasks": self.tasks}), 500, 200)
            if "Review this finished task" in prompt:
                title = prompt.split("Task: ", 1)[1].split("\n", 1)[0]
                n = self.reviews[title] = self.reviews.get(title, 0) + 1
                return ok(json.dumps(self.review(title, n)), 3000, 300)
            return ok("Final review: all good.", 800, 200)
        self.running += 1
        self.peak = max(self.peak, self.running)
        gpu = "voiceover" in prompt
        if gpu:
            self.gpu_running += 1
            self.gpu_peak = max(self.gpu_peak, self.gpu_running)
        try:
            if self.worker:
                res = await self.worker(self, a, sub, prompt)
                if res is not None:
                    return res
            await asyncio.sleep(0.02)
            if self.limit_on.get(a["id"]):
                self.limit_on[a["id"]] -= 1
                return {**ok("half of the work sk-ant-api03-SECRET", 100, 50),
                        "error": Fail("limit", "usage limit reached", interrupted=True)}
            for f in files_in(prompt):
                why = sub.lock_check("Write", {"file_path": f})
                assert why is None, why
            return ok("Done: wrote " + ", ".join(files_in(prompt)), 1000, 400)
        finally:
            self.running -= 1
            if gpu:
                self.gpu_running -= 1


def ok(text, i, o):
    return {"text": text, "usage": Usage(i, o, cost_usd=0.01), "error": None, "windows": None, "steps": []}


def files_in(prompt):
    for line in prompt.splitlines():
        if line.startswith("Files for you to write: "):
            return [f.strip() for f in line.split(": ", 1)[1].split(",")]
    return []


def task(title, *files, gpu=False):
    return {"title": title, "brief": f"do {title}" + (" voiceover" if gpu else ""), "files": list(files), "gpu": gpu}


def make(tmp_path, deps, **cfg):
    root = tmp_path / "files"
    root.mkdir(exist_ok=True)
    run = Run("p1", root)
    team = Team("p1", "Make episode 1", run, deps, TeamConfig(**{"poll": 0.05, **cfg}), state_file=tmp_path / "team.json")
    return team, run


def go(team):
    return asyncio.run(team.go())


THREE = [task("Script", "script.md"), task("Shot list", "shots.md"), task("Description", "desc.md")]


def test_three_workers_in_parallel_master_on_opus_workers_on_sonnet(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1), acc("api-a", "anthropic_api", 2)],
                THREE)
    team, run = make(tmp_path, deps)
    snap = go(team)
    assert snap["state"] == "done"
    assert [t["status"] for t in snap["tasks"]] == ["done"] * 3
    assert deps.peak == 3                                     # really in parallel
    assert {t["account"] for t in snap["tasks"]} == {"claude-a", "codex-a", "api-a"}   # spread by load
    master = [c for c in deps.calls if c[0] == "master"]
    assert {c[1] for c in master} == {"claude-a"}             # a Claude account leads
    assert {(c[2], c[3]) for c in master} == {("opus", "high")}
    workers = {c[1]: (c[2], c[3]) for c in deps.calls if c[0] == "worker"}
    assert workers["claude-a"] == ("sonnet", "medium") and workers["api-a"] == ("sonnet", "medium")
    assert workers["codex-a"] == (None, None)                 # Codex keeps the account's own picks
    assert snap["final"].startswith("Final review")
    assert json.loads((tmp_path / "team.json").read_text())["state"] == "done"
    assert any(e["t"] == "team" for e in run.events)
    assert snap["locks"] == {}                                # everything released


def test_never_more_than_max_workers(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)],
                [task(f"Part {n}", f"p{n}.md") for n in range(6)])
    team, _ = make(tmp_path, deps)
    snap = go(team)
    assert all(t["status"] == "done" for t in snap["tasks"]) and deps.peak == 3


def test_limit_repins_to_another_service_with_a_handoff(tmp_path):
    accounts = [acc("claude-a", "claude", 0), acc("claude-b", "claude", 1), acc("codex-a", "codex", 2)]
    deps = Deps(tmp_path, accounts, THREE)
    deps.limit_on["claude-a"] = 1
    team, run = make(tmp_path, deps)
    snap = go(team)
    assert [t["status"] for t in snap["tasks"]] == ["done"] * 3
    moved = [t for t in snap["tasks"] if len(t["pins"]) == 2]
    assert len(moved) == 1
    t = moved[0]
    assert t["pins"][0] == "claude-a" and t["pins"][1] == "codex-a"     # never Claude login -> Claude login
    assert t["handoffs"] == [f"team-{t['id']}-1.md"]
    text = (deps.dir / t["handoffs"][0]).read_text(encoding="utf-8")
    assert "half of the work" in text and "[REDACTED:api_key]" in text and "sk-ant-api03" not in text
    resumed = [c for c in deps.calls if c[0] == "worker" and c[1] == "codex-a" and "hit its usage limit" in c[4]]
    assert resumed and "half of the work" in resumed[0][4]                 # continues, doesn't restart
    assert any(e["t"] == "team_note" and "re-pinned to codex-a" in e["text"] for e in run.events)


def test_no_eligible_account_pauses_then_resumes_after_reset(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")])
    deps.limit_on["claude-a"] = 1
    team, run = make(tmp_path, deps, poll=30)

    async def scenario():
        job = asyncio.create_task(team.go())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if team.state == "paused":
                break
        assert team.state == "paused"
        assert "no account can take 'Script'" in team.paused_why
        assert team.resets_at == deps.t + 3600
        assert team.tasks[0].status == "paused" and team.locks.owner == {}
        deps.t += 3601                                        # the limit resets
        team.resume()
        return await asyncio.wait_for(job, 5)

    snap = asyncio.run(scenario())
    assert snap["state"] == "done" and snap["tasks"][0]["status"] == "done"
    assert snap["tasks"][0]["pins"] == ["claude-a"]
    assert any(e["t"] == "team" and e["team"]["state"] == "paused" for e in run.events)


def test_paused_team_finds_a_new_account_on_its_own(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")])
    deps.limit_on["claude-a"] = 1
    team, _ = make(tmp_path, deps, poll=0.05)

    async def scenario():
        job = asyncio.create_task(team.go())
        while team.state != "paused":
            await asyncio.sleep(0.01)
        deps.accs.append(acc("codex-a", "codex", 1))          # the user adds an account
        return await asyncio.wait_for(job, 5)

    snap = asyncio.run(scenario())
    assert snap["tasks"][0]["pins"] == ["claude-a", "codex-a"]


def test_two_workers_never_write_the_same_file(tmp_path):
    """Declared overlap: they take turns. Undeclared: the second write is refused."""
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)],
                [task("Intro", "script.md"), task("Outro", "script.md"), task("Notes", "notes.md")])
    together = []

    async def worker(d, a, sub, prompt):
        mine = files_in(prompt)
        together.append(set(mine))
        await asyncio.sleep(0.03)
        for f in mine:
            assert sub.lock_check("Write", {"file_path": f}) is None
        return ok("ok", 10, 10)

    deps.worker = worker
    team, _ = make(tmp_path, deps)
    snap = go(team)
    assert all(t["status"] == "done" for t in snap["tasks"])
    assert deps.peak == 2                        # Intro + Notes, then Outro - never Intro + Outro together

    locks = Locks(tmp_path)
    assert locks.claim("t1", "Script.md") is None
    assert locks.claim("t2", str(tmp_path / "script.md")) == "t1"     # same file, any spelling
    assert locks.claim("t1", "script.md") is None
    locks.release("t1")
    assert locks.claim("t2", "script.md") is None


def test_undeclared_write_to_a_held_file_is_refused(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)],
                [task("A", "a.md"), task("B", "b.md")])
    refused = []

    async def worker(d, a, sub, prompt):
        if "Your task: A" in prompt:
            assert sub.lock_check("Write", {"file_path": "shared.md"}) is None
            await asyncio.sleep(0.05)
        else:
            await asyncio.sleep(0.01)
            refused.append(sub.lock_check("Edit", {"file_path": "SHARED.md"}))
            assert sub.lock_check("Read", {"file_path": "shared.md"}) is None      # reading is fine
        return ok("ok", 10, 10)

    deps.worker = worker
    snap = go(make(tmp_path, deps)[0])
    assert refused and "another worker ('A') is writing" in refused[0]
    assert all(t["status"] == "done" for t in snap["tasks"])


def test_master_never_edits(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("A", "a.md")])
    seen = []
    orig = deps.call

    async def call(a, sub, prompt, note, model, effort, role):
        if role == "master":
            seen.append(sub.lock_check("Write", {"file_path": "a.md"}))
        return await orig(a, sub, prompt, note, model, effort, role)

    deps.call = call
    go(make(tmp_path, deps)[0])
    assert seen and all(s and "reviews only" in s for s in seen)


def test_third_review_round_escalates_instead_of_looping(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")],
                review=lambda title, n: {"verdict": "revise", "notes": f"tighter intro (round {n})"})
    snap = go(make(tmp_path, deps)[0])
    t = snap["tasks"][0]
    assert t["status"] == "needs_you" and t["rounds"] == 2
    assert "after 2 review rounds" in t["why"]
    workers = [c for c in deps.calls if c[0] == "worker"]
    assert len(workers) == 2                                  # first try + one revision, no third
    assert "tighter intro (round 1)" in workers[1][4]         # the notes reach the worker


def test_revise_then_pass(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")],
                review=lambda title, n: {"verdict": "revise" if n == 1 else "pass", "notes": "fix"})
    snap = go(make(tmp_path, deps)[0])
    assert snap["tasks"][0]["status"] == "done" and snap["tasks"][0]["rounds"] == 2


def test_review_token_budget_escalates(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")],
                review=lambda title, n: {"verdict": "revise", "notes": "more"})
    orig = deps.call

    async def call(a, sub, prompt, note, model, effort, role):
        res = await orig(a, sub, prompt, note, model, effort, role)
        if "Review this finished task" in prompt:
            res["usage"] = Usage(45_000, 2_000)                 # over the 40k-in round budget
        return res

    deps.call = call
    snap = go(make(tmp_path, deps)[0])
    t = snap["tasks"][0]
    assert t["status"] == "needs_you" and t["rounds"] == 1 and "over its budget" in t["why"]


def test_long_worker_report_is_cut_to_the_review_budget(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")])

    async def worker(d, a, sub, prompt):
        return ok("x" * 500_000 + "THE END", 10, 10)

    deps.worker = worker
    go(make(tmp_path, deps)[0])
    review = next(c[4] for c in deps.calls if "Review this finished task" in c[4])
    assert len(review) < 40_000 * 4 and "THE END" in review and "cut to fit" in review


def test_daily_spend_cap_pauses_and_asks(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md")])
    team, _ = make(tmp_path, deps, poll=30)

    deps.caps["claude-a"] = "daily cap of 1,000 tokens reached"         # hit right after planning

    async def scenario():
        job = asyncio.create_task(team.go())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if team.state == "paused":
                break
        assert team.state == "paused" and "spend cap reached" in team.paused_why
        assert "raise it on the Usage page" in team.paused_why
        deps.caps.clear()                                   # the user raised the cap
        team.resume()
        return await asyncio.wait_for(job, 5)

    snap = asyncio.run(scenario())
    assert snap["tasks"][0]["status"] == "done"


def test_per_task_spend_cap_stops_that_task(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("Script", "script.md"), task("Desc", "d.md")])
    deps.task_caps["claude-a"] = 1000                       # a worker turn uses 1,400
    snap = go(make(tmp_path, deps, max_workers=1)[0])
    assert {t["status"] for t in snap["tasks"]} == {"needs_you"}
    assert "spend cap per task" in snap["tasks"][0]["why"]


def test_gpu_tasks_run_one_at_a_time(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1), acc("api-a", "anthropic_api", 2)],
                [task("VO 1", "a.md", gpu=True), task("VO 2", "b.md", gpu=True), task("Desc", "c.md")])
    snap = go(make(tmp_path, deps)[0])
    assert all(t["status"] == "done" for t in snap["tasks"])
    assert deps.gpu_peak == 1 and deps.peak >= 2


def test_stop_ends_everything_and_releases_files(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)], THREE)

    async def worker(d, a, sub, prompt):
        for f in files_in(prompt):
            sub.lock_check("Write", {"file_path": f})
        await asyncio.sleep(10)

    deps.worker = worker
    team, run = make(tmp_path, deps)

    async def scenario():
        job = asyncio.create_task(team.go())
        while not team.locks.owner:
            await asyncio.sleep(0.01)
        run.stopped = True
        job.cancel()
        return await asyncio.wait_for(job, 5)

    snap = asyncio.run(scenario())
    assert snap["state"] == "stopped" and snap["locks"] == {}
    assert {t["status"] for t in snap["tasks"]} == {"stopped"}


def test_unreadable_plan_becomes_one_task(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [])
    snap = go(make(tmp_path, deps)[0])
    assert len(snap["tasks"]) == 1 and snap["tasks"][0]["brief"] == "Make episode 1"


def test_plan_files_outside_the_project_are_dropped(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0)], [task("A", "a.md", "C:/Windows/evil.txt", "../up.md")])
    snap = go(make(tmp_path, deps)[0])
    assert snap["tasks"][0]["files"] == ["a.md"]


def test_master_moves_when_its_account_hits_a_limit(tmp_path):
    deps = Deps(tmp_path, [acc("claude-a", "claude", 0), acc("codex-a", "codex", 1)], [task("A", "a.md")])
    orig = deps.call
    state = {"failed": False}

    async def call(a, sub, prompt, note, model, effort, role):
        if role == "master" and a["id"] == "claude-a" and "Review this" in prompt and not state["failed"]:
            state["failed"] = True
            return {**ok("", 0, 0), "error": Fail("limit", "usage limit reached")}
        return await orig(a, sub, prompt, note, model, effort, role)

    deps.call = call
    snap = go(make(tmp_path, deps)[0])
    assert snap["master"] == "codex-a" and snap["tasks"][0]["status"] == "done"
