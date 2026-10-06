"""Post-publish learning loop (P12): what a published episode's audience retention says, turned into
lesson CANDIDATES the owner approves or rejects in Knowledge and learning. Nothing changes how the AI
works until the owner approves a lesson.

Input: YouTube Studio > Analytics > Advanced mode > Audience retention, exported as CSV (a column
for the position in the video - percent, seconds or m:ss - and one for the share of viewers still
watching - percent or 0-1). Headers differ between exports, so the columns are found by name.

    analyse()  no AI: the steepest drops (percentage points lost per 10 s of video, outside the
               first 15 s where every video drops), each mapped to the chapter, the Fusion shot and
               the words spoken at that moment (episode bundle metadata, fusion.json, subtitles).
    draft()    the AI (one plain call) turns the findings into at most 3 concrete, testable lessons;
               without an account it falls back to plain rule-based wording.
Reports land in <episode work folder>/insights/<time>.json.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path

from . import media
from .storage import NotFound

INTRO = 15.0          # every video loses viewers in the first seconds; that isn't a lesson
WINDOW = 10.0         # drops are measured per 10 s of video
MAX_FINDINGS = 3
MIN_RATE = 1.5       # points per 10 s; slower losses are the normal slope


class InsightError(Exception):
    pass


def _num(text: str) -> float | None:
    t = (text or "").strip().replace("%", "").replace(",", ".")
    if re.fullmatch(r"\d+:\d{1,2}(:\d{1,2})?", t):
        parts = [int(p) for p in t.split(":")]
        return float(sum(v * 60 ** i for i, v in enumerate(reversed(parts))))
    try:
        return float(t)
    except ValueError:
        return None


def parse_retention(text: str, seconds: float) -> list[tuple[float, float]]:
    """CSV -> [(second, percent still watching)], sorted. Position may be %, seconds or m:ss."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if len(rows) < 3:
        raise InsightError("The retention file has too few rows - export the Audience retention report as CSV.")
    head = [h.strip().lower() for h in rows[0]]
    pos = next((i for i, h in enumerate(head) if "position" in h or "time" in h or h in ("second", "seconds")), None)
    rets = [i for i, h in enumerate(head) if i != pos and ("retention" in h or "watching" in h or "audience" in h)]
    ret = next((i for i in rets if "absolute" in head[i]), rets[0] if rets else None)   # not "relative" (vs. other videos)
    if pos is None or ret is None or pos == ret:
        raise InsightError(f"Can't find the position and retention columns in: {', '.join(rows[0])}.")
    pos_is_pct = "%" in head[pos] or "percent" in head[pos]
    points = []
    for r in rows[1:]:
        if len(r) <= max(pos, ret):
            continue
        p, v = _num(r[pos]), _num(r[ret])
        if p is None or v is None:
            continue
        if ":" in r[pos]:
            sec = p
        elif pos_is_pct or (p <= 100 and seconds > 100 and len(rows) <= 105):
            sec = p / 100.0 * seconds
        else:
            sec = p
        points.append((round(sec, 2), v))
    if len(points) < 3:
        raise InsightError("No usable rows in the retention file.")
    if max(v for _, v in points) <= 1.5:                  # 0-1 ratios -> percent
        points = [(s, v * 100) for s, v in points]
    return sorted(points)


def drops(points: list[tuple[float, float]], intro: float = INTRO, window: float = WINDOW) -> list[dict]:
    """The steepest losses after the intro, one per region, steepest first."""
    found = []
    for (s0, v0), (s1, v1) in zip(points, points[1:]):
        if s1 <= intro or s1 <= s0:
            continue
        rate = (v0 - v1) / (s1 - s0) * window          # points lost per 10 s
        if rate > 0:
            found.append({"start": s0, "end": s1, "from": round(v0, 1), "to": round(v1, 1), "rate": round(rate, 2)})
    rates = sorted(d["rate"] for d in found)
    floor = max(MIN_RATE, 2 * rates[len(rates) // 2]) if rates else MIN_RATE    # a cliff, not the steady slope
    found = sorted((d for d in found if d["rate"] >= floor), key=lambda d: -d["rate"])
    picked = []
    for d in found:
        if all(abs(d["start"] - p["start"]) > window * 2 for p in picked):
            picked.append(d)
        if len(picked) == MAX_FINDINGS:
            break
    return picked


def stamp(sec: float) -> str:
    return f"{int(sec) // 60}:{int(sec) % 60:02d}"


class Insights:
    def __init__(self, store, data_dir: Path, learning):
        self.store, self.data_dir, self.learning = store, data_dir, learning

    def _file(self, pid, name) -> Path:
        try:
            return self.store.file_path(pid, name)
        except NotFound:
            raise InsightError(f"{name} is not a file in this project.") from None

    def episode(self, pid: str) -> dict:
        """The last episode's pieces: voiceover, subtitles, chapters (bundle) and shots (fusion.json)."""
        f = self.store.project_dir(pid) / "episode.json"
        if not f.exists():
            raise InsightError("This project has no finished episode yet.")
        snap = json.loads(f.read_text(encoding="utf-8"))
        vo, base = snap.get("vo") or {}, snap.get("base") or ""
        files = self.store.files_dir(pid)
        meta = {}
        folder = next((s.get("files") or [""] for s in snap.get("stages", []) if s.get("id") == "package"), [""])[0]
        if folder and (files / folder / "metadata.json").is_file():
            meta = json.loads((files / folder / "metadata.json").read_text(encoding="utf-8"))
        fusion = files / base / "fusion.json"
        plan = json.loads(fusion.read_text(encoding="utf-8")) if base and fusion.is_file() else {}
        shots, fps = plan.get("shots", []), plan.get("fps", 60)
        cues = []
        if vo.get("srt"):
            try:
                cues = media.srt_cues(self._file(pid, vo["srt"]).read_text(encoding="utf-8-sig", errors="replace"))
            except InsightError:
                cues = []
        return {"name": (snap.get("spec") or {}).get("name", ""), "base": base, "vo": vo, "chapters": meta.get("chapters") or [],
                "shots": [{**s, "start_s": s["start"] / fps, "end_s": (s["start"] + s["frames"]) / fps} for s in shots],
                "cues": cues, "title": meta.get("title", ""), "seconds": float(plan.get("seconds") or 0)}

    @staticmethod
    def _at(ep: dict, sec: float) -> dict:
        chapter = None
        for c in ep["chapters"]:
            t = _num(str(c.get("time", ""))) or 0
            if t <= sec:
                chapter = c.get("title")
        shot = next((s for s in ep["shots"] if s["start_s"] <= sec < s["end_s"]), None)
        words = " ".join(c["text"] for c in ep["cues"] if c["end"] >= sec - 2 and c["start"] <= sec + 4)[:300]
        return {"chapter": chapter, "shot": shot and {"name": shot["name"], "idea": shot.get("idea")}, "words": words}

    def analyse(self, pid: str, retention_name: str) -> dict:
        ep = self.episode(pid)
        seconds = ep["seconds"]
        if not seconds and ep["vo"].get("wav"):
            ffmpeg = media.find_ffmpeg(self.data_dir)
            if ffmpeg:
                try:
                    seconds = media.duration(ffmpeg, self._file(pid, ep["vo"]["wav"]))
                except Exception:                        # noqa: BLE001 - fall back to the curve's own length
                    seconds = 0.0
        text = self._file(pid, retention_name).read_text(encoding="utf-8-sig", errors="replace")
        points = parse_retention(text, seconds or 600.0)
        found = drops(points)
        for d in found:
            d.update(self._at(ep, d["start"]), at=stamp(d["start"]))
        at30 = next((v for s, v in points if s >= 30), points[-1][1])
        report = {"episode": ep["name"], "title": ep["title"], "retention_file": retention_name, "made": time.time(),
                  "points": len(points), "at_30s": round(at30, 1), "end": round(points[-1][1], 1), "drops": found}
        folder = self.store.files_dir(pid) / (ep["base"] or "work") / "insights"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("retention-%Y%m%d-%H%M%S.json")
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        report["report"] = path.relative_to(self.store.files_dir(pid)).as_posix()
        return report

    @staticmethod
    def prompt(report: dict) -> str:
        lines = [f"Audience retention of the published episode \"{report['episode']}\" ({report['title']}): "
                 f"{report['at_30s']}% still watching at 0:30, {report['end']}% at the end.",
                 "The steepest drops after the intro (points lost per 10 s of video):"]
        for d in report["drops"]:
            shot = d.get("shot") or {}
            lines.append(f"- {d['at']}: {d['from']}% -> {d['to']}% ({d['rate']}/10 s); chapter \"{d.get('chapter') or '?'}\"; "
                         f"shot {shot.get('name', '?')}: {shot.get('idea', '?')}; words: \"{d.get('words', '')}\"")
        lines += ["", "Write at most 3 lessons for the NEXT scripts and visuals of this channel. Each must be concrete and "
                  "testable (what to do differently, where), grounded in one of the drops above - no generic advice. "
                  "Answer with JSON only: {\"lessons\": [{\"title\": \"short\", \"text\": \"the lesson in 1-3 sentences\", "
                  "\"drop\": \"m:ss\"}]}"]
        return "\n".join(lines)

    @staticmethod
    def fallback(report: dict) -> list[dict]:
        out = []
        for d in report["drops"]:
            shot = (d.get("shot") or {}).get("idea") or "this part"
            out.append({"title": f"Viewers left at {d['at']}"[:100],
                        "text": f"Retention fell from {d['from']}% to {d['to']}% around {d['at']} (chapter "
                                f"\"{d.get('chapter') or '?'}\", shot: {shot}). In the next scripts, tighten or re-order "
                                f"similar passages and get to the point faster there.", "drop": d["at"]})
        return out

    def save_candidates(self, pid: str, report: dict, lessons: list[dict], source: str) -> list[dict]:
        saved = []
        for l in lessons[:MAX_FINDINGS]:
            title, text = str(l.get("title") or "").strip(), str(l.get("text") or "").strip()
            if not title or not text:
                continue
            saved.append(self.learning.save(pid, title[:100], text[:3000], kind="candidate",
                                            evidence={"type": "retention_insight", "report": report["report"],
                                                      "drop": l.get("drop"), "drafted_by": source},
                                            automatic=True))         # a forgotten lesson is not brought back
        return saved


def parse_lessons(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    return [l for l in data.get("lessons", []) if isinstance(l, dict)]
