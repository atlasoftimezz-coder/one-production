"""Script Studio (P10): competitor transcripts -> style guide -> FacelessOS script -> Opus review loop.

    fetching   the app reads the competitors' captions (competitors.py, no AI)
    style      a worker (Sonnet) writes competitors/style-guide.md from the transcripts + numbers
    writing    a worker (Sonnet) writes scripts/<topic>.md with the FacelessOS writer skills
    review     the master (Opus) scores it with the FacelessOS rubric (faceless.RUBRIC)
    loop       revise -> review until every score reaches the pass mark with nothing left to fix,
               or the round cap, or no better for `stall` rounds, or the review budget: then the
               best-scoring version is kept and the user decides.
Built on the team machinery (team.py): same account picking, limit re-pin with handoffs, pause,
spend caps, file locks and Stop. Every version is kept as scripts/<topic>.v<n>.md.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from .. import faceless
from ..competitors import _safe, words_of
from .team import Stopped, SubRun, Task, Team, TeamConfig, parse_json, write_task_handoff


@dataclass
class ScriptConfig(TeamConfig):
    rounds: int = 5                  # review rounds at most
    pass_score: int = 8              # every rubric line must reach this (1-10)
    stall: int = 2                   # stop when the best score hasn't improved for this many rounds
    per_channel: int = 5             # a channel link -> its most-viewed recent uploads
    scan: int = 30                   # how many recent uploads to look through
    review_in: int = 60_000
    review_out: int = 8_000
    stage_budget: int = 300_000      # review tokens per script, all rounds


ANALYST_NOTE = ("You are the STYLE ANALYST in One Production's Script Studio. You study competitor videos and "
                "describe HOW they write so a writer can match the style. Never copy their sentences into the "
                "guide - describe patterns and quote at most a few words.")
WRITER_NOTE = ("You are the SCRIPTWRITER in One Production's Script Studio. You write original faceless YouTube "
               "scripts with the FacelessOS system, in the competitors' style but never with their sentences. "
               "Write the script into the file you are given with the Write tool.")
REVIEWER_NOTE = ("You are the REVIEWER (the master) in One Production's Script Studio. You do not edit files. "
                 "Score the script strictly with the FacelessOS system and the rubric. A score of 8 means "
                 "'ready to record'; don't be generous - the loop exists to make it better.")


def rubric_text() -> str:
    return "\n".join(f'- "{k}": {v}' for k, v in faceless.RUBRIC.items())


def spoken(text: str) -> str:
    """The words a narrator would say: no [VISUAL:]-style cues, headings or stage notes."""
    t = re.sub(r"\[[^\]]*\]", " ", text or "")
    t = re.sub(r"(?m)^\s*#.*$", " ", t)
    return t


class ScriptRun(Team):
    def __init__(self, pid: str, brief: dict, links: list[dict], run, deps, config: ScriptConfig,
                 skills: dict[str, str], state_file: Path | None = None):
        super().__init__(pid, brief["topic"], run, deps, config, state_file)
        self.brief, self.links, self.skills = brief, links, skills
        self.stage = "fetching"
        self.state = "running"
        self.fetch: dict = {"done": 0, "problems": [], "summary": {}, "line": ""}
        self.rounds: list[dict] = []
        self.result: dict = {}
        self.slug = _safe(brief["topic"], 50)
        self.script_rel = f"scripts/{self.slug}.md"
        self.target_words = None
        self.notes: list[str] = []

    # ------------------------------------------------ reporting
    def snapshot(self) -> dict:
        return {"kind": "script", "id": self.id, "state": self.state, "stage": self.stage, "brief": self.brief,
                "paused_why": self.paused_why, "resets_at": self.resets_at, "master": (self.master_acc or {}).get("id"),
                "config": {k: getattr(self.cfg, k) for k in ("rounds", "pass_score", "stall", "master_model",
                                                              "master_effort", "worker_model", "worker_effort")},
                "fetch": self.fetch, "tasks": [t.public() for t in self.tasks], "rounds": self.rounds,
                "result": self.result, "script": self.script_rel, "target_words": self.target_words,
                "master_tokens": self.master_tokens, "notes": self.notes[-6:], "started": self.started,
                "rubric": list(faceless.RUBRIC)}

    def publish(self) -> None:
        snap = self.snapshot()
        self.run.emit({"t": "script", "script": snap})
        if self.state_file:
            try:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=1), encoding="utf-8")
                tmp.replace(self.state_file)
            except OSError:
                pass

    def say(self, text: str) -> None:
        self.fetch["line"] = text
        self.publish()

    def notice(self, text: str) -> None:
        self.notes.append(text)
        super().notice(text)

    # ------------------------------------------------ prompts
    def _brief_lines(self) -> list[str]:
        b = self.brief
        return [f"Topic: {b['topic']}", f"Format: {faceless.FORMATS.get(b.get('format') or '', b.get('format') or 'any')}",
                f"Length: {b.get('minutes') or '?'} minutes (target about {self.target_words} spoken words)",
                f"Niche: {b.get('niche') or '-'}", f"Channel notes: {b.get('notes') or '-'}",
                f"Output mode: {b.get('mode') or 'Mode A - Traditional editing (visual cues for the editor)'}"]

    def analyst_prompt(self) -> str:
        files = [t["file"] for t in self.fetch.get("transcripts", [])]
        return "\n".join([
            "Study these competitor videos and write the style guide our writer will follow.",
            "Transcripts (read every one): " + ", ".join(f"competitors/{f}" for f in files),
            "Measured numbers: competitors/_numbers.md", "", *self._brief_lines(), "",
            "Write competitors/style-guide.md (800-1200 words) with these sections:",
            "1. Hooks - which FacelessOS hook categories they use, how the first 30 seconds work",
            "2. Structure - the format template they follow, section lengths, how they order points",
            "3. Rehooks and open loops - what they do and roughly how often (per minute)",
            "4. Voice - POV, tone, humour, how they address the viewer, sentence rhythm (use the numbers)",
            "5. Pace - words per minute and what that means for our target length",
            "6. Endings - outro and call-to-action pattern",
            "7. Copy this / never copy this - patterns worth using, and what would make us look like a copy",
            "Quote at most a few words per example. Finish with one line: 'Style guide written.'"])

    def writer_prompt(self, n: int, guide: str, last: dict | None) -> str:
        lines = [f"Write the script into {self.script_rel} (create the folder if needed).", "", *self._brief_lines(), "",
                 "The competitors' style guide (from competitors/style-guide.md):", guide.strip(), "",
                 "Rules: original wording only - never reuse a sentence from the competitor transcripts in "
                 "competitors/. Hit the target length (spoken words, cues don't count). Run the FacelessOS anti-AI "
                 "slop check and re-audit before you finish."]
        if last:
            lines = [f"Round {n}: rewrite {self.script_rel}. The reviewer scored the last version:", "",
                     self._card_text(last), "",
                     "Fix every must-fix point and raise every score below the pass mark "
                     f"({self.cfg.pass_score}/10). Keep what already scored well. Overwrite the same file.", "",
                     *lines[2:]]
        return "\n".join(lines)

    def _card_text(self, card: dict) -> str:
        sc = "\n".join(f"- {k}: {card['scores'].get(k, '?')}/10" for k in faceless.RUBRIC)
        mf = "\n".join(f"- {x}" for x in card["must_fix"]) or "- (none)"
        return f"Scores:\n{sc}\nMust fix:\n{mf}\nNotes: {card.get('notes') or '-'}"

    def review_prompt(self, n: int, script: str, guide: str, words: int) -> str:
        budget_chars = self.cfg.review_in * 2
        if len(script) > budget_chars:
            script = script[:budget_chars] + "\n[... cut to fit the review budget ...]"
        return "\n".join([
            f"Review round {n} of at most {self.cfg.rounds}.", "", *self._brief_lines(),
            f"Spoken words in this version: {words} (target about {self.target_words}).", "",
            "Competitor style guide:", guide.strip(), "", f"The script ({self.script_rel}):", script, "",
            "Score every rubric line 1-10:", rubric_text(), "",
            "Answer with ONLY a JSON object:",
            '{"scores": {"hook": 0, "structure": 0, "retention": 0, "rehooks": 0, "anti_slop": 0, "authenticity": 0, '
            '"style_match": 0, "length": 0, "outro": 0}, "must_fix": ["specific problems that block recording"], '
            '"notes": "the most useful advice for the next draft", "verdict": "pass or revise"}'])

    # ------------------------------------------------ the flow
    async def go(self) -> dict:
        try:
            await self._changed()
            res = await self.deps.fetch(self.links, self.say)
            self.fetch.update(done=len(res["transcripts"]), problems=res["problems"], summary=res["summary"],
                              transcripts=[{k: t.get(k) for k in ("title", "channel", "file", "wpm", "minutes", "views")}
                                           for t in res["transcripts"]], line="")
            if not res["transcripts"]:
                self.state, self.result = "failed", {"why": "no competitor transcript could be read - see the list"}
                return self.snapshot()
            wpm = res["summary"].get("median_wpm") or 150
            self.target_words = round(float(self.brief.get("minutes") or 10) * wpm)

            style = Task(id="style", title="Style guide", brief="competitor style guide",
                         files=["competitors/style-guide.md"])
            script = Task(id="script", title="Script", brief=self.brief["topic"], files=[self.script_rel])
            self.tasks = [style, script]
            self.stage = "style"
            r = await self.run_worker(style, self.analyst_prompt(), ANALYST_NOTE + "\n\n" + self.skills["analyst"], "analyst")
            if r.get("error"):
                self.state, self.result = "failed", {"why": f"the style guide failed: {r['error'].message[:200]}"}
                return self.snapshot()
            self.locks.release(style.id)
            guide_file = self.run.root / "competitors" / "style-guide.md"
            if not guide_file.is_file() and (r.get("text") or "").strip():
                guide_file.write_text(r["text"], encoding="utf-8")
            guide = guide_file.read_text(encoding="utf-8") if guide_file.is_file() else "(no style guide)"

            last, best, since_best = None, None, 0
            for n in range(1, max(1, self.cfg.rounds) + 1):
                self.stage = "writing"
                script.status = "queued"
                r = await self.run_worker(script, self.writer_prompt(n, guide, last),
                                          WRITER_NOTE + "\n\n" + self.skills["writer"], "writer")
                if r.get("error"):
                    self.result = {"why": f"the writer failed: {r['error'].message[:200]}"}
                    break
                path = self.run.root / self.script_rel
                if not path.is_file() and (r.get("text") or "").strip():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(r["text"], encoding="utf-8")
                if not path.is_file():
                    self.result = {"why": "the writer didn't write the script file"}
                    break
                text = path.read_text(encoding="utf-8")
                shutil.copyfile(path, path.with_name(f"{self.slug}.v{n}.md"))
                words = words_of(spoken(text))
                self.stage = "review"
                script.status = "review"
                await self._changed()
                card = await self.review(n, text, guide, words)
                self.rounds.append(card)
                await self._changed()
                if card["passed"]:
                    best = card
                    break
                if best is None or card["total"] > best["total"]:
                    best, since_best = card, 0
                else:
                    since_best += 1
                if card.get("stop"):
                    self.result = {"why": card["stop"]}
                    break
                if since_best >= self.cfg.stall:
                    self.result = {"why": f"no better after {since_best} more rounds - kept the best version"}
                    break
                last = card
            else:
                self.result = {"why": f"not passed after {self.cfg.rounds} rounds - kept the best version"}
            self.finish(best)
        except (Stopped, asyncio.CancelledError):
            self.state = "stopped"
            self.result = {"why": "stopped by the user"}
            for t in self.tasks:
                if t.status not in ("done", "failed"):
                    t.status = "stopped"
        finally:
            for t in self.tasks:
                self.locks.release(t.id)
            self.publish()
        return self.snapshot()

    def finish(self, best: dict | None) -> None:
        if best is None:
            self.state = "needs_you" if self.rounds else "failed"
            self.result.setdefault("why", "no review finished")
            return
        path = self.run.root / self.script_rel
        if self.rounds and best is not self.rounds[-1]:
            shutil.copyfile(path.with_name(f"{self.slug}.v{best['round']}.md"), path)
        self.result.update(passed=best["passed"], round=best["round"], total=best["total"], file=self.script_rel,
                           words=best["words"])
        self.state = "done" if best["passed"] else "needs_you"
        if best["passed"]:
            self.result["why"] = f"the reviewer passed round {best['round']}"
        for t in self.tasks:
            if t.status in ("review", "queued", "running"):
                t.status = "done" if best["passed"] else "needs_you"

    async def review(self, n: int, script: str, guide: str, words: int) -> dict:
        res = await self.master_call(self.review_prompt(n, script, guide, words), f"review round {n}",
                                     note=REVIEWER_NOTE + "\n\n" + self.skills["reviewer"], role="reviewer")
        u = res.get("usage")
        rin, rout = (u.input_tokens, u.output_tokens) if u else (0, 0)
        d = parse_json(res.get("text", "")) or {}
        raw = d.get("scores") if isinstance(d.get("scores"), dict) else {}
        scores = {}
        for k in faceless.RUBRIC:
            try:
                scores[k] = max(0, min(10, int(round(float(raw.get(k))))))
            except (TypeError, ValueError):
                scores[k] = 0
        must = [str(x)[:300] for x in d.get("must_fix") or [] if str(x).strip()][:12]
        card = {"round": n, "scores": scores, "must_fix": must, "notes": str(d.get("notes") or "")[:2000],
                "total": round(statistics.mean(scores.values()), 2), "words": words,
                "review_tokens": rin + rout, "time": time.time()}
        card["passed"] = bool(raw) and all(v >= self.cfg.pass_score for v in scores.values()) and not must
        if res.get("error"):
            card["stop"] = "the review failed: " + res["error"].message[:160]
        elif not raw:
            card["notes"] = card["notes"] or (res.get("text") or "")[:1500]
            card["unreadable"] = True
        spent = sum(c.get("review_tokens", 0) for c in self.rounds) + card["review_tokens"]
        if not card["passed"] and not card.get("stop"):
            if rin > self.cfg.review_in or rout > self.cfg.review_out:
                card["stop"] = f"a review round went over its budget ({rin:,} in / {rout:,} out)"
            elif spent >= self.cfg.stage_budget:
                card["stop"] = f"review budget used up ({spent:,} tokens)"
        return card
