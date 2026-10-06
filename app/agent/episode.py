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
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..fusion_kit import FONTS
from .team import Stopped, Task, Team, TeamConfig

STAGES = (("script", "Script"), ("voice", "Voiceover"), ("visuals", "Visuals"), ("edit", "Render"), ("package", "Package"))
ORDER = [s for s, _ in STAGES]


@dataclass
class EpisodeConfig(TeamConfig):
    gate_rounds: int = 3             # a worker may miss its numeric gate this often before it's your call
    shot_seconds: float = 3.5        # aimed-for average shot length


VISUALS_NOTE = ("You are the MOTION DESIGNER in One Production's episode run. You make every shot in DaVinci Resolve's "
                "Fusion page by script (no drawings, no image models). Every episode gets its own art direction, chosen "
                "so this script's ideas are as easy as possible to understand; never fall back to a generic template look. "
                "Quality bar: 1080p 60 fps, eased motion with motion blur, clean type, nothing overlapping or cut off.")
PACKAGER_NOTE = ("You are the PACKAGER in One Production's episode run. You write what the upload needs: honest, "
                 "specific titles and a description that matches the video, tags, and a thumbnail drawn as SVG.")
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

    @classmethod
    def from_snapshot(cls, pid: str, snap: dict, run, deps, config: EpisodeConfig, state_file: Path | None = None) -> "EpisodeRun":
        """Pick a run up again where it waited (the owner's render) - e.g. after an app restart."""
        ep = cls(pid, snap["spec"], run, deps, config, state_file)
        ep.stages = snap.get("stages") or ep.stages
        ep.vo = dict(snap.get("vo") or {})
        ep.base = snap.get("base") or ep.base
        ep.state = snap.get("state") or "running"
        ep.notes = list(snap.get("notes") or [])
        return ep

    # ------------------------------------------------ reporting
    def snapshot(self) -> dict:
        return {"kind": "episode", "id": self.id, "state": self.state, "spec": self.spec, "stages": self.stages,
                "paused_why": self.paused_why, "resets_at": self.resets_at, "master": (self.master_acc or {}).get("id"),
                "config": {k: getattr(self.cfg, k) for k in ("master_model", "master_effort", "worker_model", "worker_effort",
                                                              "review_rounds", "gate_rounds")},
                "tasks": [t.public() for t in self.tasks], "master_tokens": self.master_tokens, "final": self.final,
                "result": self.result, "notes": self.notes[-6:], "started": self.started, "base": self.base, "vo": self.vo}

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

    async def resume(self, video: str) -> dict:
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
                await self.blocked(sid, f"the worker failed: {task.why}")
                return None
            task.output = res.get("text", "")
            await self.set_stage(sid, "running", "checking the result")
            ok, why, extra = await check()
            if not ok:
                misses += 1
                if misses >= self.cfg.gate_rounds:
                    await self.blocked(sid, f"still failing its check after {misses} tries: {why}", extra.get("report", ""))
                    return None
                task.notes = "The automatic check failed - fix this first: " + why
                self.notice(f"{task.title}: check failed ({why[:120]}) - back to the worker.")
                continue
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
        return True, "", {"detail": f"{out['shots']} Fusion shots in Resolve project {out['project']}",
                          "files": [out["folder"], f"{self.base}/art.json", f"{self.base}/shots.json"],
                          "project": out["project"]}

    async def do_visuals(self) -> bool:
        ok, why = self.deps.fusion_ready()
        if not ok:
            await self.blocked("visuals", why)
            return False
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

    # render (the owner) ---------------------------------------------------------------
    async def do_edit(self) -> bool:
        video = self.vo.pop("rendered", "")
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
        return (f"The episode \"{self.spec['name']}\" went through every stage:\n{rows}\n\nWrite the final report for the owner "
                "in plain words: where the finished bundle is, what to look at before publishing, and anything you are "
                "unsure about. Keep it short. The owner watches the whole video before publishing.")
