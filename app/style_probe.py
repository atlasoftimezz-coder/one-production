"""Competitor style probe (owner, 2026-10-11: "it should see the video literally it should learn
the editing style"): a competitor video -> measured editing numbers + keyframes for the AI.

No AI, no GPU, no cost. yt-dlp (already an app dependency) downloads a small working copy (at most
720p) into the data folder's cache; ffmpeg + numpy measure it:

    cuts        ffmpeg scene detection (select gt(scene,T) + showinfo) -> shot lengths, cuts/min,
                the pace of the first 30 s against the rest, cuts per minute over the video
    picture     a 64x36 colour copy at 10 fps -> motion inside shots, share of flat frames
                (plain backgrounds: a motion-graphics hint), colourfulness, the main colours
    keyframes   one still per shot (at most MAX_FRAMES, spread evenly) + 4x4 contact sheets
    sound       integrated loudness; strong onsets (hits) per minute and the share of cuts that
                land on one (an SFX hint); the level under the pauses (a music-bed hint)
    words       the captions -> competitors.measure (words per minute and so on)

The copy is analysis material only: it is never used in an episode, and the video file is
deleted afterwards unless asked to keep it. Every number here is a measurement; the "hints"
are named as such, because a flat frame or a loud onset is evidence, not proof.
"""
from __future__ import annotations

import json
import re
import shutil
import statistics
from pathlib import Path

import numpy as np

from . import media
from .competitors import FetchError, YtDlp, clean_vtt, measure, words_of

SCENE = 0.30                 # scene-change threshold (ffmpeg's 0..1 score)
MIN_SHOT = 0.20              # cuts closer than this are one flicker, not two shots
MAX_FRAMES = 120
SMALL_W, SMALL_H, SMALL_FPS = 64, 36, 10
MAX_MINUTES = 20


class ProbeError(RuntimeError):
    pass


# ---------------------------------------------------------------- download

async def download(ytdlp: YtDlp, video_id: str, work: Path, ffmpeg: str | None = None) -> dict:
    """Small working copy + metadata + captions of one video into work/. {video, meta, text}"""
    work.mkdir(parents=True, exist_ok=True)
    args = ["-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[height<=720]/b",
            "--merge-output-format", "mp4", "--max-filesize", "600M",
            "--match-filter", f"duration<={MAX_MINUTES * 60}",
            "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
            "--print-to-file", "%(.{id,title,channel,uploader_id,duration,view_count,upload_date})j",
            f"{video_id}.meta.json", "-o", "%(id)s.%(ext)s"]
    if ffmpeg:
        args += ["--ffmpeg-location", str(Path(ffmpeg).parent)]
    await ytdlp.run([*args, f"https://www.youtube.com/watch?v={video_id}"], work)
    meta_file = work / f"{video_id}.meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8").splitlines()[0]) if meta_file.exists() else {}
    video = next((p for p in sorted(work.glob(f"{video_id}.*"))
                  if p.suffix.lower() in (".mp4", ".mkv", ".webm") and ".part" not in p.name), None)
    if not video:
        if (meta.get("duration") or 0) > MAX_MINUTES * 60:
            raise FetchError(f"longer than {MAX_MINUTES} minutes - pick a shorter video")
        raise FetchError("the video could not be downloaded")
    subs = {p.name.split(".")[-2]: p for p in work.glob(f"{video_id}.*.vtt")}
    pick = next((subs[k] for k in ("en", "en-US", "en-GB", *[k for k in subs if k.startswith("en") and k != "en-orig"],
                                   "en-orig") if k in subs), None)
    text = clean_vtt(pick.read_text(encoding="utf-8", errors="replace")) if pick else ""
    return {"video": video, "meta": meta, "text": text if words_of(text) >= 30 else ""}


# ---------------------------------------------------------------- cuts

def cut_times(ffmpeg: str, video: Path, threshold: float = SCENE) -> list[float]:
    r = media.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
                   f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    out: list[float] = []
    for t in sorted(times):
        if t >= MIN_SHOT and (not out or t - out[-1] >= MIN_SHOT):
            out.append(round(t, 3))
    return out


def shots_of(cuts: list[float], seconds: float) -> list[tuple[float, float]]:
    edges = [0.0, *[c for c in cuts if c < seconds - MIN_SHOT], seconds]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 0]


def pace(shots: list[tuple[float, float]], seconds: float) -> dict:
    lens = [b - a for a, b in shots]
    minutes = seconds / 60 if seconds else 0
    cuts = len(shots) - 1

    def per_min(lo: float, hi: float) -> float | None:
        span = min(hi, seconds) - lo
        if span <= 5:
            return None
        n = sum(1 for a, _ in shots[1:] if lo <= a < hi)
        return round(n / (span / 60), 1)

    q = np.percentile(lens, [10, 50, 90]) if lens else [0, 0, 0]
    return {"shots": len(shots), "cuts": cuts, "cuts_per_min": round(cuts / minutes, 1) if minutes else None,
            "shot_median": round(float(q[1]), 2), "shot_p10": round(float(q[0]), 2), "shot_p90": round(float(q[2]), 2),
            "shot_mean": round(statistics.mean(lens), 2) if lens else None,
            "first_30s_cuts_per_min": per_min(0, 30), "after_30s_cuts_per_min": per_min(30, seconds),
            "cuts_per_minute_curve": [per_min(m * 60, (m + 1) * 60) for m in range(int(np.ceil(minutes)))]}


# ---------------------------------------------------------------- picture

def small_frames(ffmpeg: str, video: Path) -> np.ndarray:
    """(frames, H, W, 3) uint8 at SMALL_FPS - cheap enough for a 20-minute video (~1 GB never in memory:
    64x36x3 = 6.9 kB a frame, 12,000 frames = 83 MB)."""
    import subprocess
    r = subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(video), "-an", "-vf",
                        f"fps={SMALL_FPS},scale={SMALL_W}:{SMALL_H}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=3600, creationflags=media.NO_WINDOW)
    if r.returncode:
        raise ProbeError("could not decode the video: " + r.stderr.decode(errors="replace")[-200:])
    n = len(r.stdout) // (SMALL_W * SMALL_H * 3)
    return np.frombuffer(r.stdout[: n * SMALL_W * SMALL_H * 3], dtype=np.uint8).reshape(n, SMALL_H, SMALL_W, 3)


def picture(frames: np.ndarray, shots: list[tuple[float, float]]) -> dict:
    if not len(frames):
        return {}
    f = frames.astype(np.float32)
    gray = f.mean(axis=3)
    diff = np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2)) if len(gray) > 1 else np.zeros(0)
    cut_frames = {int(round(a * SMALL_FPS)) for a, _ in shots[1:]}
    inside = [d for i, d in enumerate(diff) if i + 1 not in cut_frames and i not in cut_frames]
    per_shot = []
    for a, b in shots:
        lo, hi = int(a * SMALL_FPS) + 1, int(b * SMALL_FPS) - 1
        seg = diff[lo:hi] if hi > lo else diff[0:0]
        per_shot.append(float(seg.mean()) if len(seg) else 0.0)
    flat = (gray.std(axis=(1, 2)) < 10).mean()
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = np.sqrt(rg.std(axis=(1, 2)) ** 2 + yb.std(axis=(1, 2)) ** 2) + 0.3 * np.sqrt(
        rg.mean(axis=(1, 2)) ** 2 + yb.mean(axis=(1, 2)) ** 2)
    q = (frames[::5] // 32).reshape(-1, 3)                  # 8 levels a channel, every 5th frame
    keys, counts = np.unique(q[:, 0].astype(np.int32) * 64 + q[:, 1] * 8 + q[:, 2], return_counts=True)
    top = keys[np.argsort(-counts)][:6]
    palette = ["#%02X%02X%02X" % ((k // 64) * 32 + 16, ((k // 8) % 8) * 32 + 16, (k % 8) * 32 + 16) for k in top]
    share = [round(float(c) / len(q), 3) for c in np.sort(counts)[::-1][:6]]
    moving = [m for m in per_shot if m > 2.0]
    return {"motion_inside_shots": round(float(np.mean(inside)), 2) if inside else 0.0,
            "moving_shot_share": round(len(moving) / len(per_shot), 2) if per_shot else None,
            "flat_frame_share": round(float(flat), 2), "colourfulness": round(float(colorful.mean()), 1),
            "brightness": round(float(gray.mean()), 1), "main_colours": [{"hex": h, "share": s} for h, s in zip(palette, share)],
            "per_shot_motion": [round(m, 2) for m in per_shot]}


def keyframes(ffmpeg: str, video: Path, shots: list[tuple[float, float]], out: Path) -> list[dict]:
    """One still per shot (spread evenly when there are more than MAX_FRAMES) + 4x4 contact sheets."""
    frames_dir, sheets_dir = out / "frames", out / "sheets"
    for d in (frames_dir, sheets_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    pick = list(range(len(shots)))
    if len(pick) > MAX_FRAMES:
        pick = sorted({int(round(i * (len(shots) - 1) / (MAX_FRAMES - 1))) for i in range(MAX_FRAMES)})
    saved = []
    for n, i in enumerate(pick, 1):
        a, b = shots[i]
        at = a + (b - a) / 2
        dst = frames_dir / f"frame-{n:03d}.jpg"
        media.run([ffmpeg, "-y", "-hide_banner", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
                   "-vf", "scale=480:-2", "-q:v", "4", str(dst)], 120)
        if dst.exists():
            saved.append({"file": f"frames/{dst.name}", "shot": i + 1, "at": round(at, 2), "length": round(b - a, 2)})
    if saved:
        tmp = frames_dir / "seq"
        tmp.mkdir()
        for n, s in enumerate(saved, 1):
            shutil.copyfile(out / s["file"], tmp / f"{n:04d}.jpg")
        media.run([ffmpeg, "-y", "-hide_banner", "-v", "error", "-framerate", "1", "-i", str(tmp / "%04d.jpg"),
                   "-vf", "scale=384:216:force_original_aspect_ratio=decrease,pad=384:216:(ow-iw)/2:(oh-ih)/2,tile=4x4:padding=4",
                   "-q:v", "4", str(sheets_dir / "sheet-%02d.jpg")], 600)
        shutil.rmtree(tmp, ignore_errors=True)
    return saved


# ---------------------------------------------------------------- sound

def sound(ffmpeg: str, video: Path, shots: list[tuple[float, float]], seconds: float) -> dict:
    try:
        x = media.pcm(ffmpeg, video, rate=16000)
    except media.MediaError:
        return {"has_audio": False}
    if len(x) < 16000:
        return {"has_audio": False}
    hop = 160                                              # 10 ms
    n = len(x) // hop
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    med = float(np.median(db))
    # strong onsets: a jump of 9 dB+ over the last 100 ms, at least 6 dB above the median, 150 ms apart
    c = np.concatenate([[0.0], np.cumsum(db)])
    idx = np.arange(n)
    lo = np.maximum(0, idx - 10)
    prev = np.where(idx > 0, (c[idx] - c[lo]) / np.maximum(idx - lo, 1), db[0])
    onset = (db - prev > 9) & (db > med + 6)
    hits, last = [], -1e9
    for i in np.flatnonzero(onset):
        t = i / 100
        if t - last >= 0.15:
            hits.append(t)
            last = t
    cuts = [a for a, _ in shots[1:]]
    on_cut = sum(1 for c in cuts if any(abs(h - c) <= 0.15 for h in hits))
    floor = float(np.percentile(db, 10)) - med
    out = {"has_audio": True, "hits_per_min": round(len(hits) / (seconds / 60), 1) if seconds else None,
           "cuts_with_a_hit": round(on_cut / len(cuts), 2) if cuts else None,
           "pause_level_db": round(floor, 1), "music_bed_hint": floor > -30}
    try:
        out["lufs"] = round(media.loudness(ffmpeg, video)["lufs"], 1)
    except media.MediaError:
        pass
    return out


# ---------------------------------------------------------------- all of it

def analyse(ffmpeg: str, video: Path, out: Path, text: str = "", meta: dict | None = None) -> dict:
    """Measure one downloaded video into out/ (measure.json, frames/, sheets/)."""
    out.mkdir(parents=True, exist_ok=True)
    info = media.probe(ffmpeg, video)
    seconds = float(info["format"]["duration"])
    vs = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if not vs:
        raise ProbeError("the file has no picture")
    cuts = cut_times(ffmpeg, video)
    shots = shots_of(cuts, seconds)
    result = {"video": dict(meta or {}), "seconds": round(seconds, 2), "size": f"{vs.get('width')}x{vs.get('height')}",
              "fps": vs.get("avg_frame_rate"), "pace": pace(shots, seconds), "cuts": cuts,
              "picture": picture(small_frames(ffmpeg, video), shots), "sound": sound(ffmpeg, video, shots, seconds),
              "words": {k: v for k, v in measure(text, seconds).items()} if text else None}
    result["frames"] = keyframes(ffmpeg, video, shots, out)
    result["sheets"] = sorted(f"sheets/{p.name}" for p in (out / "sheets").glob("*.jpg"))
    (out / "measure.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    if text:
        (out / "transcript.txt").write_text(text, encoding="utf-8")
    return result


def combine(measures: list[dict]) -> dict:
    """Numbers over several videos (medians), computed by code - the AI never averages."""
    def med(path: tuple[str, str]) -> float | None:
        vals = [m[path[0]][path[1]] for m in measures if (m.get(path[0]) or {}).get(path[1]) is not None]
        return round(statistics.median(vals), 2) if vals else None
    keys = {"pace": ("cuts_per_min", "shot_median", "shot_p10", "shot_p90", "first_30s_cuts_per_min", "after_30s_cuts_per_min"),
            "picture": ("motion_inside_shots", "moving_shot_share", "flat_frame_share", "colourfulness", "brightness"),
            "sound": ("hits_per_min", "cuts_with_a_hit", "pause_level_db", "lufs"),
            "words": ("wpm", "avg_sentence")}
    out = {group: {k: med((group, k)) for k in ks} for group, ks in keys.items()}
    beds = [m["sound"].get("music_bed_hint") for m in measures if (m.get("sound") or {}).get("has_audio")]
    out["sound"]["music_bed_hint_share"] = round(sum(beds) / len(beds), 2) if beds else None
    out["videos"] = len(measures)
    return out
