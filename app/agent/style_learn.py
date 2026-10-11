"""Learn an editing style from competitor videos (owner, 2026-10-11: "once i paste the link it
should ... see the video literally it should learn the editing style").

    watch   the app downloads each video (small copy), measures cuts / picture / sound and saves
            keyframes + contact sheets (style_probe; CPU, no AI). Numbers over all videos are
            medians computed by code.
    learn   one Claude worker turn LOOKS at the contact sheets (Read on the images), reads the
            numbers and transcripts, and writes profile.json; the app checks it and stores the
            measured numbers in it itself (the AI never averages or invents a number).

The profile is the owner's to review; it becomes guidance for agents only when the owner
approves it in the Competitors tab.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from .. import styles
from ..competitors import FetchError
from .team import Stopped, Task, Team, TeamConfig

STAGES = (("watch", "Watch the videos"), ("learn", "Learn the style"))
CLAUDE_FAMILY = ["claude", "anthropic_api"]        # it must look at images
LEARN_NOTE = ("You are the EDITING-STYLE ANALYST in One Production. You study competitor videos the owner chose "
              "so new episodes can be edited with the same craft and rhythm - never copying their branding, "
              "characters, logos, exact layouts or wording.")


class StyleRun(Team):
    def __init__(self, pid: str, name: str, items: list[dict], run, deps, config: TeamConfig,
                 state_file: Path | None = None, per_channel: int = 3, keep_video: bool = False):
        super().__init__(pid, f"Learn style: {name}", run, deps, config, state_file)
        self.name, self.items, self.per_channel, self.keep_video = name, items, per_channel, keep_video
        self.base = f"style/{styles.slug(name)}"
        self.state = "running"
        self.stages = [{"id": i, "title": t, "status": "waiting", "detail": ""} for i, t in STAGES]
        self.videos: list[dict] = []
        self.problems: list[dict] = []
        self.result: dict = {}

    def snapshot(self) -> dict:
        return {"kind": "style", "id": self.id, "state": self.state, "name": self.name, "base": self.base,
                "stages": self.stages, "videos": self.videos, "problems": self.problems, "result": self.result,
                "tasks": [t.public() for t in self.tasks], "paused_why": self.paused_why, "started": self.started}

    def publish(self) -> None:
        snap = self.snapshot()
        self.run.emit({"t": "style", "style": snap})
        if self.state_file:
            try:
                tmp = self.state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=1), encoding="utf-8")
                tmp.replace(self.state_file)
            except OSError:
                pass

    async def set_stage(self, sid: str, status: str, detail: str = "") -> None:
        next(s for s in self.stages if s["id"] == sid).update(status=status, detail=detail)
        await self._changed()

    async def stop_here(self, sid: str, why: str) -> dict:
        await self.set_stage(sid, "needs_you", why)
        self.state, self.result = "needs_you", {"stage": sid, "why": why}
        return self.snapshot()

    # ------------------------------------------------ the flow
    async def go(self) -> dict:
        try:
            await self._changed()
            if not await self.watch():
                return self.snapshot()
            if not await self.learn():
                return self.snapshot()
            self.state = "done"
        except (Stopped, asyncio.CancelledError):
            self.state = "stopped"
            for s in self.stages:
                if s["status"] in ("waiting", "running"):
                    s["status"] = "stopped"
        finally:
            self.publish()
        return self.snapshot()

    async def watch(self) -> bool:
        await self.set_stage("watch", "running", "finding the videos")
        files = self.deps.files_dir()
        work = self.deps.cache_dir()
        ids: list[str] = []
        for it in self.items:
            if it["kind"] == "video":
                ids.append(it["key"])
                continue
            try:
                ups = await self.deps.uploads(it, work)
            except FetchError as e:
                self.problems.append({"link": it["url"], "why": str(e)})
                continue
            ids += [u["id"] for u in sorted(ups, key=lambda u: -(u["views"] or 0))[: self.per_channel]]
        ids = list(dict.fromkeys(ids))
        measures = []
        for n, vid in enumerate(ids, 1):
            if self.run.stopped:
                raise Stopped()
            await self.set_stage("watch", "running", f"video {n}/{len(ids)}: downloading")
            vwork = work / vid
            try:
                got = await self.deps.download(vid, vwork)
            except FetchError as e:
                self.problems.append({"link": f"https://www.youtube.com/watch?v={vid}", "why": str(e)})
                continue
            await self.set_stage("watch", "running", f"video {n}/{len(ids)}: measuring cuts, picture and sound")
            out = files / self.base / vid
            try:
                m = await asyncio.to_thread(self.deps.analyse, got["video"], out, got["text"], got["meta"])
            except Exception as e:                       # noqa: BLE001 - one bad video doesn't stop the rest
                self.problems.append({"link": f"https://www.youtube.com/watch?v={vid}", "why": f"could not measure: {e}"[:300]})
                continue
            finally:
                if not self.keep_video:
                    shutil.rmtree(vwork, ignore_errors=True)
            measures.append(m)
            self.videos.append({"id": vid, "title": got["meta"].get("title") or vid, "channel": got["meta"].get("channel"),
                                "folder": f"{self.base}/{vid}", "shots": m["pace"]["shots"],
                                "cuts_per_min": m["pace"]["cuts_per_min"], "sheets": [f"{self.base}/{vid}/{s}" for s in m["sheets"]]})
            await self._changed()
        if not measures:
            await self.stop_here("watch", "no video could be watched: " +
                                 "; ".join(p["why"] for p in self.problems[:3]) if self.problems else "no YouTube videos found")
            return False
        self.numbers = self.deps.combine(measures)
        (files / self.base / "numbers.json").write_text(json.dumps(self.numbers, indent=1), encoding="utf-8")
        await self.set_stage("watch", "passed", f"{len(measures)} video(s) measured")
        return True

    def learn_prompt(self, task: Task) -> str:
        b = self.base
        lines = [f"Learn the editing style of these competitor videos. Everything is in {b}/:",
                 f"- {b}/numbers.json: medians over all videos, measured by the app (trust these numbers).",
                 *[f"- {v['folder']}/: measure.json (this video's numbers: cut times, per-shot motion, colours, sound), "
                   f"transcript.txt, frames/ (one still per shot, in order), sheets/ (contact sheets of those stills: "
                   f"{', '.join(Path(s).name for s in v['sheets'][:12])}) - \"{v['title']}\"" for v in self.videos], "",
                 "1. LOOK at every contact sheet (Read the image files) and at single frames where you need detail. "
                 "Watch for: what each shot shows (kinetic text, diagrams, maps, charts, stock footage, footage with "
                 "graphics on top, characters, 3D), how text is set and animated in, colour and contrast, framing, "
                 "transitions, how often the picture changes, and how the hook differs from the rest.",
                 "2. Read the transcripts next to measure.json's cut times to see what the visuals do for the words "
                 "(a cut on every new claim? numbers drawn as they are said?).",
                 f"3. Write {b}/profile.json, a JSON object with exactly these keys:",
                 '   "summary": 3-5 sentences an editor can work from;',
                 '   "pacing": {"hook": "...", "body": "...", "beats": "how sections open and close", "notes": "..."};',
                 '   "visual_language": [{"kind": "kinetic type | diagram | map | chart | stock footage | footage + overlay | '
                 'character | 3D | other", "share": percent of shots, "how": "how it is built and moves", "frames": '
                 '["frames/... files that show it"]}];',
                 '   "typography": {"fonts_look": "...", "case": "...", "placement": "...", "animate_in": "..."};',
                 '   "color": {"approach": "...", "contrast": "...", "accents": "..."} (describe the approach - do not copy '
                 'a brand palette);',
                 '   "motion": {"eases": "...", "camera": "...", "transitions": "...", "how_often": "..."};',
                 '   "sound": {"music": "...", "sfx": "which kinds and when they hit", "level": "..."};',
                 '   "do": ["short, concrete rules an editor follows"], "dont": ["what this style avoids"].',
                 "Base every claim on what you saw or on the numbers. Do not repeat the numbers in your own words as "
                 "different values - the app adds numbers.json to the profile itself.",
                 "Finish with two lines: the style in one sentence, and anything you could not tell from the frames."]
        if task.notes:
            lines += ["", "Fix this first:", task.notes]
        return "\n".join(lines)

    async def learn(self) -> bool:
        await self.set_stage("learn", "running", "the AI is looking at the frames")
        task = Task(id="style", title="Learn the style", brief="editing-style profile", files=[f"{self.base}/profile.json"],
                    providers=list(CLAUDE_FAMILY))
        self.tasks.append(task)
        target = self.deps.files_dir() / self.base / "profile.json"
        for attempt in range(3):
            res = await self.run_worker(task, self.learn_prompt(task), LEARN_NOTE, "worker")
            self.locks.release(task.id)
            if res.get("error"):
                await self.stop_here("learn", f"the AI could not finish: {task.why or res['error'].message}")
                return False
            try:
                profile = styles.check_profile(json.loads(target.read_text(encoding="utf-8-sig")))
            except FileNotFoundError:
                why = f"{self.base}/profile.json was not written"
            except (ValueError, styles.StyleError) as e:
                why = f"profile.json: {e}"
            else:
                profile["measured"] = self.numbers                    # numbers come from code, never the AI
                profile["videos"] = [{k: v[k] for k in ("id", "title", "channel")} for v in self.videos]
                target.write_text(json.dumps(profile, indent=1), encoding="utf-8")
                entry = self.deps.styles.add(self.name, self.pid, self.base, self.videos)
                self.result = {"style": entry["id"], "profile": f"{self.base}/profile.json", "summary": profile["summary"]}
                task.status = "done"
                await self.set_stage("learn", "passed", profile["summary"][:200])
                return True
            task.notes = why
            self.notice(f"Style profile: {why} - asking again.")
        await self.stop_here("learn", task.notes)
        return False
