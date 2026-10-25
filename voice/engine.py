"""Job runner. One GPU, so one job at a time; models stay resident between jobs."""
import os, json, time, queue, shutil, threading, traceback, pathlib
import config, pipeline

PRESETS = {
    "narrator": {
        "label": "Narrator (natural pitch)",
        "note": "Even, mid-range read, about 110 Hz. The Cancel The Debt voice.",
        "exaggeration": 0.50, "cfg_weight": 0.50, "temperature": 0.80,
        "pitch": 1.0, "pause": 0.0},
    "documentary_deep": {
        "label": "Documentary - deep",
        "note": "Pitched to about 90 Hz with wide pauses. Matches the slow explainer channels.",
        "exaggeration": 0.80, "cfg_weight": 0.30, "temperature": 0.85,
        "pitch": 0.699, "pause": 0.50},
    "documentary_fast": {
        "label": "Documentary - fast",
        "note": "Bright and relentless, almost no pauses. Matches the high-density channels.",
        "exaggeration": 0.80, "cfg_weight": 0.30, "temperature": 0.85,
        "pitch": 1.0, "pause": 0.16},
    "wildlife": {
        "label": "Wildlife / animal facts",
        "note": "Bright and fast, pitched into the 111-129 Hz band the animal channels use.",
        "exaggeration": 0.80, "cfg_weight": 0.30, "temperature": 0.85,
        "pitch": 0.708, "pause": 0.0},
    "clone_exact": {
        "label": "Clone the reference exactly",
        "note": "No pitch shift, no pause edit. Tracks the source clip's pitch and range most closely.",
        "exaggeration": 0.65, "cfg_weight": 0.40, "temperature": 0.80,
        "pitch": 1.0, "pause": 0.0},
    "calm": {
        "label": "Calm / low energy",
        "note": "Restrained delivery, the flattest of the set. Use for sombre copy.",
        "exaggeration": 0.35, "cfg_weight": 0.45, "temperature": 0.70,
        "pitch": 1.0, "pause": 0.0},
}

WER_LIMIT = 0.05        # above this a section is re-rendered at a new seed
RETRY_SEEDS = [9137, 5150, 6001]

_jobs, _lock, _q = {}, threading.Lock(), queue.Queue()
_tts = _asr = None


def voices():
    if not config.VOICES.exists():
        return []
    return sorted(p.name for p in config.VOICES.iterdir()
                  if p.suffix.lower() in (".wav", ".mp3", ".flac"))


def job_state(jid):
    with _lock:
        j = _jobs.get(jid)
        return json.loads(json.dumps(j)) if j else None


def all_jobs():
    with _lock:
        return [{k: v[k] for k in ("id", "name", "status", "created", "message")}
                for v in sorted(_jobs.values(), key=lambda x: -x["created"])]


def _set(jid, **kw):
    with _lock:
        _jobs[jid].update(kw)


def submit(spec):
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    with _lock:
        _jobs[jid] = {"id": jid, "name": spec.get("name") or "voiceover", "status": "queued",
                      "created": time.time(), "message": "waiting for the worker",
                      "spec": spec, "sections": [], "result": None, "error": None}
    _q.put(jid)
    return jid


def _load_models(jid):
    global _tts, _asr
    if _tts is None:
        _set(jid, message="loading Chatterbox onto the GPU (first run takes about 30s)")
        from chatterbox.tts import ChatterboxTTS
        import torch
        _tts = ChatterboxTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    if _asr is None:
        _set(jid, message="loading the read-check model")
        from faster_whisper import WhisperModel
        _asr = WhisperModel(str(config.WHISPER), device="cpu", compute_type="int8")


def _render(text, ref, p, seed, out_path):
    import torch, torchaudio
    parts = []
    for i, chunk in enumerate(pipeline.chunk_text(text)):
        torch.manual_seed(seed + i * 101)
        w = _tts.generate(chunk, audio_prompt_path=str(ref),
                          exaggeration=p["exaggeration"], cfg_weight=p["cfg_weight"],
                          temperature=p["temperature"])
        parts.append(w.squeeze(0).cpu())
        parts.append(torch.zeros(int(pipeline.CHUNK_GAP * _tts.sr)))
    a = torch.cat(parts[:-1]).unsqueeze(0)
    torchaudio.save(str(out_path), a, _tts.sr)
    return a.shape[1] / _tts.sr


def _ff(*args):
    pipeline._run([config.FFMPEG, "-y", "-loglevel", "error", *args])


def _worker():
    while True:
        jid = _q.get()
        try:
            _run_job(jid)
        except Exception as e:
            _set(jid, status="failed", error=str(e), message="failed")
            traceback.print_exc()
        finally:
            _q.task_done()


def _slot_for(sections, i, fallback):
    """Length of section i's slot when the script carries [m:ss] stamps."""
    s = sections[i]
    if s["stamp"] is None:
        return None
    nxt = sections[i + 1]["stamp"] if i + 1 < len(sections) else None
    return (nxt - s["stamp"]) if nxt is not None else fallback


def _run_job(jid):
    spec = job_state(jid)["spec"]
    work = config.JOBS / jid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    sections = pipeline.parse_script(spec["script"])
    if not sections:
        raise RuntimeError("no narration found in that script")

    p = dict(PRESETS[spec.get("preset", "narrator")])
    for k in ("exaggeration", "cfg_weight", "temperature", "pitch", "pause"):
        if spec.get(k) is not None:
            p[k] = float(spec[k])

    ref = config.VOICES / spec["voice"]
    if not ref.exists():
        raise RuntimeError("reference clip not found: " + str(spec["voice"]))
    if p["pitch"] != 1.0 and not config.FFMPEG_HAS_RUBBERBAND:
        raise RuntimeError("this preset pitch-shifts, but the detected ffmpeg has no rubberband filter")

    _set(jid, status="running",
         sections=[{"i": i, "title": s["title"], "words": s["words"], "stamp": s["stamp"],
                    "status": "pending", "wer": None, "diffs": [], "seed": 4242,
                    "speech": None, "wpm": None}
                   for i, s in enumerate(sections)])
    _load_models(jid)

    # ---- render, read-check, re-render anything that fails the check
    for i, s in enumerate(sections):
        raw = work / ("s%d_raw.wav" % i)
        best_wav = work / ("s%d_best.wav" % i)
        initial_seed = int(spec.get('seed', 4242)) % (2**31)
        seeds = [initial_seed] + ([s for s in RETRY_SEEDS if s != initial_seed] if spec.get("auto_fix", True) else [])
        best = None
        for attempt, seed in enumerate(seeds):
            label = "rendering" if attempt == 0 else ("retry %d after a misread" % attempt)
            _set(jid, message="section %d/%d: %s" % (i + 1, len(sections), label))
            with _lock:
                _jobs[jid]["sections"][i]["status"] = "rendering"
            _render(s["text"], ref, p, seed, raw)
            _set(jid, message="section %d/%d: read-check" % (i + 1, len(sections)))
            qc = pipeline.read_check(_asr, raw, s["text"])
            if best is None or qc["wer"] < best[1]["wer"]:
                best = (seed, qc)
                shutil.copy(raw, best_wav)
            if qc["wer"] <= WER_LIMIT:
                break
        seed, qc = best
        shutil.copy(best_wav, raw)
        with _lock:
            _jobs[jid]["sections"][i].update(
                status="ok" if qc["wer"] <= WER_LIMIT else "check",
                wer=qc["wer"], diffs=qc["diffs"], seed=seed)

    # ---- delivery shaping: pitch, pauses, pace, slot padding
    mode = spec.get("pace_mode", "natural")
    gap = float(spec.get("grid_gap", 1.5))
    parts = []
    for i, s in enumerate(sections):
        _set(jid, message="section %d/%d: shaping" % (i + 1, len(sections)))
        cur = work / ("s%d_raw.wav" % i)
        if p["pitch"] != 1.0:
            nxt = work / ("s%d_pitch.wav" % i)
            pipeline.pitch_shift(cur, nxt, p["pitch"])
            cur = nxt
        if p["pause"] > 0:
            nxt = work / ("s%d_pause.wav" % i)
            pipeline.set_pauses(cur, nxt, p["pause"])
            cur = nxt

        d = pipeline.duration(cur)
        target = None
        if mode == "wpm" and spec.get("target_wpm"):
            target = s["words"] / (float(spec["target_wpm"]) / 60.0)
        elif mode == "grid":
            slot = _slot_for(sections, i, d + gap)
            if slot:
                target = max(slot - gap, 1.0)
        if mode == "grid_pad":
            target = None          # fidelity mode: never time-stretch, only pad
        if target:
            at = d / target
            if not 0.5 <= at <= 2.0:
                raise RuntimeError(
                    "section %d needs a %.2fx speed change to fit; the target pace is too "
                    "far from what the voice rendered" % (i + 1, at))
            nxt = work / ("s%d_tempo.wav" % i)
            _ff("-i", str(cur), "-af", "atempo=%.4f" % at, str(nxt))
            cur = nxt

        speech = pipeline.duration(cur)
        slot = (_slot_for(sections, i, speech + gap)
                if mode in ("grid", "grid_pad") else None)
        if slot and speech > slot:
            raise RuntimeError(
                "section %d runs %.1fs but its slot is only %.0fs; at this voice's "
                "natural pace the script overruns the timestamps - shorten the copy "
                "or use the stretching grid mode" % (i + 1, speech, slot))
        pad = ("apad=whole_dur=%s" % slot) if slot else ("apad=pad_dur=%s" % gap)
        nxt = work / ("s%d_slot.wav" % i)
        _ff("-i", str(cur), "-af", pad + ",aresample=48000", "-ac", "1",
            "-c:a", "pcm_s16le", str(nxt))
        cur = nxt

        with _lock:
            _jobs[jid]["sections"][i].update(
                speech=round(speech, 2),
                wpm=round(s["words"] / (speech / 60)) if speech else 0)
        parts.append(cur)

    _set(jid, message="joining and mastering")
    lst = work / "list.txt"
    lst.write_text("".join("file '%s'\n" % q.name for q in parts), encoding="utf-8")
    joined = work / "joined.wav"
    _ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "pcm_s16le", str(joined))

    name = "".join(c for c in (spec.get("name") or "voiceover")
                   if c.isalnum() or c in "-_ ").strip() or "voiceover"
    wav_out = config.OUTPUTS / (name + ".wav")
    mp3_out = config.OUTPUTS / (name + ".mp3")
    pipeline.master(joined, wav_out, mp3_out)

    total = pipeline.duration(wav_out)
    words = sum(s["words"] for s in sections)
    flagged = [x["i"] + 1 for x in job_state(jid)["sections"] if x["status"] == "check"]
    _set(jid, status="done", message="done",
         result={"wav": str(wav_out), "mp3": str(mp3_out), "seconds": round(total, 1),
                 "clock": "%d:%02d" % (int(total // 60), int(total % 60)),
                 "words": words, "wpm": round(words / (total / 60)) if total else 0,
                 "flagged": flagged})


threading.Thread(target=_worker, daemon=True).start()
