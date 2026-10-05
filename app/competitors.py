"""Competitor transcripts for Script Studio (P10): links in any format -> captions -> numbers.

No AI and no cost here. yt-dlp (a dependency of the app, run as `python -m yt_dlp`) reads a
channel's upload list and a video's captions; nothing is downloaded but the caption text.
Verified on yt-dlp 2026.07/08 (2026-10-05): `--flat-playlist -J <channel>/videos` lists uploads
with view_count and duration; `--skip-download --write-subs --write-auto-subs --sub-format vtt`
writes <id>.<lang>.vtt (manual captions as `en`, auto ones as `en`/`en-orig` with rolling lines).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import sys
import zipfile
from pathlib import Path

from .providers.cli import run_capture
from .scripts import read_text_file

_VIDEO = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^\s\"'<>]*?&)?v=|shorts/|live/|embed/)|youtu\.be/)"
                    r"([A-Za-z0-9_-]{11})")
_CHANNEL = re.compile(r"youtube\.com/(@[A-Za-z0-9._\-]+|channel/UC[A-Za-z0-9_-]{22}|c/[^/\s?\"'<>]+|user/[^/\s?\"'<>]+)")
_PLAYLIST = re.compile(r"youtube\.com/playlist\?(?:[^\s\"'<>]*?&)?list=([A-Za-z0-9_-]{10,})")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+|(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s\"'<>)\]]+", re.I)
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")
LIST_TYPES = {".txt", ".md", ".csv", ".tsv", ".docx", ".xlsx", ".json", ".html", ".htm"}


# ---------------------------------------------------------------- 1. links from anything

def find_links(text: str) -> dict:
    """Every YouTube link, in the user's order, once. {"items": [{kind, key, url}], "skipped": [urls]}"""
    items, seen, skipped = [], set(), []
    for m in _URL.finditer(text or ""):
        url = m.group(0).rstrip(".,;:")
        v, c, p = _VIDEO.search(url), _CHANNEL.search(url), _PLAYLIST.search(url)
        if v:
            item = {"kind": "video", "key": v.group(1), "url": f"https://www.youtube.com/watch?v={v.group(1)}"}
        elif p:
            item = {"kind": "playlist", "key": p.group(1), "url": f"https://www.youtube.com/playlist?list={p.group(1)}"}
        elif c:
            item = {"kind": "channel", "key": c.group(1), "url": f"https://www.youtube.com/{c.group(1)}"}
        else:
            if url not in skipped:
                skipped.append(url)
            continue
        if (item["kind"], item["key"].lower()) not in seen:
            seen.add((item["kind"], item["key"].lower()))
            items.append(item)
    return {"items": items, "skipped": skipped}


def read_list_file(path: Path) -> str:
    """Text of a competitor list in any common format. Word and Excel files are zips of XML:
    every part is read, so links typed as text and links hidden behind hyperlinks both count."""
    if path.suffix.lower() in (".docx", ".xlsx"):
        out = []
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith((".xml", ".rels")):
                    out.append(z.read(name).decode("utf-8", "replace").replace("&amp;", "&"))
        return "\n".join(out)
    return read_text_file(path)


# ---------------------------------------------------------------- 2. captions -> clean text

_TAG = re.compile(r"<[^>]+>")
_TIME = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s+-->")


def clean_vtt(vtt: str) -> str:
    """Caption file -> plain spoken text. Auto captions repeat each line as it rolls; those
    repeats are dropped. [Music]-style cues and speaker dashes go too."""
    lines, last = [], None
    for raw in vtt.splitlines():
        s = raw.strip()
        if not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")) or _TIME.match(s) or s.isdigit():
            continue
        s = _TAG.sub("", s).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<").replace("&nbsp;", " ")
        s = re.sub(r"\[[^\]]{1,30}\]|\([^)]{1,30}\)", "", s).lstrip("-– ").strip()
        if not s or s == last:
            continue
        if lines and s.startswith(lines[-1]) and len(s) > len(lines[-1]):
            lines[-1] = s
        else:
            lines.append(s)
        last = s
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip()


def words_of(text: str) -> int:
    return len(_WORD.findall(text or ""))


def measure(text: str, duration: float | None) -> dict:
    """Plain numbers about how a competitor talks - used by the style analyst and the writer."""
    words = words_of(text)
    minutes = (duration or 0) / 60
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text or "") if words_of(s)]
    lens = [words_of(s) for s in sentences]
    wpm = round(words / minutes) if minutes else None
    return {"words": words, "minutes": round(minutes, 1), "wpm": wpm,
            "avg_sentence": round(statistics.mean(lens), 1) if lens else None,
            "questions_per_min": round((text or "").count("?") / minutes, 2) if minutes else None,
            "you_per_100": round(100 * len(re.findall(r"\byou(?:r|'re|'ll|'ve)?\b", text or "", re.I)) / words, 1) if words else None,
            "first_30s": " ".join((text or "").split()[: max(40, int((wpm or 150) / 2))]),
            "punctuated": bool(lens) and len(lens) > words / 60}   # auto captions often have no full stops


# ---------------------------------------------------------------- 3. yt-dlp

class FetchError(RuntimeError):
    pass


class YtDlp:
    """yt-dlp through the app's own Python (it's a dependency); a test passes a fake command."""

    def __init__(self, command: list[str] | None = None, timeout: float = 120):
        self.command = command
        self.timeout = timeout

    def argv(self) -> list[str]:
        if self.command:
            return self.command
        fake = os.environ.get("ONEPROD_YTDLP")            # tests: a stand-in script
        return [sys.executable, fake] if fake else [sys.executable, "-m", "yt_dlp"]

    async def run(self, args: list[str], cwd: Path) -> str:
        code, out, err = await run_capture([*self.argv(), "--no-update", "--no-warnings", *args], {}, cwd,
                                           timeout=self.timeout)
        if code != 0:
            msg = next((x for x in reversed(err.splitlines()) if "ERROR" in x), err.strip()[-300:] or f"exit {code}")
            raise FetchError(msg.replace("ERROR: ", "")[:300])
        return out

    async def uploads(self, item: dict, cwd: Path, scan: int = 30) -> list[dict]:
        """A channel's (or playlist's) recent uploads: [{id, title, views, duration}]"""
        url = item["url"] + ("/videos" if item["kind"] == "channel" else "")
        d = json.loads(await self.run(["--flat-playlist", "-J", "--playlist-end", str(scan), url], cwd))
        return [{"id": e.get("id"), "title": e.get("title"), "views": e.get("view_count") or 0,
                 "duration": e.get("duration")}
                for e in d.get("entries") or [] if e.get("id") and len(str(e.get("id"))) == 11]

    async def captions(self, video_id: str, work: Path) -> tuple[dict, str]:
        """(metadata, caption text) for one video; raises FetchError when it has none."""
        work.mkdir(parents=True, exist_ok=True)
        for old in work.glob(f"{video_id}*"):
            old.unlink()
        await self.run(["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
                        "--sub-format", "vtt", "--print-to-file",
                        "%(.{id,title,channel,uploader_id,duration,view_count,upload_date,language})j",
                        f"{video_id}.meta.json", "-o", "%(id)s.%(ext)s",
                        f"https://www.youtube.com/watch?v={video_id}"], work)
        meta_file = work / f"{video_id}.meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8").splitlines()[0]) if meta_file.exists() else {}
        subs = {p.name.split(".")[-2]: p for p in work.glob(f"{video_id}.*.vtt")}
        pick = next((subs[k] for k in ("en", "en-US", "en-GB", *[k for k in subs if k.startswith("en") and k != "en-orig"],
                                       "en-orig") if k in subs), None)
        if not pick:
            raise FetchError("no English captions")
        text = clean_vtt(pick.read_text(encoding="utf-8", errors="replace"))
        if words_of(text) < 30:
            raise FetchError("captions are empty")
        return meta, text


def _safe(name: str, n: int = 60) -> str:
    s = re.sub(r"[^\w\- ]+", "", name or "").strip().replace(" ", "-")
    return (s[:n].strip("-") or "untitled").lower()


async def collect(items: list[dict], dest: Path, ytdlp: YtDlp, *, per_channel: int = 5, scan: int = 30,
                  progress=None, pause: float = 1.0) -> dict:
    """Links -> transcripts in dest/<channel>/<title>.md, numbers in dest/_numbers.md.
    A video transcribed before is reused (dest/_index.json). Never raises for one bad link."""
    dest.mkdir(parents=True, exist_ok=True)
    work = dest / ".tmp"
    index_file = dest / "_index.json"
    try:
        index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    except ValueError:
        index = {}
    say = progress or (lambda text: None)
    videos: list[tuple[str, str]] = []                 # (video id, where it came from)
    problems: list[dict] = []
    for it in items:
        if it["kind"] == "video":
            videos.append((it["key"], it["url"]))
            continue
        say(f"Reading the uploads of {it['key']}…")
        try:
            ups = await ytdlp.uploads(it, work, scan)
        except FetchError as e:
            problems.append({"link": it["url"], "why": str(e)})
            continue
        top = sorted(ups, key=lambda u: -(u["views"] or 0))[:per_channel]
        if not top:
            problems.append({"link": it["url"], "why": "no uploads found"})
        videos += [(u["id"], it["url"]) for u in top]
    seen, done = set(), []
    videos = [v for v in videos if not (v[0] in seen or seen.add(v[0]))]
    for n, (vid, origin) in enumerate(videos, 1):
        if vid in index and (dest / index[vid]["file"]).exists():
            done.append(index[vid])
            say(f"Transcript {n}/{len(videos)} (saved before): {index[vid]['title']}")
            continue
        say(f"Transcript {n}/{len(videos)}…")
        meta, text, err = {}, "", None
        for attempt in range(3):
            try:
                meta, text = await ytdlp.captions(vid, work)
                err = None
                break
            except FetchError as e:
                err = str(e)
                if not re.search(r"429|too many|temporar|timed out|unable to download", err, re.I):
                    break
                await asyncio.sleep(pause * (attempt + 1) * 3)
        if err:
            problems.append({"link": f"https://www.youtube.com/watch?v={vid}", "why": err})
            continue
        nums = measure(text, meta.get("duration"))
        rel = f"{_safe(meta.get('channel') or meta.get('uploader_id') or 'channel', 40)}/{_safe(meta.get('title') or vid)}.md"
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join([
            f"# {meta.get('title') or vid}", "",
            f"- Channel: {meta.get('channel') or '?'}", f"- Link: https://www.youtube.com/watch?v={vid}",
            f"- Views: {meta.get('view_count') or '?'}", f"- Length: {nums['minutes']} min",
            f"- Uploaded: {meta.get('upload_date') or '?'}", f"- Spoken: {nums['words']} words, {nums['wpm'] or '?'} wpm",
            "", "## Transcript (YouTube captions)", "", text, ""]), encoding="utf-8")
        entry = {"id": vid, "title": meta.get("title") or vid, "channel": meta.get("channel"), "file": rel,
                 "views": meta.get("view_count"), "from": origin, **{k: v for k, v in nums.items() if k != "first_30s"},
                 "first_30s": nums["first_30s"]}
        index[vid] = entry
        done.append(entry)
        index_file.write_text(json.dumps(index, indent=1), encoding="utf-8")
        if pause:
            await asyncio.sleep(pause)
    for p in work.glob("*"):
        p.unlink(missing_ok=True)
    if work.exists():
        work.rmdir()
    write_numbers(dest, done, problems)
    return {"transcripts": done, "problems": problems}


def summary(done: list[dict]) -> dict:
    wpms = [d["wpm"] for d in done if d.get("wpm")]
    sents = [d["avg_sentence"] for d in done if d.get("avg_sentence") and d.get("punctuated")]
    mins = [d["minutes"] for d in done if d.get("minutes")]
    return {"videos": len(done), "median_wpm": round(statistics.median(wpms)) if wpms else None,
            "median_minutes": round(statistics.median(mins), 1) if mins else None,
            "median_sentence": round(statistics.median(sents), 1) if sents else None}


def write_numbers(dest: Path, done: list[dict], problems: list[dict]) -> Path:
    s = summary(done)
    rows = [f"| {d['title'][:60]} | {d.get('channel') or ''} | {d.get('views') or ''} | {d['minutes']} | "
            f"{d.get('wpm') or ''} | {d.get('avg_sentence') if d.get('punctuated') else 'n/a'} | "
            f"{d.get('questions_per_min') or ''} | {d.get('you_per_100') or ''} | {d['file']} |" for d in done]
    text = "\n".join([
        "# Competitor numbers (measured from captions)", "",
        f"{s['videos']} videos · median {s['median_wpm'] or '?'} words per minute · median "
        f"{s['median_minutes'] or '?'} minutes · median sentence {s['median_sentence'] or 'n/a'} words", "",
        "| Video | Channel | Views | Min | WPM | Avg sentence | Questions/min | 'you' per 100 words | File |",
        "|---|---|---|---|---|---|---|---|---|", *rows, "",
        "## First ~30 seconds of each", "",
        *[f"**{d['title'][:70]}** — {d['first_30s']}\n" for d in done],
        *(["## Not transcribed", "", *[f"- {p['link']} — {p['why']}" for p in problems], ""] if problems else [])])
    f = dest / "_numbers.md"
    f.write_text(text, encoding="utf-8")
    return f
