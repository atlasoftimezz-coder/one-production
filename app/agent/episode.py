"""Episode run (P10, Fusion since F4): a finished script -> voiceover -> Fusion visuals -> the owner's
render -> upload bundle.

    script    the owner's script file (Script Studio writes and reviews it)               app, no AI
    voice     a voiceover job (or one the owner already made), then the voice gate        app, GPU
    visuals   a worker designs this episode's own art direction, writes the shot list,     worker (Claude) + master
              fusion_setup builds the Resolve project (1080p 60 fps, one Fusion comp per
              shot at its spoken words), the worker scripts every shot through Resolve's
              MCP with the Fusion kit (each script is an approval card), fusion_stills;
              every shot must be built, then the master reviews the stills (2 rounds)
    edit      the OWNER renders the timeline in Resolve (owner, 2026-10-06: "I render         owner
              myself") and picks the file; then the video gate
    package   a worker writes title options, description, tags and a thumbnail;           worker + master
              the bundle must pass the package gate; then the master reviews
    final     the master's short report for the owner

A numeric gate that fails sends its numbers back to the worker as the review notes (at most
`gate_rounds` times); a gate the app's own stages fail stops the run with "needs you". The run
waits at "edit" (state "waiting_render") until the owner picks the rendered file, then resumes
- also after an app restart, from the saved snapshot.

DRAFT (spec["draft"], owner 2026-10-11: "first it should draft completely for first level of review"):
the run never waits for the owner unless going on is impossible. A failed gate, a shot still empty
after its retries or the master's doubts become FLAGS (stage "passed_with_notes") listed in the draft
review report. Visuals are made in fresh turns: a plan turn (art direction from the chosen competitor
style profiles, this episode's own motion library motion.py, shots timed to the learned pacing,
fusion_setup), script turns of about BATCH shots (kit scripts for this episode's shots run without a
card - fusion.ShotScripts), and a check turn on the stills. The app renders the timeline itself.
Hard stops: unreadable script, failed voiceover, Resolve not ready, no Resolve project, failed render.
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

from .. import fusion_kit
from ..fusion import ShotScripts
from ..fusion_kit import FONTS
from .team import Stopped, SubRun, Task, Team, TeamConfig

BATCH = 10                          # shots per script turn in a draft
REFS = 12                           # competitor frames copied for the shot QA sheets
QA_PASS = 7                         # a shot scoring under this (1-10) against the competitor is re-scripted

STAGES = (("script", "Script"), ("voice", "Voiceover"), ("visuals", "Visuals"), ("edit", "Render"), ("package", "Package"))
ORDER = [s for s, _ in STAGES]


@dataclass
class EpisodeConfig(TeamConfig):
    gate_rounds: int = 3             # a worker may miss its numeric gate this often before it's your call
    shot_seconds: float = 3.5        # aimed-for average shot length


VISUALS_NOTE = ("You are the MOTION DESIGNER in One Production's episode run. You make every shot in DaVinci Resolve's "
                "Fusion page by script (no drawings, no image models). Every episode gets its own art direction, chosen "
                "so this script's ideas are as easy as possible to understand; never fall back to a generic template look. "
                "Quality bar: 1080p 60 fps, eased motion with motion blur, clean type, nothing overlapping or cut off. "
                "Use only the file tools (Read, Glob, Grep, Write, Edit), the studio tools and Resolve's MCP - never run "
                "shell commands: each one stops the run and waits for the owner.")
PACKAGER_NOTE = ("You are the PACKAGER in One Production's episode run. You write what the upload needs: honest, "
                 "specific titles and a description that matches the video, tags, and a thumbnail drawn as SVG. "
                 "Use only the file tools and the studio tools - never run shell commands (each one waits for the owner).")
CLAUDE_ACCOUNTS = ["claude", "anthropic_api"]      # Resolve's MCP reaches Claude Code only (resolve_mcp.py)


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40] or 'episode'


class EpisodeRun(Team):
    def __init__(self, pid: str, spec: dict, run, deps, config: EpisodeConfig, state_file: Path | None = None):
        super().__init__(pid, f"Episode: {spec['name']}", run, deps, config, state_file)
        self.spec = spec
        self.state = "running"
        self.base = f"work/{slug(spec['name'])}"
        self.stages = [{"id": i, "title": t, "status": "waiting", "detail": "", "report": "", "files": []} for i, t in STAGES]
        self.notes: list[str] = []
        self.result: dict = {}
        self.vo: dict = {}
        self.draft = bool(spec.get("draft"))
        self.flags: list[dict] = []                  # draft: what the owner should look at first
        self.profiles: list[dict] = []               # draft: the chosen competitor style profiles

    @classmethod
    def from_snapshot(cls, pid: str, snap: dict, run, deps, config: EpisodeConfig, state_file: Path | None = None) -> "EpisodeRun":
        """Pick a run up again where it waited (the owner's render) - e.g. after an app restart."""
        ep = cls(pid, snap["spec"], run, deps, config, state_file)
        ep.stages = snap.get("stages") or ep.stages
        ep.vo = dict(snap.get("vo") or {})
        ep.base = snap.get("base") or ep.base
        ep.state = snap.get("state") or "running"
        ep.notes = list(snap.get("notes") or [])
        ep.flags = list(snap.get("flags") or [])
        return ep

    # ------------------------------------------------ reporting
    def snapshot(self) -> dict:
        return {"kind": "episode", "id": self.id, "state": self.state, "spec": self.spec, "stages": self.stages,
                "paused_why": self.paused_why, "resets_at": self.resets_at, "master": (self.master_acc or {}).get("id"),
                "config": {k: getattr(self.cfg, k) for k in ("master_model", "master_effort", "worker_model", "worker_effort",
                                                              "review_rounds", "gate_rounds")},
                "tasks": [t.public() for t in self.tasks], "master_tokens": self.master_tokens, "final": self.final,
                "result": self.result, "notes": self.notes[-6:], "started": self.started, "base": self.base, "vo": self.vo,
                "draft": self.draft, "flags": self.flags}

    def publish(self) -> None:
        snap = self.snapshot()
        self.run.emit({"t": "episode", "episode": snap})
        if self.state_file:
            try:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=1), encoding="utf-8")
                tmp.replace(self.state_file)
            except OSError:
                pass

    def notice(self, text: str) -> None:
        self.notes.append(text)
        super().notice(text)

    def stage(self, sid: str) -> dict:
        return next(s for s in self.stages if s["id"] == sid)

    async def set_stage(self, sid: str, status: str, detail: str = "", **extra) -> None:
        self.stage(sid).update(status=status, detail=detail, **extra)
        await self._changed()

    async def blocked(self, sid: str, why: str, report: str = "") -> dict:
        """A failed gate or a failed step: stop here and hand it to the owner."""
        await self.set_stage(sid, "needs_you", why, report=report)
        self.state, self.result = "needs_you", {"stage": sid, "why": why}
        return self.snapshot()

    async def flag(self, sid: str, why: str, report: str = "", **extra) -> None:
        """Draft: note it for the owner's review and carry on."""
        self.flags.append({"stage": sid, "why": why[:600], "report": report})
        self.notice(f"Flagged for your review - {self.stage(sid)['title']}: {why[:160]}")
        await self.set_stage(sid, "passed_with_notes", why[:300], report=report, **extra)

    # ------------------------------------------------ the flow
    async def go(self, start: str = "script") -> dict:
        try:
            self.state = "running"
            await self._changed()
            steps = {"script": self.do_script, "voice": self.do_voice, "visuals": self.do_visuals,
                     "edit": self.do_edit, "package": self.do_package}
            for sid in ORDER[ORDER.index(start):]:
                if self.run.stopped:
                    raise Stopped()
                await self.set_stage(sid, "running")
                ok = await steps[sid]()
                if not ok:
                    return self.snapshot()
            self.state = "reviewing"
            await self._changed()
            res = await self.master_call(self.final_prompt(), "final report")
            self.final = res.get("text") or ""
            self.state = "done"
            self.result = {"folder": self.stage("package")["files"][0] if self.stage("package")["files"] else "",
                           "video": self.vo.get("video", "")}
        except (Stopped, asyncio.CancelledError):
            self.state = "stopped"
            for s in self.stages:
                if s["status"] in ("waiting", "running"):
                    s["status"] = "stopped"
        finally:
            for t in self.tasks:
                self.locks.release(t.id)
            self.publish()
        return self.snapshot()

    async def rendered(self, video: str) -> dict:
        """The owner rendered the timeline: check the file, then package."""
        self.vo["rendered"] = video
        return await self.go("edit")

    async def do_script(self) -> bool:
        try:
            info = await asyncio.to_thread(self.deps.script_stats, self.spec["script"])
        except Exception as exc:                     # noqa: BLE001 - shown to the owner
            await self.blocked("script", f"the script can't be read: {exc}")
            return False
        if not info["words"]:
            await self.blocked("script", "the script has no spoken words")
            return False
        await self.set_stage("script", "passed", f"{info['words']} spoken words, {info['sections']} sections",
                             files=[self.spec["script"]])
        return True

    async def do_voice(self) -> bool:
        if self.spec.get("voice_job"):
            job = self.deps.voice_job(self.spec["voice_job"])
            if not job or job.get("status") != "done":
                await self.blocked("voice", "the chosen voiceover is not finished")
                return False
        else:
            job = await self.deps.start_voice(self.spec)
            while job.get("status") not in ("done", "failed"):
                await self.set_stage("voice", "running", job.get("step") or "voicing")
                await asyncio.sleep(self.deps.poll)
                if self.run.stopped:
                    raise Stopped()
                job = self.deps.voice_job(job["id"]) or {"status": "failed", "error": "the voiceover disappeared"}
            if job["status"] == "failed":
                await self.blocked("voice", f"the voiceover failed: {job.get('error') or 'unknown error'}")
                return False
        report = await self.deps.voice_gate(job["id"])
        wav = next(f for f in job["files"] if f.lower().endswith(".wav"))
        srt = next((f for f in job["files"] if f.lower().endswith(".srt")), "")
        self.vo = {"job": job["id"], "wav": wav, "srt": srt}
        if not report["passed"]:
            if self.draft and srt:
                await self.flag("voice", "voice gate: " + report["summary"], report["report"], files=[wav, srt])
                return True
            await self.blocked("voice", "voice gate: " + report["summary"], report["report"])
            return False
        await self.set_stage("voice", "passed", f"{report.get('seconds', 0):.0f} s, all voice checks passed",
                             report=report["report"], files=[wav, srt])
        return True

    async def worker_stage(self, sid: str, task: Task, prompt, note: str, check) -> dict | None:
        """Worker -> numeric check (fails go back as notes) -> master review (2 rounds). None = handed to the owner."""
        self.tasks.append(task)
        misses = 0
        while True:
            res = await self.run_worker(task, prompt(task), note, "worker")
            self.locks.release(task.id)
            if res.get("error"):
                if self.draft and sid == "package":          # the draft video exists; packaging can wait for review
                    await self.flag(sid, f"the packaging worker failed: {task.why}")
                    return {}
                await self.blocked(sid, f"the worker failed: {task.why}")
                return None
            task.output = res.get("text", "")
            await self.set_stage(sid, "running", "checking the result")
            ok, why, extra = await check()
            if not ok:
                misses += 1
                if misses >= self.cfg.gate_rounds:
                    if self.draft and extra.get("usable", True):
                        task.status = "done"
                        await self.flag(sid, f"still failing its check after {misses} tries: {why}", extra.get("report", ""),
                                        files=extra.get("files", []))
                        return extra
                    await self.blocked(sid, f"still failing its check after {misses} tries: {why}", extra.get("report", ""))
                    return None
                task.notes = "The automatic check failed - fix this first: " + why
                self.notice(f"{task.title}: check failed ({why[:120]}) - back to the worker.")
                continue
            if self.draft:                           # one look by the master; its doubts go into the review report
                verdict = await self.review(task)
                task.status = "done"
                if verdict == "pass":
                    await self.set_stage(sid, "passed", extra.get("detail", ""), report=extra.get("report", ""), files=extra.get("files", []))
                else:
                    await self.flag(sid, "the master's notes: " + (task.notes or task.why or verdict), extra.get("report", ""),
                                    files=extra.get("files", []))
                return extra
            verdict = await self.review(task)
            if verdict == "pass":
                task.status = "done"
                await self.set_stage(sid, "passed", extra.get("detail", ""), report=extra.get("report", ""), files=extra.get("files", []))
                return extra
            if verdict == "escalate":
                task.status = "needs_you"
                await self.blocked(sid, task.why or "the master wants your decision", extra.get("report", ""))
                return None
            self.notice(f"{task.title}: the master asked for changes.")

    # visuals (Fusion) -----------------------------------------------------------
    def visuals_prompt(self, task: Task) -> str:
        s, b = self.spec, self.base
        music = f' music="{s["music"]}"' if s.get("music") else ""
        lines = [f"Make the visuals for the episode \"{s['name']}\" in DaVinci Resolve's Fusion page (1080p, 60 fps).",
                 f"Script: {s['script']}. Voiceover: {self.vo['wav']} ({self.vo.get('seconds', 0):.0f} s). Subtitles with the spoken "
                 f"words and times: {self.vo['srt']}. Read the script first.", "",
                 f"1. Art direction for THIS episode only (never reuse a look from another video): write {b}/art.json "
                 "{\"concept\": \"one paragraph: why this look makes this script's ideas easiest to follow\", \"palette\": "
                 "{\"bg\", \"ink\", \"accent\", ... named \"#RRGGBB\" colors}, \"fonts\": {\"heading\", \"body\", \"mono\"} from "
                 f"{', '.join(f for f in FONTS if f not in ('Segoe UI Emoji', 'Segoe Fluent Icons'))}, \"motion\": \"eases, "
                 "stagger, transitions, camera language\", \"three_d\": true/false}.",
                 f"2. Shot list: write {b}/shots.json {{\"shots\": [{{\"idea\": \"what the shot shows and how it moves\", "
                 "\"text\": \"on-screen words\"}, {\"anchor\": \"3-6 consecutive words exactly as in the subtitles\", "
                 "\"idea\": \"...\", \"text\": \"...\"}]}. The first shot starts at 0; every other shot starts when its anchor "
                 f"words are spoken. One idea per shot, a new shot about every {self.cfg.shot_seconds:g} s and whenever the "
                 "narration changes subject.",
                 f"3. Call fusion_setup with shots=\"{b}/shots.json\" art=\"{b}/art.json\" voiceover=\"{self.vo['wav']}\" "
                 f"subtitles=\"{self.vo['srt']}\"{music} name=\"{s['name']}\". Fix whatever it reports and call it again.",
                 "4. Script EVERY shot with its own mcp__davinci__run_script: the script starts with `from op_kit import *`, "
                 "then S = begin(\"shot-NNN\"), builds the shot from the art direction and its brief, and ends with "
                 "result = finish(S, layer). The owner approves each script, so keep each one clear and focused.",
                 f"5. Call fusion_stills with state=\"{b}/fusion.json\", open the stills (Read the PNG files) and re-script any "
                 "shot that is unclear, off-style, cramped or overlapping; call fusion_stills again afterwards.",
                 "Finish with a short summary: the concept in one line, the number of shots, and anything you were unsure about."]
        if s.get("visual_notes"):
            lines += ["", "The owner's directions for the look: " + s["visual_notes"]]
        if task.notes:
            lines += ["", "Changes required before this can pass:", task.notes]
        return "\n".join(lines)

    async def check_visuals(self) -> tuple[bool, str, dict]:
        try:
            out = await asyncio.to_thread(self.deps.fusion_stills, f"{self.base}/fusion.json")
        except Exception as exc:                     # noqa: BLE001 - goes back to the worker
            return False, str(exc)[:600], {}
        if out.get("empty"):
            return False, "these shots are still empty in Resolve: " + ", ".join(out["empty"][:20]), {}
        if not out.get("stills"):
            return False, "no stills could be exported", {}
        extra = {"detail": f"{out['shots']} Fusion shots in Resolve project {out['project']}",
                 "files": [out["folder"], f"{self.base}/art.json", f"{self.base}/shots.json"], "project": out["project"]}
        qa = out.get("qa")
        if not (self.draft and qa):
            return True, "", extra
        self.vo["qa"] = {"target": qa["target"], "flat": qa["flat"]}
        try:
            scores = json.loads((self.run.root / self.base / "qa.json").read_text(encoding="utf-8-sig")).get("shots") or []
        except (OSError, ValueError, AttributeError):
            return False, f"write {self.base}/qa.json with a score for every shot against the competitor", extra
        by_shot = {str(s.get("shot")): s for s in scores if isinstance(s, dict)}
        missing = [n for n in qa["shots"] if n not in by_shot]
        low = sorted(n for n, s in by_shot.items() if isinstance(s.get("score"), (int, float)) and s["score"] < QA_PASS)
        self.vo["qa"]["scores"] = {n: by_shot[n].get("score") for n in by_shot}
        problems = []
        if missing:
            problems.append("no QA score for " + ", ".join(missing[:15]))
        if low:
            problems.append(f"scored under {QA_PASS}: " + "; ".join(f"{n} ({by_shot[n].get('score')}: {str(by_shot[n].get('why', ''))[:80]})"
                                                               for n in low[:12]))
        if qa["flat"]:
            t = qa["target"]
            problems.append(f"measure flat next to the competitor (its median: {t.get('colours')} colour shades, fill "
                            f"{t.get('fill')}): " + ", ".join(f"{n} ({qa['shots'][n]['colours']} shades, fill {qa['shots'][n]['fill']})"
                                                             for n in qa["flat"][:12]) + " - add a generated asset or a real backdrop")
        if problems:
            return False, " | ".join(problems), extra
        return True, "", extra

    async def do_visuals(self) -> bool:
        ok, why = self.deps.fusion_ready()
        if not ok:
            await self.blocked("visuals", why)
            return False
        if self.draft:
            return await self.draft_visuals()
        task = Task(id="visuals", title="Visuals (Fusion)", files=[f"{self.base}/art.json", f"{self.base}/shots.json"],
                    providers=list(CLAUDE_ACCOUNTS),
                    brief=(f"Fusion motion graphics at 1080p 60 fps. Read {self.base}/art.json and open the stills in "
                           f"{self.base}/stills/ (Read the PNG files). Pass only if: the art direction suits this script and "
                           "is followed in every shot; each shot makes its one idea easy to understand at a glance; type is "
                           "clean and readable on a phone; nothing overlaps or is cut off; margins are consistent; and it does "
                           "not look like a generic template."))
        self.vo["seconds"] = await asyncio.to_thread(self.deps.duration, self.vo["wav"])
        extra = await self.worker_stage("visuals", task, self.visuals_prompt, VISUALS_NOTE, self.check_visuals)
        if extra is None:
            return False
        self.vo["resolve_project"] = extra.get("project", "")
        return True

    # visuals as a draft: plan turn -> script turns -> check turn --------------------------------
    def load_profiles(self) -> None:
        """The chosen competitor style profiles, copied into the episode folder for the worker to read, and
        up to REFS of the competitor's frames (spread over the video) for the shot QA sheets."""
        self.profiles = []
        refs: list[str] = []
        for n, item in enumerate(self.deps.style_profiles(self.spec.get("styles") or []), 1):
            rel = f"{self.base}/style/{n}-{slug(item['name'])}.json"
            target = self.run.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(item["profile"], indent=1), encoding="utf-8")
            self.profiles.append({k: v for k, v in item.items() if k != "frames"} | {"file": rel})
            refs += item.get("frames") or []
        ref_dir = self.run.root / self.base / "style" / "refs"
        shutil.rmtree(ref_dir, ignore_errors=True)
        if refs:
            ref_dir.mkdir(parents=True, exist_ok=True)
            k = min(REFS, len(refs))
            pick = [refs[int(round(i * (len(refs) - 1) / max(1, k - 1)))] for i in range(k)]
            for i, src in enumerate(dict.fromkeys(pick), 1):
                try:
                    shutil.copyfile(src, ref_dir / f"ref-{i:02d}.jpg")
                except OSError:
                    continue

    def pace_target(self) -> tuple[float, float | None]:
        """(shot length to aim for in seconds, hook cuts per minute) from the competitor numbers."""
        def num(key):
            vals = [((p["profile"].get("measured") or {}).get("pace") or {}).get(key) for p in self.profiles]
            vals = [float(v) for v in vals if v]
            return statistics.median(vals) if vals else None
        # a visible change (cut OR something animating in) is what a new Fusion shot is; hard cuts alone undercount a
        # continuous motion-graphics style (Bravos Research: 0.2 cuts/min, 11 changes/min - real measure, 2026-10-11)
        shot = num("beat_median") or num("shot_median")
        hook = num("first_30s_beats_per_min") or num("first_30s_cuts_per_min")
        return (round(max(1.0, min(10.0, shot)), 2) if shot else self.cfg.shot_seconds), hook

    def style_lines(self) -> list[str]:
        if not self.profiles:
            return ["No competitor style was chosen for this episode: design the look from the script alone."]
        return ["The owner chose these competitor editing styles to learn from (read each file: what the shots show, "
                "type, colour approach, motion, transitions, sound, do/don't, and the measured numbers). Learn their craft "
                "and rhythm - never copy their branding, characters, logos, exact layouts or wording:",
                *[f"- {p['name']}: {p['file']}" for p in self.profiles]]

    def plan_prompt(self, task: Task) -> str:
        s, b = self.spec, self.base
        shot, hook = self.pace_target()
        music = f' music="{s["music"]}"' if s.get("music") else ""
        sfx = getattr(self.deps, "sfx_ready", lambda: False)()
        lines = [f"Plan the visuals of the episode \"{s['name']}\" (DaVinci Resolve Fusion, 1080p, 60 fps). This turn plans "
                 "and sets up the project; other turns script the shots.",
                 f"Script: {s['script']}. Voiceover: {self.vo['wav']} ({self.vo.get('seconds', 0):.0f} s). Subtitles with the "
                 f"spoken words and times: {self.vo['srt']}. Read the script first.", "", *self.style_lines(), "",
                 f"1. Art direction for THIS script (new every time, never a template): write {b}/art.json {{\"concept\", "
                 "\"palette\": {\"bg\", \"ink\", \"accent\", ... \"#RRGGBB\"}, \"fonts\": {\"heading\", \"body\", \"mono\"} from "
                 f"{', '.join(f for f in FONTS if f not in ('Segoe UI Emoji', 'Segoe Fluent Icons'))}, \"motion\", \"three_d\", "
                 "\"why_this_fits\": \"how this look follows the chosen style and makes this script easy to follow\"}.",
                 f"2. This episode's own motion library: write {b}/motion.py - new helper functions designed for this "
                 "script in the learned style (how text enters, reveals, transitions, diagram/chart/map builders, emphasis "
                 "moves). Build only on the Fusion kit (functions take S, the shot, and use its methods or S.add with real "
                 "Fusion tool IDs). No imports, no files, no exec. Shot scripts will start with `from op_kit import *` and "
                 "`from ep_motion import *` and call your functions.",
                 f"3. Shot list: write {b}/shots.json {{\"shots\": [{{\"idea\", \"text\"}}, {{\"anchor\": \"3-6 consecutive "
                 "words exactly as in the subtitles\", \"idea\": \"what the shot shows and how it moves\", \"text\"}}]}. The "
                 f"first shot starts at 0; every other shot starts at its anchor words. Pace: shots of about {shot:g} s"
                 + (f", with about {hook:g} cuts per minute in the first 30 s" if hook else "") +
                 " (the competitor's measured rhythm), and always a new shot when the narration changes subject."
                 + (" Where real footage explains better than graphics (follow the style's share of footage; at most about "
                    "half of the shots), find a Pexels video with stock_search, download it with stock_fetch, and give that "
                    "shot \"stock\": \"stock/<file>.mp4\" and \"from\": seconds into the clip; the shot's graphics then go over "
                    "the footage. If fusion_setup rejects a footage shot, FIX it (another \"from\", a longer clip, a slightly "
                    "shorter shot) - never drop the footage silently." if getattr(self.deps, "stock_ready", lambda: False)() else "")]
        steps = []
        if getattr(self.deps, "assets_ready", lambda: False)():
            steps.append("Hero objects: list what the shots need that Fusion can't draw well - buildings, vault doors, gold "
                         "bars, coins, banknotes, maps, machines, a GENERIC person figure - and make each with asset_generate "
                         "(name = short-kebab-name, prompt = ONE isolated object, no text, no logos, no real people; the "
                         "episode's look is added for you). Look at every result (Read the PNG) and regenerate a bad one at "
                         "most twice. Reuse an asset across shots; about 10-25 for a few minutes of video. Shots use them with "
                         "S.image(name, x, y, h).")
        if sfx:
            steps.append("Sound effects, following the style's sound rules (which kinds, how often, on which cuts): "
                         "sfx_search and sfx_fetch a small set (every download counts against the owner's Epidemic Sound plan - "
                         f"reuse one effect for every similar moment), then write {b}/sfx.json [{{\"file\": \"sfx/...\", \"shot\": "
                         "\"shot-NNN\" (plays on that cut) or \"anchor\": \"words from the subtitles\", \"offset_s\": 0, \"gain_db\": "
                         "-18}]. Keep them under the voice: never more than one per cut.")
        steps.append(f"Call fusion_setup with shots=\"{b}/shots.json\" art=\"{b}/art.json\" voiceover=\"{self.vo['wav']}\" "
                     f"subtitles=\"{self.vo['srt']}\"{music}" + (f" sfx=\"{b}/sfx.json\"" if sfx else "") +
                     f" name=\"{s['name']}\". Fix whatever it reports and call it again.")
        lines += [f"{n}. {text}" for n, text in enumerate(steps, 4)]
        lines += ["Do not script any shot in this turn. Finish with the concept in one line and the number of shots."]
        if s.get("visual_notes"):
            lines += ["", "The owner's directions for the look: " + s["visual_notes"]]
        if task.notes:
            lines += ["", "Fix this first:", task.notes]
        return "\n".join(lines)

    def asset_names(self) -> list[str]:
        f = self.run.root / "assets" / "index.json"
        try:
            return sorted(json.loads(f.read_text(encoding="utf-8"))) if f.is_file() else []
        except (OSError, ValueError):
            return []

    def fusion_state(self) -> dict | None:
        f = self.run.root / self.base / "fusion.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except ValueError:
            return None

    def motion_ok(self) -> str:
        """'' when motion.py is usable, else why not."""
        f = self.run.root / self.base / "motion.py"
        if not f.is_file():
            return f"{self.base}/motion.py was not written"
        try:
            fusion_kit.check_motion(f.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            return str(e)
        return ""

    async def check_plan(self) -> tuple[bool, str, dict]:
        state = self.fusion_state()
        if not state:
            return False, f"fusion_setup has not built the Resolve project yet ({self.base}/fusion.json is missing)", {"usable": False}
        why = self.motion_ok()
        extra = {"project": state["project_name"], "detail": f"{len(state['shots'])} shots in Resolve project {state['project_name']}",
                 "files": [f"{self.base}/art.json", f"{self.base}/motion.py", f"{self.base}/shots.json"]}
        return (not why), why, extra

    def shots_prompt(self, task: Task, shots: list[dict]) -> str:
        b = self.base
        use_motion = not self.motion_ok()
        rows = [f"- {s['name']} ({s['frames'] / 60:.1f} s): {s['idea']}" + (f" | on screen: {s['text']}" if s.get("text") else "")
                + (f" | footage: {s['stock']} - build over it: S.over(S.media(), layer)" if s.get("stock") else "")
                for s in shots]
        lines = [f"Build these shots of the episode \"{self.spec['name']}\" in its Resolve project. Read {b}/art.json"
                 + (f" and {b}/motion.py (this episode's own helpers)" if use_motion else "") + " first"
                 + (", and the style files in " + f"{b}/style/" if self.profiles else "") + ".", *rows, "",
                 "Script EACH shot with its own mcp__davinci__run_script. The script starts with `from op_kit import *`"
                 + (" then `from ep_motion import *`" if use_motion else "") + ", then S = begin(\"shot-NNN\"), builds the "
                 "shot for its idea in this episode's style, and ends with result = finish(S, layer).",
                 "Every shot is a full, layered frame like the competitor's: S.backdrop() (never a flat empty colour), "
                 "ONE clear hero (a generated asset with S.image, a chart, a quote card or footage) filling 35-60% of the "
                 "frame height, then labels; captions at least size 0.02; all text fully on screen by 60% of the shot "
                 "(no word still typing at the end); nothing tiny floating in empty space. Real people and headlines are "
                 "S.person_tag / S.quote_card - never a real face, logo or imitated page."
                 + (f" Generated assets for this episode: {', '.join(self.asset_names())}." if self.asset_names() else ""),
                 "These scripts run straight away without an approval card, because they only touch this episode's own "
                 "Resolve project; anything else still asks. If Resolve reports an error, fix the script and run it again.",
                 "Finish with one line per shot you could not build and why."]
        if task.notes:
            lines += ["", "Fix this first:", task.notes]
        return "\n".join(lines)

    def check_prompt(self, task: Task) -> str:
        b = self.base
        lines = [f"Check the visuals of the episode \"{self.spec['name']}\". Call fusion_stills with state=\"{b}/fusion.json\", "
                 f"open the stills (Read the PNG files in {b}/stills/; judge the LATER still of each shot - the earlier one "
                 "may be mid-animation) and re-script (mcp__davinci__run_script, same rules: `from op_kit import *`"
                 + ("" if self.motion_ok() else ", `from ep_motion import *`") + ", begin, finish) every shot that is "
                 "empty, unclear, off-style, cramped, overlapping or cut off. Then call fusion_stills again.",
                 *([""] + self.style_lines() if self.profiles else [])]
        if self.profiles:
            lines += ["",
                      "Quality bar - the competitor (owner, after the first draft: \"competitor actually did very well in "
                      "terms of overall quality\"): fusion_stills also returns \"qa\": side-by-side sheets (top row = the "
                      "competitor's frames, below = our shots in the listed order), each shot's measured fill and colour "
                      "richness against the competitor's, and the shots that measure flat. LOOK at every sheet.",
                      f"Score every shot 1-10 against the competitor on: a clear hero object (a generated asset, a chart, a "
                      "card - not a lone icon), a full layered frame (backdrop + hero + labels), readable text, motion that "
                      "shows the idea, and the style. Re-script every shot under {QA_PASS} (more assets, a real backdrop, a "
                      "bigger hero, fewer floating words), then call fusion_stills again and score again.",
                      f"Write {b}/qa.json: {{\"shots\": [{{\"shot\": \"shot-001\", \"score\": 8, \"why\": \"...\"}}, ...]}} with "
                      "your FINAL scores for every shot."]
        lines += ["Finish with the shots you changed and anything still weak."]
        if task.notes:
            lines += ["", "Fix this first:", task.notes]
        return "\n".join(lines)

    def prepare_sub(self, task: Task, sub: SubRun) -> None:
        """Visuals turns get the episode's shot-script rules once the Resolve project exists, and a draft's
        heavy jobs OK'd in its start dialog (generated assets) for every worker turn."""
        sub.approved_jobs = set(self.spec.get("approved_jobs") or [])
        if task.id != "visuals" and not task.id.startswith("shots-"):
            return
        state = self.fusion_state()
        if state:
            sub.shot_scripts = ShotScripts(state["project_name"], {s["name"] for s in state["shots"]},
                                           self.run.root / self.base / "motion.py", self.run.root / self.base / "scripts",
                                           auto=self.draft, root=self.run.root,
                                           quality="draft" if self.draft else "final")

    async def draft_visuals(self) -> bool:
        self.vo["seconds"] = await asyncio.to_thread(self.deps.duration, self.vo["wav"])
        self.load_profiles()
        plan = Task(id="visuals-plan", title="Visuals: plan", providers=list(CLAUDE_ACCOUNTS),
                    files=[f"{self.base}/art.json", f"{self.base}/motion.py", f"{self.base}/shots.json"],
                    brief="art direction, motion library, shot list, Resolve project")
        self.tasks.append(plan)
        misses = 0
        while True:
            res = await self.run_worker(plan, self.plan_prompt(plan), VISUALS_NOTE, "worker")
            self.locks.release(plan.id)
            if res.get("error"):
                await self.blocked("visuals", f"the visuals worker failed: {plan.why}")
                return False
            ok, why, extra = await self.check_plan()
            if ok:
                break
            misses += 1
            if misses >= self.cfg.gate_rounds:
                if not extra.get("usable", True):
                    await self.blocked("visuals", f"no Resolve project after {misses} tries: {why}")
                    return False
                self.flags.append({"stage": "visuals", "why": f"motion.py not usable ({why}) - shots use the kit only", "report": ""})
                break
            plan.notes = why
            self.notice(f"Visuals plan: {why[:120]} - back to the worker.")
        plan.status = "done"
        unload = getattr(self.deps, "assets_unload", None)
        if unload:                                   # FLUX holds ~12 GB of the card; Resolve needs it for the shots
            await asyncio.to_thread(unload)
        state = self.fusion_state()
        self.vo["resolve_project"] = state["project_name"]
        shots = state["shots"]
        for n in range(0, len(shots), BATCH):
            part = shots[n:n + BATCH]
            t = Task(id=f"shots-{n // BATCH + 1}", title=f"Shots {part[0]['name'][5:]}-{part[-1]['name'][5:]}",
                     providers=list(CLAUDE_ACCOUNTS), brief="script these shots")
            self.tasks.append(t)
            await self.set_stage("visuals", "running", f"building shots {n + 1}-{n + len(part)} of {len(shots)}")
            res = await self.run_worker(t, self.shots_prompt(t, part), VISUALS_NOTE, "worker")
            self.locks.release(t.id)
            if res.get("error"):
                self.flags.append({"stage": "visuals", "why": f"{t.title}: the worker stopped ({t.why}) - the check turn retries them",
                                   "report": ""})
            t.status = "done"
        check = Task(id="visuals", title="Visuals (Fusion)", providers=list(CLAUDE_ACCOUNTS),
                     files=[f"{self.base}/art.json", f"{self.base}/shots.json"],
                     brief=(f"Fusion motion graphics at 1080p 60 fps. Read {self.base}/art.json and open the stills in "
                            f"{self.base}/stills/ (Read the PNG files). Pass only if: the art direction suits this script "
                            + ("and follows the chosen competitor style; " if self.profiles else "") +
                            "each shot makes its one idea easy to understand at a glance; type is clean and readable on a "
                            "phone; nothing overlaps or is cut off; and it does not look like a generic template."))
        extra = await self.worker_stage("visuals", check, self.check_prompt, VISUALS_NOTE, self.check_visuals)
        if extra is None:
            return False
        lengths = [s["frames"] / 60 for s in shots]
        target, _ = self.pace_target()
        median = round(statistics.median(lengths), 2) if lengths else None
        self.vo["pace"] = {"median": median, "target": target}
        if self.profiles and median and abs(median - target) / target > 0.4:
            await self.flag("visuals", f"pacing: median shot {median} s, the competitor style's is {target} s",
                            files=extra.get("files", []))
        return True

    # render (the app in a draft, otherwise the owner) -------------------------------------------
    async def render_draft(self) -> str | None:
        """Draft: Resolve renders the Episode timeline into the project (resolve_runner render_start /
        render_status). The file name, or None after blocking the run."""
        name = f"{self.base}/draft-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        try:
            job = await asyncio.to_thread(self.deps.render_start, f"{self.base}/fusion.json", name)
            while True:
                st = await asyncio.to_thread(self.deps.render_status, f"{self.base}/fusion.json", job)
                if st.get("failed"):
                    raise RuntimeError(st.get("error") or "Resolve stopped the render")
                if st.get("done"):
                    return name
                await self.set_stage("edit", "running", f"Resolve is rendering the draft: {st.get('progress', 0)}%")
                if self.run.stopped:
                    raise Stopped()
                await asyncio.sleep(self.deps.poll)
        except Stopped:
            raise
        except Exception as exc:                     # noqa: BLE001 - shown to the owner
            await self.blocked("edit", f"the draft render failed: {str(exc)[:300]}")
            return None

    async def do_edit(self) -> bool:
        video = self.vo.pop("rendered", "")
        if not video and self.draft and hasattr(self.deps, "render_start"):
            video = await self.render_draft()
            if not video:
                return False
        if not video:
            project = self.vo.get("resolve_project") or "the episode's project"
            await self.set_stage("edit", "waiting",
                                 f"Render the \"Episode\" timeline of Resolve project \"{project}\" (Deliver page: H.264, "
                                 "1920x1080, 60 fps) into this project's folder, then pick the file here.")
            self.state = "waiting_render"
            self.result = {"stage": "edit", "project": project}
            return False
        report = await self.deps.video_gate(video, self.vo["wav"])
        if not report["passed"]:
            if self.draft:
                self.vo["video"] = video
                await self.flag("edit", "video gate: " + report["summary"], report.get("report", ""), files=[video])
                return True
            await self.blocked("edit", "video gate: " + report["summary"], report.get("report", ""))
            return False
        self.vo["video"] = video
        await self.set_stage("edit", "passed", f"{video} - all video checks passed", report=report.get("report", ""), files=[video])
        return True

    # package ---------------------------------------------------------------
    def package_prompt(self, task: Task) -> str:
        s, b = self.spec, self.base
        lines = [f"Package the finished episode \"{s['name']}\" for upload. Video: {self.vo['video']}. Script: {s['script']}.",
                 f"1. Draw the thumbnail as a self-contained SVG at {b}/art/thumbnail.svg with viewBox \"0 0 1280 720\": "
                 "one big clear subject, at most 4 large words, strong contrast, readable when small; use the episode's "
                 f"art direction ({b}/art.json).",
                 f"2. Write {b}/package.json: {{\"name\": \"{slug(s['name'])}\", \"video\": \"{self.vo['video']}\", "
                 f"\"subtitles\": \"{self.vo['srt']}\", \"script\": \"{s['script']}\", \"title\": \"...\", "
                 "\"title_options\": [\"3-5 options\"], \"description\": \"2-4 short paragraphs, no chapter list (it is "
                 "added for you)\", \"tags\": [\"...\"], \"thumbnail_svg\": \"" + f"{b}/art/thumbnail.svg\"}}.",
                 "Titles: at most 100 characters, specific and true to the video - no clickbait the video doesn't pay "
                 "off. Tags: at most 500 characters in all. No < or > anywhere.",
                 f"3. Call package_episode with spec \"{b}/package.json\" and fix anything its gate reports.",
                 "Finish with the chosen title and one line on why."]
        if task.notes:
            lines += ["", "Changes required before this can pass:", task.notes]
        return "\n".join(lines)

    async def check_package(self) -> tuple[bool, str, dict]:
        try:
            out = await asyncio.to_thread(self.deps.package, f"{self.base}/package.json")
        except Exception as exc:                     # noqa: BLE001
            return False, str(exc)[:600], {}
        extra = {"detail": f"{out['folder']} - {len(out['chapters'])} chapters", "files": [out["folder"]],
                 "report": out["gate"]["report"]}
        return out["gate"]["passed"], out["gate"]["summary"], extra

    async def do_package(self) -> bool:
        task = Task(id="package", title="Package", files=[f"{self.base}/package.json"],
                    brief=("Title options, description, tags and thumbnail. Pass only if the title is specific and honest, "
                           "the description matches the video and the thumbnail is readable at small size "
                           "(look at thumbnail.png in the bundle under episode/)."))
        return await self.worker_stage("package", task, self.package_prompt, PACKAGER_NOTE, self.check_package) is not None

    def final_prompt(self) -> str:
        rows = "\n".join(f"- {s['title']}: {s['status']} - {s['detail']}" for s in self.stages)
        if self.draft:
            flags = "\n".join(f"- {f['stage']}: {f['why']}" for f in self.flags) or "- none"
            pace = self.vo.get("pace") or {}
            return (f"The DRAFT of the episode \"{self.spec['name']}\" is finished:\n{rows}\n\nFlagged for review:\n{flags}\n\n"
                    f"Draft video: {self.vo.get('video', '(not rendered)')}. Resolve project: {self.vo.get('resolve_project', '?')}. "
                    f"Stills: {self.base}/stills/. Shot scripts that ran: {self.base}/scripts/. "
                    + (f"Pacing: median shot {pace.get('median')} s against the competitor style's {pace.get('target')} s. "
                       if pace else "")
                    + "\n\nWrite the draft review report for the owner in plain words: where to watch the draft, the flagged "
                    "points first (what each means and what you would change), anything you are unsure about. This is the "
                    "first review, not a publish - keep it short and concrete.")
        return (f"The episode \"{self.spec['name']}\" went through every stage:\n{rows}\n\nWrite the final report for the owner "
                "in plain words: where the finished bundle is, what to look at before publishing, and anything you are "
                "unsure about. Keep it short. The owner watches the whole video before publishing.")
