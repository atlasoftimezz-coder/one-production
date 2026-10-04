"""Local web front-end for the voiceover pipeline.

Run:  python server.py            (then open http://127.0.0.1:8720)
      python server.py --host 0.0.0.0 --port 8720   to reach it from another machine
"""
import argparse, pathlib, shutil, subprocess, sys
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

import config, engine

HERE = pathlib.Path(__file__).parent
app = FastAPI(title="Voiceover Studio")


class JobSpec(BaseModel):
    script: str
    voice: str
    name: str = "voiceover"
    preset: str = "narrator"
    pace_mode: str = "natural"          # natural | wpm | grid
    target_wpm: Optional[float] = None
    grid_gap: float = 1.5
    auto_fix: bool = True
    exaggeration: Optional[float] = None
    cfg_weight: Optional[float] = None
    temperature: Optional[float] = None
    pitch: Optional[float] = None
    pause: Optional[float] = None


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/meta")
def meta():
    return {"presets": engine.PRESETS, "voices": engine.voices(),
            "problems": config.problems(), "outputs": str(config.OUTPUTS),
            "voices_dir": str(config.VOICES), "wer_limit": engine.WER_LIMIT}


@app.post("/api/jobs")
def create_job(spec: JobSpec):
    if not spec.script.strip():
        raise HTTPException(400, "the script is empty")
    if spec.voice not in engine.voices():
        raise HTTPException(400, "unknown reference clip: " + spec.voice)
    if spec.preset not in engine.PRESETS:
        raise HTTPException(400, "unknown preset: " + spec.preset)
    if spec.pace_mode == "wpm" and not spec.target_wpm:
        raise HTTPException(400, "pick a target words-per-minute, or choose another pace mode")
    return {"id": engine.submit(spec.model_dump())}


@app.get("/api/jobs")
def list_jobs():
    return engine.all_jobs()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = engine.job_state(jid)
    if not j:
        raise HTTPException(404, "no such job")
    j.pop("spec", None)
    return j


@app.get("/api/preview/{jid}/{kind}")
def preview(jid: str, kind: str):
    j = engine.job_state(jid)
    if not j or not j.get("result"):
        raise HTTPException(404, "that job has no output yet")
    path = pathlib.Path(j["result"]["mp3" if kind == "mp3" else "wav"])
    if not path.exists():
        raise HTTPException(404, "the output file has been moved or deleted")
    return FileResponse(path, media_type="audio/mpeg" if kind == "mp3" else "audio/wav",
                        filename=path.name)


@app.post("/api/voices")
async def add_voice(file: UploadFile = File(...)):
    """Reference clips want 10-30s of clean single-speaker narration.
    Shorter than about 8s and the clone stops tracking the voice's pitch."""
    suffix = pathlib.Path(file.filename or "").suffix.lower()
    if suffix not in (".wav", ".mp3", ".flac"):
        raise HTTPException(400, "reference clips must be .wav, .mp3 or .flac")
    safe = "".join(c for c in pathlib.Path(file.filename).stem
                   if c.isalnum() or c in "-_ ").strip() or "reference"
    raw = config.VOICES / ("_upload_" + safe + suffix)
    dst = config.VOICES / (safe + "_ref48.wav")
    with raw.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        subprocess.run([config.FFMPEG, "-y", "-loglevel", "error", "-i", str(raw),
                        "-af", "highpass=f=50,loudnorm=I=-20:TP=-3:LRA=7,aresample=48000",
                        "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    except subprocess.CalledProcessError:
        raise HTTPException(400, "ffmpeg could not read that file")
    finally:
        raw.unlink(missing_ok=True)
    dur = 0.0
    try:
        import pipeline
        dur = pipeline.duration(dst)
    except Exception:
        pass
    warn = None
    if dur and dur < 8:
        warn = ("that clip is %.1fs; under about 8s the clone often loses the voice's "
                "pitch and drifts back to a generic register" % dur)
    return JSONResponse({"name": dst.name, "seconds": round(dur, 1), "warning": warn})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8720)
    a = ap.parse_args()
    for p in config.problems():
        print("  setup warning:", p, file=sys.stderr)
    print("Voiceover Studio  ->  http://%s:%d" % (a.host, a.port))
    print("  ffmpeg :", config.FFMPEG)
    print("  voices :", config.VOICES)
    print("  outputs:", config.OUTPUTS)
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
