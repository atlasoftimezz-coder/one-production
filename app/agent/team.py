"""Team mode (plan R4, R10): a master plans and reviews, workers do the parts in parallel.

    master   Opus 5.5 (high effort) by default. Splits the goal into tasks, reviews every
             finished task, writes the final review. Reads files, never edits them.
    workers  Sonnet 5.5 (medium effort) by default, up to 3 at once. Each is pinned to one
             account; the scheduler spreads them by load and remaining headroom.

Rules that keep it safe and cheap:
  - two workers never write the same file: a worker claims a file on its first write and
    holds it until its task ends; a write to a file another worker holds is refused. Tasks
    whose declared files overlap don't start side by side.
  - GPU tasks (voiceovers) run one at a time.
  - a worker that hits a usage limit writes its own handoff, releases its files and is
    re-pinned to the next account it may move to (never Claude login -> Claude login while
    same-service switching is off). No account left -> the team pauses until one resets
    or the user presses Resume.
  - review: at most 2 rounds per task and a token budget (40k in / 8k out per round, 120k per
    task); whatever hits first escalates to the user instead of looping.
  - spend caps per account (tokens per task, tokens and dollars per day) pause and ask.
Everything the team needs from the app comes in through `deps`, so tests drive it with fakes.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import policy

TERMINAL = {"done", "needs_you", "failed", "stopped"}
SPAWN_TOOLS = {"Task", "Agent"}      # Claude Code's own sub-agents: not allowed inside a team
NO_SPAWN = ("team members don't start their own sub-agents - do the work yourself "
            "(the team already runs its own workers)")
CLAUDE_FAMILY = ("claude", "anthropic_api")


@dataclass
class TeamConfig:
    max_workers: int = 3
    review_rounds: int = 2
    review_in: int = 40_000          # tokens per review round
    review_out: int = 8_000
    stage_budget: int = 120_000      # review tokens per task, all rounds together
    max_tasks: int = 6
    master_account: str | None = None
    master_model: str = "opus"
    master_effort: str | None = "high"
    worker_model: str = "sonnet"
    worker_effort: str | None = "medium"
    poll: float = 60.0               # while paused: how often to look for an account again

    @classmethod
    def from_dict(cls, d: dict | None) -> "TeamConfig":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class Task:
    id: str
    title: str
    brief: str
    files: list[str] = field(default_factory=list)
    gpu: bool = False
    status: str = "queued"           # queued running review done paused needs_you failed stopped
    account: str | None = None
    pins: list[str] = field(default_factory=list)
    rounds: int = 0
    tokens: int = 0                  # worker tokens (all turns, all accounts)
    cost_usd: float = 0.0
    review_in: int = 0
    review_out: int = 0
    output: str = ""
    notes: str = ""                  # the master's last review notes
    why: str = ""                    # why it stopped / escalated
    resume: str = ""                 # partial work handed to the next account after a limit
    handoffs: list[str] = field(default_factory=list)
    activity: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d["output"] = self.output[-1500:]
        d.pop("resume")
        return d


class Locks:
    """File claims per project: path (case-insensitive, relative to the project) -> task id."""

    def __init__(self, root: Path):
        self.root = root
        self.owner: dict[str, str] = {}

    def key(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        try:
            rel = Path(os.path.realpath(p)).relative_to(Path(os.path.realpath(self.root)))
        except ValueError:
            rel = p
        return str(rel).replace("\\", "/").lower()

    def claim(self, task_id: str, path: str) -> str | None:
        """None when the task may write it, otherwise the task that holds it."""
        k = self.key(path)
        holder = self.owner.get(k)
        if holder and holder != task_id:
            return holder
        self.owner[k] = task_id
        return None

    def release(self, task_id: str) -> None:
        self.owner = {k: v for k, v in self.owner.items() if v != task_id}

    def held(self, task_id: str) -> list[str]:
        return sorted(k for k, v in self.owner.items() if v == task_id)


class SubRun:
    """What an agent sees as its run: events go to the team's run tagged with the task, approval
    cards are the team run's own (one Stop, one place to answer), writes go through the locks."""

    def __init__(self, parent, tag: str, write_check):
        self.parent, self.tag, self.write_check = parent, tag, write_check
        self.pid, self.root = parent.pid, parent.root
        self.pending, self.asks = parent.pending, parent.asks
        self.task = None

    @property
    def stopped(self) -> bool:
        return self.parent.stopped

    def emit(self, event: dict) -> None:
        self.parent.emit({**event, "w": self.tag})

    def lock_check(self, tool: str, tool_input: dict) -> str | None:
        return self.write_check(tool, tool_input)


class Stopped(Exception):
    pass


# ---------------------------------------------------------------- prompts

MASTER_NOTE = ("You are the MASTER of a small team in One Production. You plan and you review; you never "
               "edit or create files yourself (writes are refused). You may read any file in the project.")
WORKER_NOTE = ("You are a WORKER in a team led by a master reviewer. Do only your task. Other workers "
               "may be writing other files at the same time: a write to a file another worker holds is "
               "refused - then leave that file alone and say so. Finish with a short summary of what you "
               "did and which files you wrote.")


def plan_prompt(goal: str, max_tasks: int) -> str:
    return (f"Goal from the user:\n{goal}\n\n"
            f"Split this into at most {max_tasks} independent tasks that workers can do in parallel. "
            "Look at the project files first if that helps. Answer with ONLY a JSON object, no prose:\n"
            '{"tasks": [{"title": "short title", "brief": "everything the worker needs to know", '
            '"files": ["files this task will write, relative to the project"], '
            '"gpu": false}]}\n'
            "Set gpu to true only for a voiceover. Give each file to one task only. "
            "If the goal is small, one task is fine.")


def worker_prompt(goal: str, task: Task, others: list[Task]) -> str:
    lines = [f"Team goal: {goal}", "", f"Your task: {task.title}", task.brief]
    if task.files:
        lines += ["", "Files for you to write: " + ", ".join(task.files)]
    if others:
        lines += ["", "Other workers handle: " + "; ".join(f"{o.title} ({', '.join(o.files) or 'no files'})"
                                                           for o in others)]
    if task.resume:
        lines += ["", "Another account started this task and hit its usage limit. Continue from where it "
                      "stopped - do not start over. Its handoff:", task.resume]
    if task.notes:
        lines += ["", "The master reviewed your last attempt and asks for these changes:", task.notes]
    return "\n".join(lines)


def review_prompt(goal: str, task: Task, budget_chars: int) -> str:
    out = task.output
    if len(out) > budget_chars:
        out = "[... start cut to fit the review budget ...]\n" + out[-budget_chars:]
    files = ", ".join(task.files) or "none declared"
    return (f"Team goal: {goal}\n\nReview this finished task (round {task.rounds + 1}).\n"
            f"Task: {task.title}\nBrief: {task.brief}\nFiles: {files}\n\nWorker's report:\n{out}\n\n"
            "Read the files it wrote if you need to. Answer with ONLY a JSON object:\n"
            '{"verdict": "pass" or "revise", "notes": "what must change (empty when pass)"}')


def final_prompt(goal: str, tasks: list[Task]) -> str:
    rows = "\n".join(f"- {t.title}: {t.status}" + (f" ({t.why})" if t.why else "") for t in tasks)
    return (f"Team goal: {goal}\n\nAll tasks have ended:\n{rows}\n\nWrite the final review for the user in "
            "plain words: what was made (with file names), the quality, and anything they must decide or check. "
            "Keep it short.")


_JSON = re.compile(r"\{.*\}", re.S)


def parse_json(text: str) -> dict | None:
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def write_task_handoff(folder: Path, goal: str, task: Task, acc: dict, partial: str, reason: str,
                       redact) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    n = len(task.handoffs) + 1
    path = folder / f"team-{task.id}-{n}.md"
    text = "\n".join([
        f"# Worker handoff - {task.title}", "",
        f"- Reason: {reason}", f"- From account: {acc.get('label') or acc['id']} ({acc['provider']})",
        f"- Time: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"- Files this task holds: {', '.join(task.files) or 'none'}",
        "", "## Team goal", goal, "", "## Task", task.brief, "",
        "## Work so far (partial - continue from here, don't restart)", partial or "(nothing written yet)", "",
        "Any action that was in flight is unconfirmed - check the files before repeating it.", ""])
    path.write_text(redact(text), encoding="utf-8")
    return path


# ---------------------------------------------------------------- the team

class Team:
    def __init__(self, pid: str, goal: str, run, deps, config: TeamConfig, state_file: Path | None = None,
                 tasks: list[dict] | None = None):
        self.pid, self.goal, self.run, self.deps, self.cfg = pid, goal, run, deps, config
        self.id = secrets.token_hex(4)
        self.tasks: list[Task] = []
        self.locks = Locks(run.root)
        self.gpu = asyncio.Semaphore(1)
        config.max_workers = max(1, int(config.max_workers or 1))      # the user's number, no ceiling
        self.own_plan = [t for t in (tasks or []) if isinstance(t, dict)]   # parts the user wrote
        self.slots = asyncio.Semaphore(config.max_workers)
        self.changed = asyncio.Condition()
        self.wake = asyncio.Event()
        self.state = "planning"       # planning running paused reviewing done stopped
        self.paused_why = ""
        self.resets_at: float | None = None
        self.limited: dict[str, float] = {}        # account -> until when it is skipped
        self.load: dict[str, int] = {}             # account -> running workers
        self.master_acc: dict | None = None
        self.master_tokens = {"in": 0, "out": 0}
        self.final = ""
        self.planned_by = "user" if self.own_plan else "master"
        self.state_file = state_file
        self.started = time.time()

    # ------------------------------------------------ reporting
    def snapshot(self) -> dict:
        return {"id": self.id, "goal": self.goal, "state": self.state, "paused_why": self.paused_why,
                "resets_at": self.resets_at, "master": (self.master_acc or {}).get("id"),
                "config": asdict(self.cfg), "tasks": [t.public() for t in self.tasks], "planned_by": self.planned_by,
                "locks": dict(self.locks.owner), "master_tokens": self.master_tokens, "final": self.final,
                "started": self.started}

    def publish(self) -> None:
        snap = self.snapshot()
        self.run.emit({"t": "team", "team": snap})
        if self.state_file:
            try:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=1), encoding="utf-8")
                tmp.replace(self.state_file)
            except OSError:
                pass

    async def _changed(self) -> None:
        self.publish()
        async with self.changed:
            self.changed.notify_all()

    def notice(self, text: str) -> None:
        self.run.emit({"t": "team_note", "text": text})

    # ------------------------------------------------ accounts
    def _usable(self) -> list[dict]:
        now = self.deps.now()
        return [a for a in self.deps.accounts() if self.limited.get(a["id"], 0) <= now]

    def choice(self, acc: dict, role: str) -> tuple[str | None, str | None]:
        """(model, effort): role defaults on Claude accounts, the account's own picks on Codex."""
        if acc["provider"] in CLAUDE_FAMILY:
            if role == "master":
                return self.cfg.master_model, self.cfg.master_effort
            return self.cfg.worker_model, self.cfg.worker_effort
        return acc.get("model"), acc.get("effort")

    async def pause(self, why: str) -> None:
        if self.run.stopped:
            raise Stopped()
        resets = [t for t in self.limited.values() if t > self.deps.now()]
        self.state, self.paused_why = "paused", why
        self.resets_at = min(resets) if resets else None
        await self._changed()
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), self.cfg.poll)
        except asyncio.TimeoutError:
            pass
        if self.run.stopped:
            raise Stopped()
        if self.state == "paused":
            self.state, self.paused_why, self.resets_at = "running", "", None
            await self._changed()

    def resume(self) -> None:
        """The user pressed Resume (raised a cap, added an account, a limit reset)."""
        self.limited = {k: v for k, v in self.limited.items() if v > self.deps.now()}
        self.wake.set()

    def mark_limited(self, acc: dict) -> None:
        until = self.deps.resets_at(acc) or 0
        self.limited[acc["id"]] = max(until, self.deps.now() + self.cfg.poll)

    async def pick_worker(self, task: Task, moved_from: dict | None) -> dict:
        while True:
            if self.run.stopped:
                raise Stopped()
            cands = []
            capped = []
            for a in self._usable():
                why = self.deps.over_cap(a)
                if why:
                    capped.append(f"{a.get('label') or a['id']}: {why}")
                    continue
                if moved_from and a["id"] != moved_from["id"] and not self.deps.switch_allowed(moved_from, a):
                    continue                                 # (the same account again is fine once it reset)
                cands.append(a)
            if task.account and any(a["id"] == task.account for a in cands):
                return next(a for a in cands if a["id"] == task.account)     # stay pinned between rounds
            if cands:
                return min(cands, key=lambda a: (self.load.get(a["id"], 0), self.deps.usage_pct(a) or 0, a["order"]))
            why = f"no account can take '{task.title}'"
            if capped:
                why += " - spend cap reached (" + "; ".join(capped) + "); raise it on the Usage page, then Resume"
            elif moved_from:
                why += " after the limit on " + (moved_from.get("label") or moved_from["id"])
            task.status = "paused"
            await self.pause(why)
            task.status = "queued"

    async def master_call(self, prompt: str, kind: str, note: str = MASTER_NOTE, role: str = "master") -> dict:
        """Run the master; on a limit move it like a worker (same switching rules)."""
        moved_from = None
        while True:
            if self.run.stopped:
                raise Stopped()
            acc = self.master_acc
            if acc is None or self.limited.get(acc["id"], 0) > self.deps.now() or self.deps.over_cap(acc):
                acc = self._pick_master(moved_from)
                if acc is None:
                    await self.pause(f"no account can run the master ({kind})")
                    continue
                self.master_acc = acc
                await self._changed()
            model, effort = self.choice(acc, "master")
            sub = SubRun(self.run, "master", lambda tool, inp: self._master_write(tool, inp))
            self.run.emit({"t": "team_note", "text": f"{kind}…", "w": "master"})
            res = await self.deps.call(acc, sub, prompt, note, model, effort, role)
            u = res.get("usage")
            if u:
                self.master_tokens["in"] += u.input_tokens
                self.master_tokens["out"] += u.output_tokens
            err = res.get("error")
            if err and err.kind in ("limit", "auth") and not self.run.stopped:
                self.mark_limited(acc)
                self.notice(f"Master account {acc.get('label') or acc['id']} hit {'its limit' if err.kind == 'limit' else 'a sign-in problem'}; moving the master.")
                moved_from, self.master_acc = acc, None
                continue
            if self.run.stopped:
                raise Stopped()
            return res

    def _pick_master(self, moved_from: dict | None) -> dict | None:
        cands = [a for a in self._usable() if not self.deps.over_cap(a)
                 and (not moved_from or a["id"] == moved_from["id"] or self.deps.switch_allowed(moved_from, a))]
        if self.cfg.master_account:
            pref = [a for a in cands if a["id"] == self.cfg.master_account]
            if pref:
                return pref[0]
        claude = [a for a in cands if a["provider"] in CLAUDE_FAMILY]
        return (claude or cands or [None])[0]

    def _master_write(self, tool: str, tool_input: dict) -> str | None:
        if tool in SPAWN_TOOLS:
            return NO_SPAWN
        if tool in policy.WRITES:
            return "the master reviews only - it does not edit files"
        return None

    def _write_check(self, task: Task):
        def check(tool: str, tool_input: dict) -> str | None:
            if tool in SPAWN_TOOLS:
                return NO_SPAWN
            if tool not in policy.WRITES:
                return None
            for p in policy.paths_of(tool_input):
                holder = self.locks.claim(task.id, p)
                if holder:
                    other = next((t.title for t in self.tasks if t.id == holder), holder)
                    return f"another worker ('{other}') is writing {p} - leave it to them"
            return None
        return check

    # ------------------------------------------------ one sequential worker turn with limit re-pinning
    # (Script Studio and the episode run use this; the parallel team uses lifecycle())
    async def run_worker(self, task: Task, prompt: str, note: str, role: str) -> dict:
        moved = None
        while True:
            acc = await self.pick_worker(task, moved)
            task.status, task.account = "running", acc["id"]
            if not task.pins or task.pins[-1] != acc["id"]:
                task.pins.append(acc["id"])
            self.load[acc["id"]] = self.load.get(acc["id"], 0) + 1
            await self._changed()
            model, effort = self.choice(acc, "worker")
            sub = SubRun(self.run, task.id, self._write_check(task))
            try:
                res = await self.deps.call(acc, sub, prompt + self._resume(task), note, model, effort, role)
            finally:
                self.load[acc["id"]] -= 1
            u = res.get("usage")
            if u:
                task.tokens += (u.input_tokens or 0) + (u.output_tokens or 0)
                task.cost_usd += u.cost_usd or 0
            if self.run.stopped:
                raise Stopped()
            err = res.get("error")
            if err and err.kind in ("limit", "auth"):
                path = write_task_handoff(self.deps.handoff_dir(), self.goal, task, acc, res.get("text", ""),
                                          "usage limit" if err.kind == "limit" else "sign-in problem", self.deps.redact)
                task.handoffs.append(path.name)
                task.resume = (task.resume + "\n\n" if task.resume else "") + (res.get("text") or "")
                self.locks.release(task.id)
                self.mark_limited(acc)
                moved, task.account, task.status = acc, None, "queued"
                self.notice(f"{task.title}: {acc.get('label') or acc['id']} hit "
                            f"{'its usage limit' if err.kind == 'limit' else 'a sign-in problem'} - handoff saved "
                            f"({path.name}), moving to another account.")
                await self._changed()
                continue
            task.resume = ""
            task.status = "failed" if err else "done"
            if err:
                task.why = f"{err.kind}: {err.message[:200]}"
            await self._changed()
            return res

    @staticmethod
    def _resume(task: Task) -> str:
        if not task.resume:
            return ""
        return ("\n\nAnother account started this and hit its usage limit. Continue from where it stopped - "
                "check the file first, don't start over. What it had said:\n" + task.resume)

    # ------------------------------------------------ the flow
    async def go(self) -> dict:
        try:
            if self.own_plan:
                self._add_tasks(self.own_plan)            # the user's parts: no planning call
                await self._changed()
            else:
                await self.plan()
            self.state = "running"
            await self._changed()
            jobs = [asyncio.get_running_loop().create_task(self.lifecycle(t)) for t in self.tasks]
            try:
                await asyncio.gather(*jobs)
            finally:
                for j in jobs:
                    j.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
            if self.run.stopped:
                raise Stopped()
            self.state = "reviewing"
            await self._changed()
            if any(t.status == "done" for t in self.tasks):
                res = await self.master_call(final_prompt(self.goal, self.tasks), "final review")
                self.final = res.get("text") or ""
            else:
                self.final = "No task finished - see each task for why."
            self.state = "done"
        except (Stopped, asyncio.CancelledError):
            self.state = "stopped"
            for t in self.tasks:
                if t.status not in TERMINAL:
                    t.status, t.why = "stopped", "stopped by the user"
        finally:
            for t in self.tasks:
                self.locks.release(t.id)
            self.publish()
        return self.snapshot()

    async def plan(self) -> None:
        res = await self.master_call(plan_prompt(self.goal, self.cfg.max_tasks), "planning")
        d = parse_json(res.get("text", "")) or {}
        raw = [t for t in d.get("tasks") or [] if isinstance(t, dict)][: self.cfg.max_tasks]
        if not raw:
            raw = [{"title": "Do the goal", "brief": self.goal, "files": []}]
        self._add_tasks(raw)
        await self._changed()

    def _add_tasks(self, raw: list[dict]) -> None:
        """Parts from the master's plan or written by the user; files outside the project are dropped."""
        for n, t in enumerate(raw, 1):
            files = [str(f).strip() for f in t.get("files") or []
                     if isinstance(f, str) and f.strip() and policy.inside(f.strip(), self.run.root)]
            self.tasks.append(Task(id=f"t{n}", title=str(t.get("title") or f"Part {n}")[:80],
                                   brief=str(t.get("brief") or t.get("title") or self.goal),
                                   files=files, gpu=bool(t.get("gpu"))))

    ACTIVE = ("starting", "running", "review")

    def _overlaps(self, task: Task) -> bool:
        """Declared files of a task that is under way, or files any unfinished task still holds."""
        mine = {self.locks.key(f) for f in task.files}
        for o in self.tasks:
            if o is task or o.status in TERMINAL:
                continue
            theirs = set(self.locks.held(o.id))
            if o.status in self.ACTIVE:
                theirs |= {self.locks.key(f) for f in o.files}
            if mine & theirs:
                return True
        return False

    async def lifecycle(self, task: Task) -> None:
        moved_from = None
        while True:
            async with self.changed:                       # declared files must not clash with a running task
                await self.changed.wait_for(lambda: not self._overlaps(task) or self.run.stopped)
                if self.run.stopped:
                    raise Stopped()
                task.status = "starting"                    # claimed before anything can yield
            acc = await self.pick_worker(task, moved_from)
            async with self.slots:
                res = await self.work(task, acc)
            err = res.get("error")
            if self.run.stopped:
                raise Stopped()
            if err and err.kind in ("limit", "auth"):
                path = write_task_handoff(self.deps.handoff_dir(), self.goal, task, acc, res.get("text", ""),
                                          "usage limit" if err.kind == "limit" else "sign-in problem",
                                          self.deps.redact)
                task.handoffs.append(path.name)
                task.resume = (task.resume + "\n\n" if task.resume else "") + (res.get("text") or "")
                self.locks.release(task.id)
                self.mark_limited(acc)
                moved_from, task.account, task.status = acc, None, "queued"
                nxt = [a for a in self._usable() if not self.deps.over_cap(a) and self.deps.switch_allowed(acc, a)]
                self.notice(f"'{task.title}' hit {'the usage limit' if err.kind == 'limit' else 'a sign-in problem'} on "
                            f"{acc.get('label') or acc['id']} - handoff saved ({path.name}); "
                            + (f"re-pinned to {nxt[0].get('label') or nxt[0]['id']}." if nxt else "waiting for an account."))
                await self._changed()
                continue
            if err:
                task.status, task.why = "failed", f"{err.kind}: {err.message[:200]}"
                self.locks.release(task.id)
                await self._changed()
                return
            moved_from = None
            task.output, task.resume = res.get("text", ""), ""
            cap = self.deps.task_cap(acc)
            if cap and task.tokens >= cap:
                task.status, task.why = "needs_you", f"spend cap per task reached ({task.tokens:,} of {cap:,} tokens)"
                self.locks.release(task.id)
                await self._changed()
                return
            verdict = await self.review(task)
            if verdict == "pass":
                task.status = "done"
                self.locks.release(task.id)
                await self._changed()
                return
            if verdict == "escalate":
                task.status = "needs_you"
                self.locks.release(task.id)
                await self._changed()
                return
            task.status = "queued"                          # revise: same account, with the notes
            await self._changed()

    async def work(self, task: Task, acc: dict) -> dict:
        task.status, task.account = "running", acc["id"]
        if not task.pins or task.pins[-1] != acc["id"]:
            task.pins.append(acc["id"])
        self.load[acc["id"]] = self.load.get(acc["id"], 0) + 1
        await self._changed()
        model, effort = self.choice(acc, "worker")
        sub = SubRun(self.run, task.id, self._write_check(task))
        others = [t for t in self.tasks if t is not task]
        try:
            async with (self.gpu if task.gpu else nullcontext()):
                res = await self.deps.call(acc, sub, worker_prompt(self.goal, task, others), WORKER_NOTE,
                                           model, effort, "worker")
        finally:
            self.load[acc["id"]] -= 1
        u = res.get("usage")
        if u:
            task.tokens += (u.input_tokens or 0) + (u.output_tokens or 0)
            task.cost_usd += u.cost_usd or 0
        return res

    async def review(self, task: Task) -> str:
        """'pass' | 'revise' | 'escalate' - within the round and token caps (R10)."""
        used = task.review_in + task.review_out
        if used >= self.cfg.stage_budget:
            task.why = f"review budget used up ({used:,} of {self.cfg.stage_budget:,} tokens)"
            return "escalate"
        task.status = "review"
        await self._changed()
        res = await self.master_call(review_prompt(self.goal, task, self.cfg.review_in * 3), f"reviewing '{task.title}'")
        u = res.get("usage")
        rin, rout = (u.input_tokens, u.output_tokens) if u else (0, 0)
        task.review_in += rin
        task.review_out += rout
        task.rounds += 1
        d = parse_json(res.get("text", "")) or {}
        verdict = str(d.get("verdict", "")).lower()
        task.notes = str(d.get("notes") or "").strip()
        if res.get("error"):
            task.why = "the review failed: " + res["error"].message[:160]
            return "escalate"
        if verdict == "pass":
            return "pass"
        if verdict != "revise":
            task.why, task.notes = "the master's review could not be read", (res.get("text") or "")[:1500]
            return "escalate"
        if task.rounds >= self.cfg.review_rounds:
            task.why = f"still not right after {task.rounds} review rounds - your call"
            return "escalate"
        if rin > self.cfg.review_in or rout > self.cfg.review_out:
            task.why = f"a review round went over its budget ({rin:,} in / {rout:,} out)"
            return "escalate"
        if task.review_in + task.review_out >= self.cfg.stage_budget:
            task.why = f"review budget used up ({task.review_in + task.review_out:,} tokens)"
            return "escalate"
        return "revise"
