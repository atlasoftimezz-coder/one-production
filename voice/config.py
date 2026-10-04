"""Where the voice engine finds its tools and keeps its files.

One Production starts this engine and passes every location as an environment variable,
so nothing here depends on one particular PC:

    VOAPP_FFMPEG / VOAPP_FFPROBE   ffmpeg (an ffmpeg with the rubberband filter is preferred)
    VOAPP_WHISPER                  a local faster-whisper model folder, or a HuggingFace id
    VOAPP_VOICES                   reference clips
    VOAPP_OUTPUTS                  finished voiceovers
    VOAPP_JOBS                     scratch space while rendering
    HF_HOME                        where model downloads live (set by One Production)

Run on its own (python server.py), it falls back to ffmpeg on PATH and folders next to it.
"""
import os
import pathlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).parent


def _first(*cands):
    for c in cands:
        if c and pathlib.Path(c).exists():
            return str(c)
    return None


def _has_rubberband(exe):
    """The deep-voice presets pitch-shift with formant preservation; not every ffmpeg build
    ships the rubberband filter, so prefer one that has it."""
    try:
        out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "rubberband" in out
    except Exception:
        return False


def _pick_ffmpeg():
    cands = [c for c in (os.environ.get("VOAPP_FFMPEG"), shutil.which("ffmpeg")) if c and pathlib.Path(c).exists()]
    for c in cands:
        if _has_rubberband(c):
            return str(c), True
    return (str(cands[0]), False) if cands else (None, False)


FFMPEG, FFMPEG_HAS_RUBBERBAND = _pick_ffmpeg()
FFPROBE = os.environ.get("VOAPP_FFPROBE") or _first(
    str(pathlib.Path(FFMPEG).with_name("ffprobe.exe")) if FFMPEG else None,
    shutil.which("ffprobe"))

WHISPER = os.environ.get("VOAPP_WHISPER") or "deepdml/faster-whisper-large-v3-turbo-ct2"

VOICES = pathlib.Path(os.environ.get("VOAPP_VOICES") or HERE / "voices")
OUTPUTS = pathlib.Path(os.environ.get("VOAPP_OUTPUTS") or HERE / "outputs")
JOBS = pathlib.Path(os.environ.get("VOAPP_JOBS") or HERE / "jobs")

for d in (VOICES, OUTPUTS, JOBS):
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def problems():
    out = []
    if not FFMPEG:
        out.append("ffmpeg not found - set VOAPP_FFMPEG or put ffmpeg on PATH")
    if not FFPROBE:
        out.append("ffprobe not found - set VOAPP_FFPROBE or put ffprobe on PATH")
    if not VOICES.exists():
        out.append(f"voices folder missing: {VOICES}")
    if FFMPEG and not FFMPEG_HAS_RUBBERBAND:
        out.append("this ffmpeg has no rubberband filter - the deep presets cannot pitch-shift")
    return out
