"""Episode run (P10): a finished script -> voiceover -> visuals -> edit -> upload bundle.

    script    the owner's script file (Script Studio writes and reviews it)          app, no AI
    voice     a voiceover job (or one the owner already made), then the voice gate   app, GPU
    visuals   a worker draws SVG shots + a shots list; build_animation must succeed;   worker + master
              then the master reviews (2 rounds)
    edit      the ffmpeg editor renders the edit list, then the video gate             app, CPU
    package   a worker writes title options, description, tags and a thumbnail;      worker + master
              the bundle must pass the package gate; then the master reviews
    final     the master's short report for the owner

A numeric gate that fails sends its numbers back to the worker as the review notes (at most
`gate_rounds` times); a gate the app's own stages fail stops the run with "needs you" - nothing
moves on past a failed gate. The heavy jobs (GPU voice, CPU render) are approved by the owner
when the run starts. Same accounts, limits, handoffs, pause and Stop as the team (team.py).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .team import Stopped, Task, Team, TeamConfig

STAGES = (("script", "Script"), ("voice", "Voiceover"), ("visuals", "Visuals"), ("edit", "Edit"), ("package", "Package"))


@dataclass
class EpisodeConfig(TeamConfig):
    gate_rounds: int = 3             # a worker may miss its numeric gate this often before it's your call
    shot_seconds: float = 3.0        # aimed-for average shot length (the stickman style cuts every 2-4 s)


VISUALS_NOTE = ("You are the ANIMATOR in One Production's episode run. You draw every shot yourself as SVG code "
                "(no image models, no downloads) and time it to the voiceover with anchor words from the script.")
PACKAGER_NOTE = ("You are the PACKAGER in One Production's episode run. You write what the upload needs: honest, "
                 "specific titles and a description that matches the video, tags, and a thumbnail drawn as SVG.")


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

    # ------------------------------------------------ reporting
    def snapshot(self) -> dict:
        return {"kind": "episode", "id": self.id, "state": self.state, "spec": self.spec, "stages": self.stages,
                "paused_why": self.paused_why, "resets_at": self.resets_at, "master": (self.master_acc or {}).get("id"),
                "config": {k: getattr(self.cfg, k) for k in ("master_model", "master_effort", "worker_model", "worker_effort",
                                                              "review_rounds", "gate_rounds")},
                "tasks": [t.public() for t in self.tasks], "master_tokens": self.master_tokens, "final": self.final,
                "result": self.result, "notes": self.notes[-6:], "started": self.started, "base": self.base}

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
    async def go(self) -> dict:
        try:
            await self._changed()
            for sid, step in (("script", self.do_script), ("voice", self.do_voice), ("visuals", self.do_visuals),
                              ("edit", self.do_edit), ("package", self.do_package)):
                if self.run.stopped:
                    raise Stopped()
                await self.set_stage(sid, "running")
                ok = await step()
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
            if self.vo.get("render_job"):
                self.deps.cancel_render(self.vo["render_job"])
        finally:
            for t in self.tasks:
                self.locks.release(t.id)
            self.publish()
        return self.snapshot()

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

    # visuals ------------------------------------------------------------
    def visuals_prompt(self, task: Task) -> str:
        s, b = self.spec, self.base
        lines = [f"Draw the visuals for the episode \"{s['name']}\".",
                 f"Script: {s['script']}. Voiceover: {self.vo['wav']} ({self.vo.get('seconds', 0):.0f} s). Subtitles with the spoken "
                 f"words and times: {self.vo['srt']}.", "",
                 f"1. Draw each shot as a self-contained SVG at {b}/art/sNNN.svg with viewBox \"0 0 1920 1080\": full-bleed "
                 "scene background, bold dark outlines, big subjects filling most of the frame, a few large labels; "
                 "no <script>, no event handlers, no links to other files (images only as data: URIs). Reuse a "
                 "drawing for recurring scenes instead of redrawing it.",
                 f"2. Write {b}/shots.json: {{\"voiceover\": \"{self.vo['wav']}\", \"subtitles\": \"{self.vo['srt']}\", "
                 f"\"size\": \"{s.get('size') or '1920x1080'}\", \"fps\": 30, \"output\": \"{b}/{slug(s['name'])}.mp4\", "
                 f"\"burn_subtitles\": {json.dumps(bool(s.get('burn_subtitles')))}"
                 + (f", \"music\": {{\"file\": \"{s['music']}\", \"db\": -22, \"duck\": true}}" if s.get("music") else "")
                 + ", \"shots\": [{\"svg\": \"...\", \"motion\": \"push\"}, {\"svg\": \"...\", \"anchor\": \"exact words\", "
                 "\"motion\": \"pan_right\"}, {\"card\": {\"text\": \"1986\", \"style\": \"date\"}, \"anchor\": \"...\"}]}. "
                 "The first shot starts at 0; every other shot starts when its anchor words (3-6 consecutive words, "
                 "exactly as in the subtitles) are spoken. Motions: push, pull, pan_left, pan_right, none. Cards: "
                 "title, date, label.",
                 f"3. Aim for a new shot about every {self.cfg.shot_seconds:g} seconds and change it whenever the "
                 "narration changes subject.",
                 f"4. Call build_animation with shots \"{b}/shots.json\" and edit \"{b}/edit.json\". Fix whatever "
                 "it reports and call it again until it succeeds.",
                 "Finish with a short summary: number of shots and drawings, and anything you were unsure about."]
        if s.get("visual_notes"):
            lines += ["", "The owner's directions for the look: " + s["visual_notes"]]
        if task.notes:
            lines += ["", "Changes required before this can pass:", task.notes]
        return "\n".join(lines)

    async def check_visuals(self) -> tuple[bool, str, dict]:
        try:
            out = await asyncio.to_thread(self.deps.build_animation, f"{self.base}/shots.json", f"{self.base}/edit.json")
        except Exception as exc:                     # noqa: BLE001 - goes back to the worker
            return False, str(exc)[:600], {}
        return True, "", {"detail": f"{out['shots']} shots, {out['drawings']} drawings, median {out['median_shot']} s",
                          "files": [out["edit"], out["frames_folder"]]}

    async def do_visuals(self) -> bool:
        task = Task(id="visuals", title="Visuals", files=[f"{self.base}/shots.json"],
                    brief=("Draw every shot as SVG, list the shots with anchor words and build the edit list. Pass only "
                           f"if the drawings are clear and on-topic for what is said at that moment (look at a few frames "
                           f"in {self.base} frames folder) and the shots change often enough."))
        self.vo["seconds"] = await asyncio.to_thread(self.deps.duration, self.vo["wav"])
        return await self.worker_stage("visuals", task, self.visuals_prompt, VISUALS_NOTE, self.check_visuals) is not None

    # edit ------------------------------------------------------------------
    async def do_edit(self) -> bool:
        try:
            job = await asyncio.to_thread(self.deps.start_render, f"{self.base}/edit.json")
        except Exception as exc:                     # noqa: BLE001
            await self.blocked("edit", f"the render could not start: {exc}")
            return False
        self.vo["render_job"] = job["id"]
        while job["status"] == "rendering":
            await self.set_stage("edit", "running", f"{job.get('step')} ({int(100 * (job.get('progress') or 0))}%)")
            await asyncio.sleep(self.deps.poll)
            if self.run.stopped:
                raise Stopped()
            job = self.deps.render_job(job["id"])
        self.vo.pop("render_job", None)
        if job["status"] != "done":
            await self.blocked("edit", f"the render {job['status']}: {job.get('error')}")
            return False
        gate = job.get("gate") or {}
        self.vo["video"] = job["output"]
        if not gate.get("passed"):
            await self.blocked("edit", "video gate: " + (gate.get("summary") or "no report"), gate.get("report", ""))
            return False
        await self.set_stage("edit", "passed", f"{job['output']} - all video checks passed", report=gate["report"], files=[job["output"]])
        return True

    # package ---------------------------------------------------------------
    def package_prompt(self, task: Task) -> str:
        s, b = self.spec, self.base
        lines = [f"Package the finished episode \"{s['name']}\" for upload. Video: {self.vo['video']}. Script: {s['script']}.",
                 f"1. Draw the thumbnail as a self-contained SVG at {b}/art/thumbnail.svg with viewBox \"0 0 1280 720\": "
                 "one big clear subject, at most 4 large words, strong contrast, readable when small.",
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
