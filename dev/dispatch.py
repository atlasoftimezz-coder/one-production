r"""Dev orchestration (plan D1, phase P11): hand a coding task on One Production itself to
Claude or Codex, then have the OTHER one review the change.

    .venv\Scripts\python dev\dispatch.py pick   [--kind K] "task text"
    .venv\Scripts\python dev\dispatch.py run    <task-id> "task text" [--kind K] [--provider P] [--no-review]
    .venv\Scripts\python dev\dispatch.py review <rev-range | branch> [--task ID] [--author claude|codex]
    .venv\Scripts\python dev\dispatch.py status

Rules live in dev\dispatch.json (provider order per kind of task, the usage floor). Accounts are
the app's own (data_dir\accounts.json + the login folders in profiles\), used only through the
official CLIs. accounts.json is only read. Within one service only the first signed-in account
is used: moving between two logins of the same service stays off until the P12 terms check
(D7), so "runs low" means Claude -> Codex or the reverse.

`run` works in a git worktree build\dispatch\<task> on branch dispatch/<task>; the main
working tree is never touched. Reviews are written to dev\reviews\<task>.md.

Exit codes: 0 done, 1 error, 2 no account above its floor, 3 change made but not reviewed.
See docs/P11-plan.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models as M  # noqa: E402
from app.accounts import Accounts, chaining_allowed, claude_env  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.providers.base import Fail, RateLimit, Usage, classify_text  # noqa: E402
from app.providers.claude_cli import MODELS as CLAUDE_MODELS, ClaudeCLI  # noqa: E402
from app.providers.cli import run_lines  # noqa: E402
from app.providers.codex_cli import CodexCLI, read_rate_limits  # noqa: E402
from app.usage import Ledger, usage_pct  # noqa: E402

CONFIG = ROOT / "dev" / "dispatch.json"
PROVIDERS = ("claude", "codex")
NAMES = {"claude": "Claude", "codex": "Codex"}
TRAILERS = {"claude": "Co-Authored-By: Claude <noreply@anthropic.com>",
            "codex": "Co-Authored-By: Codex <noreply@openai.com>"}
AUTHOR_RX = {"claude": re.compile(r"^Co-Authored-By:\s*Claude\b", re.I | re.M),
             "codex": re.compile(r"^Co-Authored-By:\s*Codex\b", re.I | re.M)}
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EXIT_OK, EXIT_ERROR, EXIT_NO_ACCOUNT, EXIT_NOT_REVIEWED = 0, 1, 2, 3
REVIEW_TOOLS = "Read,Glob,Grep"
FENCE = "~~~~"          # diffs can contain ``` (Markdown files); a tilde fence can't clash

DEFAULTS = {
    "floor_pct": 20,
    "review_floor_pct": 10,
    "rules": [{"kind": "default", "keywords": [], "order": ["claude", "codex"]}],
    "coder": {"timeout_s": 3600,
              "claude_allowed_tools": ["Read", "Edit", "Write", "Glob", "Grep", "Bash(python -m pytest:*)"]},
    "review": {"timeout_s": 900, "max_diff_chars": 80000},
}


class DispatchError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


def load_config(path: Path = CONFIG) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    for r in cfg["rules"]:
        bad = [p for p in r.get("order") or [] if p not in PROVIDERS]
        if bad or not r.get("order"):
            raise DispatchError(f"dispatch.json: rule {r.get('kind')!r} has a bad provider order {r.get('order')}")
    return cfg


def other(provider: str) -> str:
    return "codex" if provider == "claude" else "claude"


def label(acc: dict) -> str:
    return acc.get("label") or acc.get("email") or acc["id"]


def when(ts: float | None) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(ts)) if ts else "unknown"


def git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise DispatchError(f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def authors_of(repo: Path, rev_range: str) -> list[str]:
    """Who wrote the commits in the range, from their Co-Authored-By lines, oldest first."""
    found: list[str] = []
    for body in git(repo, "log", "--reverse", "--format=%B%x00", rev_range).split("\x00"):
        for p, rx in AUTHOR_RX.items():
            if rx.search(body) and p not in found:
                found.append(p)
    return found


@dataclass
class Choice:
    provider: str
    account: dict
    pct: float | None
    kind: str


@dataclass
class Outcome:
    text: str = ""
    usage: Usage | None = None
    windows: dict | None = None
    fail: Fail | None = None


class Dispatcher:
    def __init__(self, settings: Settings, repo: Path = ROOT, config: dict | None = None, say=print):
        self.settings = settings
        self.repo = Path(repo)
        self.config = config or load_config()
        self.say = say
        self.accounts = Accounts(settings.data_dir)
        self.ledger = Ledger(settings.data_dir)
        work = settings.data_dir / "cache" / "dispatch"
        self.cli = {"claude": ClaudeCLI(settings.claude_cmd, work), "codex": CodexCLI(settings.codex_cmd, work)}

    # ------------------------------------------------------------------ choosing
    def order_for(self, text: str, kind: str | None = None) -> tuple[list[str], str]:
        rules = self.config["rules"]
        if kind:
            for r in rules:
                if r.get("kind") == kind:
                    return list(r["order"]), kind
            raise DispatchError(f"no rule for kind {kind!r} in dispatch.json "
                                f"(known: {', '.join(r.get('kind', '?') for r in rules)})")
        low = text.lower()
        for r in rules[:-1]:
            if any(k.lower() in low for k in r.get("keywords") or []):
                return list(r["order"]), r.get("kind", "?")
        return list(rules[-1]["order"]), rules[-1].get("kind", "default")

    async def candidates(self, provider: str) -> tuple[list[dict], list[str]]:
        """Signed-in accounts of this service that may take work, and notes on the others.
        Only the first one unless same-service switching is allowed (D7)."""
        out, notes = [], []
        for acc in self.accounts.list():
            if acc["provider"] != provider:
                continue
            if out and not chaining_allowed(provider, getattr(self.settings, "allow_chaining", False)):
                notes.append(f"{NAMES[provider]} ({label(acc)}): not used - moving between two "
                             f"{NAMES[provider]} logins is off: the provider's terms don't allow it (docs/terms-check.md)")
                continue
            if not acc.get("enabled", True):
                notes.append(f"{NAMES[provider]} ({label(acc)}): turned off")
                continue
            try:
                st = await self.cli[provider].status(acc)
            except Exception as e:           # a broken CLI is "not available", not a crash
                st = {"ok": False, "reason": f"check failed: {e.__class__.__name__}"}
            if not st.get("ok"):
                notes.append(f"{NAMES[provider]} ({label(acc)}): {st.get('reason') or 'not signed in'}")
                continue
            out.append(acc)
        return out, notes

    async def usage(self, acc: dict) -> tuple[float | None, str, float | None]:
        """(percent used, exact|estimated|none, earliest reset). Live when the client can
        tell without a model turn; otherwise the last numbers the app saved."""
        live = None
        try:
            live = await self.cli[acc["provider"]].live_usage(acc)
        except Exception:
            live = None
        if live and live.get("windows"):
            rl = {"windows": live["windows"], "at": live.get("as_of") or time.time()}
        else:
            rl = acc.get("rate_limit")
        pct, kind = usage_pct({**acc, "rate_limit": rl}, self.ledger)
        resets = [w.get("resets_at") for w in ((rl or {}).get("windows") or {}).values() if w.get("resets_at")]
        return pct, kind, (min(resets) if resets else None)

    async def pick(self, order: list[str], floor: float) -> tuple[Choice | None, list[str]]:
        report: list[str] = []
        for provider in order:
            accs, notes = await self.candidates(provider)
            report += notes
            if not accs:
                report.append(f"{NAMES[provider]}: no signed-in account")
                continue
            for acc in accs:
                pct, kind, resets = await self.usage(acc)
                name = f"{NAMES[provider]} ({label(acc)})"
                if pct is not None and 100 - pct < floor:
                    report.append(f"{name}: {pct:.0f}% used, {100 - pct:.0f}% left - below the {floor:g}% "
                                  f"floor (resets {when(resets)})")
                    continue
                used = "usage unknown" if pct is None else f"{pct:.0f}% used ({kind})"
                report.append(f"{name}: {used} - picked")
                return Choice(provider, acc, pct, kind), report
        return None, report

    # ------------------------------------------------------------------ calling a model
    def argv(self, choice: Choice, role: str) -> list[str]:
        """The official CLI's command line. role = "coder" (edits inside its folder) or
        "reviewer" (read-only)."""
        p, acc = choice.provider, choice.account
        exe = self.cli[p]._exe()
        if not exe:
            raise DispatchError(f"{NAMES[p]} CLI is not installed")
        model, effort = acc.get("model"), acc.get("effort")
        if p == "claude":
            argv = [exe, "-p", "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
                    "--no-session-persistence"]
            if model in CLAUDE_MODELS:
                argv += ["--model", CLAUDE_MODELS[model]]
            if effort in M.EFFORTS and model != "haiku":
                argv += ["--effort", effort]
            if role == "coder":
                argv += ["--permission-mode", "acceptEdits", "--allowedTools",
                         *self.config["coder"]["claude_allowed_tools"]]
            else:
                argv += ["--tools", REVIEW_TOOLS]
            return argv
        argv = [exe, "exec", "--json", "--sandbox", "workspace-write" if role == "coder" else "read-only"]
        if model and M._SAFE.match(model):
            argv += ["--model", model]
        if effort and effort.isalnum():
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        return argv + ["-"]

    def env(self, choice: Choice) -> dict[str, str]:
        acc = choice.account
        if choice.provider == "claude":
            # Unlike the app's studio assistant, a dev task SHOULD read the repo's CLAUDE.md.
            # The user's own global instructions stay out: they live in the real ~/.claude,
            # not in this account's login folder.
            env = {**claude_env(acc), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        else:
            env = {"CODEX_HOME": acc["profile_dir"]}
        venv = self.repo / ".venv" / "Scripts"
        if venv.is_dir():              # `python -m pytest` in a worktree runs the repo's own venv
            env["PATH"] = str(venv) + os.pathsep + os.environ.get("PATH", "")
        return env

    async def call(self, choice: Choice, role: str, prompt: str, cwd: Path, timeout: float) -> Outcome:
        o = Outcome()
        got_result = False
        async with aclosing(run_lines(self.argv(choice, role), prompt, self.env(choice), cwd, timeout)) as lines:
            async for kind, value in lines:
                if kind == "timeout":
                    o.fail = Fail("transient", value)
                    break
                if kind == "out":
                    try:
                        d = json.loads(value)
                    except ValueError:
                        continue
                    got_result |= self._read(choice.provider, d, o)
                elif kind == "err" and not got_result and not o.fail:
                    msg = value.strip() or f"{NAMES[choice.provider]} stopped without an answer"
                    o.fail = Fail(classify_text(msg), msg[-300:])
        if choice.provider == "codex":
            rl = read_rate_limits(choice.account["profile_dir"])
            o.windows = rl["windows"] if rl else None
        self.ledger.record_call(account=choice.account["id"], provider=choice.provider, project=None,
                                usage=o.usage, outcome="error" if o.fail else "ok",
                                err_kind=o.fail.kind if o.fail else None, windows=o.windows,
                                role=f"dispatch-{role}")
        return o

    @staticmethod
    def _read(provider: str, d: dict, o: Outcome) -> bool:
        """Fold one JSON line into the outcome. True once the final answer arrived."""
        t = d.get("type")
        if provider == "claude":
            if t == "result":
                o.text = str(d.get("result") or "")
            for ev in ClaudeCLI._parse(d):
                if isinstance(ev, Usage):
                    o.usage = ev
                elif isinstance(ev, RateLimit):
                    o.windows = ev.windows
                elif isinstance(ev, Fail) and not o.fail:
                    o.fail = ev
            return t == "result"
        if t == "item.completed" and (d.get("item") or {}).get("type") == "agent_message":
            o.text = d["item"].get("text") or ""          # the last message is the summary
        elif t == "turn.completed":
            u = d.get("usage") or {}
            o.usage = Usage(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                            cache_read_tokens=u.get("cached_input_tokens", 0))
            return True
        elif t in ("error", "turn.failed") and not o.fail:
            msg = str(d.get("message") or (d.get("error") or {}).get("message") or d)[:300]
            o.fail = Fail(classify_text(msg), msg)
        return False

    # ------------------------------------------------------------------ run a task
    def coder_prompt(self, task: str, text: str, folder: Path, provider: str, handoff: str | None) -> str:
        lines = [
            "You are working on One Production, in a git worktree made for this one task:",
            str(folder), "",
            f"Task ({task}):", text.strip(), "",
            "Rules:",
            "- Read CLAUDE.md first; HANDOFF.md has the project's context, conventions and known traps.",
            "- Change only what the task needs, and only inside this folder. Do not commit; "
            "dispatch.py commits your work and a different model reviews it.",
            "- Add or update tests for what you change and run them with `python -m pytest <paths>`; "
            "fix what fails.",
            "- Finish with a short summary: files changed, tests run and their result, anything left undone.",
        ]
        if provider == "codex":
            lines.append("- Edit files with apply_patch, not with shell commands.")
        if handoff is not None:
            lines += ["", "Another model started this task and stopped at its usage limit. Its work so far is "
                      "already committed in this folder (see `git log` and `git show`). Continue from there; "
                      "don't redo finished parts. Its last words were:", handoff.strip() or "(none)"]
        return "\n".join(lines) + "\n"

    def commit(self, folder: Path, task: str, text: str, choice: Choice, partial: bool) -> bool:
        git(folder, "add", "-A")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=folder).returncode == 0:
            return False
        title = text.strip().splitlines()[0][:60]
        msg = (f"dispatch {task}: {title}{' (partial - stopped at its limit)' if partial else ''}\n\n"
               f"{text.strip()}\n\nWritten by {NAMES[choice.provider]} ({label(choice.account)}) "
               f"via dev/dispatch.py.\n\n{TRAILERS[choice.provider]}\n")
        p = subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=folder, input=msg, text=True,
                           encoding="utf-8", errors="replace", capture_output=True)
        if p.returncode:
            raise DispatchError(f"git commit failed: {(p.stderr or p.stdout).strip()[:300]}")
        return True

    async def run(self, task: str, text: str, kind: str | None = None, provider: str | None = None,
                  review: bool = True) -> int:
        if not TASK_ID.match(task):
            raise DispatchError("task id: lower-case letters, digits and '-', up to 40 characters")
        branch, folder = f"dispatch/{task}", self.repo / "build" / "dispatch" / task
        if folder.exists() or git(self.repo, "branch", "--list", branch).strip():
            raise DispatchError(f"task {task!r} already exists (branch {branch} or folder {folder}); "
                                "pick another id")
        if provider:
            order, rule = [provider], "--provider"
        else:
            order, rule = self.order_for(text, kind)
        self.say(f"Rule: {rule} -> {' then '.join(NAMES[p] for p in order)}")
        choice, report = await self.pick(order, self.config["floor_pct"])
        for line in report:
            self.say("  " + line)
        if not choice:
            self.say("No account has enough left for a coding task. Nothing was started.")
            return EXIT_NO_ACCOUNT
        if git(self.repo, "status", "--porcelain").strip():
            self.say("Note: the main folder has uncommitted changes; the task starts from the last commit.")
        base = git(self.repo, "rev-parse", "HEAD").strip()
        folder.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(folder), base)
        self.say(f"Worktree {folder} on {branch}")

        tried, writers, handoff, o = [], [], None, Outcome()
        while choice:
            self.say(f"{NAMES[choice.provider]} ({label(choice.account)}) is coding...")
            o = await self.call(choice, "coder", self.coder_prompt(task, text, folder, choice.provider, handoff),
                                folder, self.config["coder"]["timeout_s"])
            tried.append(choice.provider)
            if self.commit(folder, task, text, choice, partial=bool(o.fail)):
                writers.append(choice.provider)
            if not (o.fail and o.fail.kind == "limit"):
                break
            self.say(f"{NAMES[choice.provider]} hit its usage limit; handing over.")
            rest = [p for p in dict.fromkeys(order + list(PROVIDERS)) if p not in tried]
            choice, report = await self.pick(rest, self.config["floor_pct"])
            for line in report:
                self.say("  " + line)
            handoff = o.text
        if o.fail:
            self.say(f"The task stopped: {o.fail.message}. The worktree stays for you to look at.")
            return EXIT_ERROR
        head = git(folder, "rev-parse", "HEAD").strip()
        if head == base:
            self.say("The coder changed nothing. Summary:\n" + (o.text or "(none)"))
            return EXIT_OK
        self.say(f"Committed on {branch}: {git(folder, 'diff', '--shortstat', base, head).strip()}")
        if not review:
            return EXIT_OK
        return await self.review(f"{base}..{head}", task=task, authors=writers, cwd=folder,
                                 task_text=text, summary=o.text, branch=branch)

    # ------------------------------------------------------------------ review
    def review_prompt(self, author: str, rev_range: str, stat: str, task_text: str | None,
                      diff: str) -> str:
        return "\n".join([
            "You are reviewing a code change in One Production, a local Windows app (FastAPI on "
            "127.0.0.1 + one-page web UI). CLAUDE.md and HANDOFF.md in this folder describe it.",
            f"The change was written by {NAMES[author]}. You are the second pair of eyes, from a different model.",
            "",
            f"Task the change was made for: {task_text.strip() if task_text else '(not given - judge it from the commits)'}",
            f"Change: {rev_range} ({stat})",
            "",
            "Look for, in this order: bugs (wrong results, crashes, races, Windows-only breakage), security "
            "problems (secrets, paths outside the project, actions the app's policy should ask about), "
            "missing or weak tests for what changed, and breaks of the project's rules (CLAUDE.md, "
            "HANDOFF.md sections 8-10).",
            "Do not list style preferences and do not restate the diff. You may open files in this folder "
            "for context; you cannot change anything.",
            "",
            "Answer in Markdown, exactly these two sections:",
            "## Findings",
            "1. **high|medium|low** `path:line` - what is wrong, when it goes wrong, and the fix.",
            "(or the single line: No problems found.)",
            "## Verdict",
            "One line: merge, or fix first - and why.",
            "",
            "The diff:",
            FENCE + "diff", diff, FENCE,
        ]) + "\n"

    def review_path(self, task: str) -> Path:
        folder = self.repo / "dev" / "reviews"
        folder.mkdir(parents=True, exist_ok=True)
        path, n = folder / f"{task}.md", 2
        while path.exists():                      # a re-review never overwrites the earlier one
            path, n = folder / f"{task}-{n}.md", n + 1
        return path

    async def review(self, rev_range: str, task: str | None = None, authors: list[str] | None = None,
                     cwd: Path | None = None, task_text: str | None = None, summary: str | None = None,
                     branch: str | None = None) -> int:
        cwd = cwd or self.repo
        if ".." not in rev_range:                 # a branch or one commit: review it against main
            base = git(self.repo, "merge-base", "main", rev_range).strip()
            if base == git(self.repo, "rev-parse", rev_range).strip():
                base = git(self.repo, "rev-parse", f"{rev_range}~1").strip()
            branch = branch or rev_range
            rev_range = f"{base}..{git(self.repo, 'rev-parse', rev_range).strip()}"
        authors = authors or authors_of(cwd, rev_range)
        if not authors:
            raise DispatchError("can't tell who wrote this change (no Co-Authored-By: Claude/Codex line); "
                                "pass --author claude or --author codex")
        author = authors[-1]
        reviewer = other(author)
        task = task or "review-" + rev_range.split("..")[-1][:8]
        diff = git(cwd, "diff", rev_range)
        stat = git(cwd, "diff", "--shortstat", rev_range).strip() or "no changes"
        limit = int(self.config["review"]["max_diff_chars"])
        cut = len(diff) > limit
        if cut:
            diff = diff[:limit] + f"\n[... diff cut: {limit:,} of {len(diff):,} characters shown ...]"
        path = self.review_path(task)
        head = {"Change": f"`{rev_range}`" + (f" (branch `{branch}`)" if branch else ""),
                "Written by": " then ".join(NAMES[a] for a in authors), "Size": stat + (" - diff cut for the "
                "review" if cut else ""), "When": time.strftime("%Y-%m-%d %H:%M")}
        choice, report = await self.pick([reviewer], self.config["review_floor_pct"])
        for line in report:
            self.say("  " + line)
        if not choice:
            head["Reviewed by"] = f"nobody - {NAMES[reviewer]} not available: " + "; ".join(report)
            self.write_review(path, task, head, task_text, summary, None)
            self.say(f"Not reviewed; noted in {path.relative_to(self.repo)}")
            return EXIT_NOT_REVIEWED
        self.say(f"{NAMES[reviewer]} ({label(choice.account)}) is reviewing {stat}...")
        o = await self.call(choice, "reviewer", self.review_prompt(author, rev_range, stat, task_text, diff),
                            cwd, self.config["review"]["timeout_s"])
        if o.fail:
            head["Reviewed by"] = f"nobody - {NAMES[reviewer]} failed: {o.fail.message}"
            self.write_review(path, task, head, task_text, summary, None)
            self.say(f"Review failed ({o.fail.message}); noted in {path.relative_to(self.repo)}")
            return EXIT_NOT_REVIEWED
        head["Reviewed by"] = f"{NAMES[reviewer]} ({label(choice.account)})"
        self.write_review(path, task, head, task_text, summary, o.text)
        self.say(f"Review written: {path.relative_to(self.repo)}")
        return EXIT_OK

    @staticmethod
    def write_review(path: Path, task: str, head: dict, task_text: str | None, summary: str | None,
                     review: str | None) -> None:
        out = [f"# Review: {task}", "", "| | |", "|---|---|"]
        out += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in head.items()]
        out += ["", "## Status", "", "Reviewed." if review is not None else "**Not reviewed.**"]
        if task_text:
            out += ["", "## Task", "", task_text.strip()]
        if summary:
            out += ["", "## Coder's summary", "", summary.strip()]
        if review is not None:
            out += ["", "## Review", "", review.strip() or "(the reviewer returned nothing)"]
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ status
    def status(self) -> int:
        branches = [b.strip(" *+") for b in git(self.repo, "branch", "--list", "dispatch/*").splitlines() if b.strip()]
        reviews = sorted((self.repo / "dev" / "reviews").glob("*.md")) if (self.repo / "dev" / "reviews").is_dir() else []
        self.say("Dispatch branches: " + (", ".join(branches) or "none"))
        for b in branches:
            folder = self.repo / "build" / "dispatch" / b.split("/", 1)[1]
            ahead = git(self.repo, "rev-list", "--count", f"main..{b}", check=False).strip() or "?"
            self.say(f"  {b}: {ahead} commit(s) ahead of main" + (f", worktree {folder}" if folder.exists() else ""))
        self.say("Reviews: " + (", ".join(p.name for p in reviews) or "none"))
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="dispatch.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick", help="show which account would get a task")
    p.add_argument("text")
    p.add_argument("--kind")
    p.add_argument("--review", action="store_true", help="check for a review instead (lower floor)")
    r = sub.add_parser("run", help="code a task in its own worktree, then cross-review it")
    r.add_argument("task")
    r.add_argument("text")
    r.add_argument("--kind")
    r.add_argument("--provider", choices=PROVIDERS)
    r.add_argument("--no-review", action="store_true")
    v = sub.add_parser("review", help="cross-model review of an existing change")
    v.add_argument("range", help="A..B, a branch, or one commit")
    v.add_argument("--task")
    v.add_argument("--author", choices=PROVIDERS)
    sub.add_parser("status", help="dispatch branches and review files")
    a = ap.parse_args(argv)
    try:
        d = Dispatcher(load_settings())
        if a.cmd == "status":
            return d.status()
        if a.cmd == "pick":
            order, rule = d.order_for(a.text, a.kind)
            floor = d.config["review_floor_pct" if a.review else "floor_pct"]
            d.say(f"Rule: {rule} -> {' then '.join(NAMES[x] for x in order)} (floor {floor}% left)")
            choice, report = asyncio.run(d.pick(order, floor))
            for line in report:
                d.say("  " + line)
            return EXIT_OK if choice else EXIT_NO_ACCOUNT
        if a.cmd == "run":
            return asyncio.run(d.run(a.task, a.text, a.kind, a.provider, not a.no_review))
        return asyncio.run(d.review(a.range, task=a.task, authors=[a.author] if a.author else None))
    except DispatchError as e:
        print(f"dispatch: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
