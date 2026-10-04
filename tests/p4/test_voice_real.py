"""The real thing: one full voiceover through One Production on this PC's GPU.

Opt-in, because it renders minutes of audio:
    set ONEPROD_GPU_TESTS=1
    set ONEPROD_REAL_SCRIPT=<path to a .txt/.md/.docx script>
    set ONEPROD_REAL_VOICE=human_ref48.wav          (optional)
Uses the voice app configured in the user's settings file.

Pass criteria (plan P4): every section passes the read-check (WER <= 0.05 after retries),
the voice stays within 1 semitone of its reference, the master is -16 LUFS +/- 0.5, and the
subtitles cover the script with no overlapping cues and at most two rows each.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.security.guard import TOKEN_HEADER

pytestmark = pytest.mark.skipif(os.environ.get("ONEPROD_GPU_TESTS") != "1",
                                reason="real GPU render: set ONEPROD_GPU_TESTS=1 to run")

MEASURE = r'''
import json, sys, subprocess, numpy as np, librosa
sys.path.insert(0, sys.argv[3])
import config
def f0(path, offset=0, dur=None):
    y, sr = librosa.load(path, sr=22050, mono=True, offset=offset, duration=dur)
    f, _, _ = librosa.pyin(y, fmin=55, fmax=340, sr=sr, frame_length=1024)
    return float(np.nanmedian(f))
out = subprocess.run([config.FFMPEG, "-hide_banner", "-i", sys.argv[1], "-af", "loudnorm=print_format=json",
                      "-f", "null", "-"], capture_output=True, text=True).stderr
lufs = float(json.loads(out[out.rindex("{"):out.rindex("}") + 1])["input_i"])
print(json.dumps({"lufs": lufs, "f0_out": f0(sys.argv[1], 20, 120), "f0_ref": f0(sys.argv[2])}))
'''


def _srt_cues(text):
    cues = []
    for block in [b for b in text.strip().split("\n\n") if b.strip()]:
        lines = block.splitlines()
        a, b = lines[1].split(" --> ")
        sec = lambda s: (lambda h, m, rest: int(h) * 3600 + int(m) * 60 + float(rest.replace(",", ".")))(*s.split(":"))
        cues.append((sec(a), sec(b), lines[2:]))
    return cues


def test_real_voiceover_end_to_end(tmp_path):
    script = Path(os.environ["ONEPROD_REAL_SCRIPT"])
    voice = os.environ.get("ONEPROD_REAL_VOICE", "human_ref48.wav")
    settings = load_settings().model_copy(update={"data_dir": tmp_path / "data", "open_browser": False})
    assert settings.voice_app_dir and settings.voice_python, "voice app not configured in settings.json"
    app = create_app(settings, token="real-gpu-test-token-0123456789")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers[TOKEN_HEADER] = "real-gpu-test-token-0123456789"
        pid = c.post("/api/projects", json={"name": "Real voice test"}).json()["id"]
        name = c.post(f"/api/projects/{pid}/files",
                      files={"files": (script.name, script.read_bytes())}).json()[0]["name"]
        stats = c.post(f"/api/projects/{pid}/script-stats", json={"file": name}).json()
        r = c.post(f"/api/projects/{pid}/voice", json={
            "file": name, "voice": voice, "name": "real-test", "preset": "clone_exact",
            "exaggeration": 0.45, "cfg_weight": 0.50, "temperature": 0.75,
            "pace_mode": "wpm", "target_wpm": 155, "approve": "once"})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        end = time.time() + 45 * 60
        while time.time() < end:
            job = c.get(f"/api/voicejobs/{jid}").json()
            if job["status"] in ("done", "failed"):
                break
            time.sleep(10)
        report = {"job": job, "script_stats": stats}
        assert job["status"] == "done", job.get("error")

        files = settings.data_dir / "projects" / pid / "files"
        wav, srt = files / "real-test.wav", files / "real-test.srt"
        secs = job["sections"]
        report["sections"] = [(s["title"], s["status"], s["wer"]) for s in secs]
        assert secs and all(s["status"] == "ok" and s["wer"] <= 0.05 for s in secs), report["sections"]
        assert job["result"]["flagged"] == []

        cues = _srt_cues(srt.read_text(encoding="utf-8"))
        overlaps = sum(1 for (a1, b1, _), (a2, b2, _) in zip(cues, cues[1:]) if a2 < b1 - 0.001)
        rows = max(len(t) for *_, t in cues)
        words = sum(len(" ".join(t).split()) for *_, t in cues)
        report["srt"] = {"cues": len(cues), "overlaps": overlaps, "max_rows": rows, "words": words}
        assert overlaps == 0 and rows <= 2
        assert abs(words - stats["words"]) <= max(5, stats["words"] * 0.02), report["srt"]

        voices_dir = subprocess.run([settings.voice_python, "-c",
                                     "import sys; sys.path.insert(0, sys.argv[1]); import config; print(config.VOICES)",
                                     settings.voice_app_dir], capture_output=True, text=True).stdout.strip()
        m = subprocess.run([settings.voice_python, "-c", MEASURE, str(wav), str(Path(voices_dir) / voice),
                            settings.voice_app_dir], capture_output=True, text=True, timeout=900)
        meas = json.loads(m.stdout.strip().splitlines()[-1])
        import math
        meas["semitones_off"] = round(12 * abs(math.log2(meas["f0_out"] / meas["f0_ref"])), 2)
        report["measure"] = meas
        (Path(os.environ.get("ONEPROD_REAL_REPORT", tmp_path / "report.json"))).write_text(
            json.dumps(report, indent=1, default=str), encoding="utf-8")
        assert abs(meas["lufs"] + 16) <= 0.5, meas
        assert meas["semitones_off"] <= 1.0, meas
