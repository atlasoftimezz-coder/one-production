"""Script -> mastered voiceover, with a read-check pass that catches misreads.

Chatterbox runaway is invisible to duration checks - the failure replaces words rather
than adding time - so ASR is the only reliable detector. Sections that fail the check are
re-rendered at a different seed automatically.
"""
import os, re, json, time, math, shutil, difflib, subprocess, pathlib
import numpy as np, soundfile as sf, librosa
import config

MAXC = 280          # Chatterbox degrades past ~300 characters
CHUNK_GAP = 0.20

# ---------------------------------------------------------------- script parsing

STAMP = re.compile(r'^\s*\[(\d+):(\d{2})\]\s*(.*)$')
MDHDR = re.compile(r'^\s*#{1,6}\s+(.*)$')
# A heading can carry its own timestamp: "## 00:27 - Water"
HDRSTAMP = re.compile(r'^(\d+):(\d{2})\s*[-–—:]*\s*(.*)$')
# Headings that introduce material for the editor rather than the narrator.
NOT_NARRATED = re.compile(r'not narrated|production note|b.?roll|shot list|fact.?check|'
                          r'thumbnail|title option|sponsor|references?$|sources?$', re.I)
# "**Target runtime:** 8 minutes" - script metadata, never spoken.
METALINE = re.compile(r'^\s*\*\*[^*]{1,40}:\*\*')

def parse_script(text):
    """Split into sections. [m:ss] Title and ## Title lines are treated as on-screen
    headings and are NOT spoken - the narration almost always repeats the name anyway."""
    sections, cur = [], None
    for line in text.splitlines():
        m = STAMP.match(line)
        h = MDHDR.match(line)
        if m:
            if cur: sections.append(cur)
            cur = {"title": m.group(3).strip(), "stamp": int(m.group(1))*60 + int(m.group(2)), "body": []}
        elif h:
            if cur: sections.append(cur)
            title = h.group(1).strip()
            ts = HDRSTAMP.match(title)          # "## 00:27 - Water" carries its own stamp
            stamp = (int(ts.group(1)) * 60 + int(ts.group(2))) if ts else None
            if ts and ts.group(3):
                title = ts.group(3).strip()
            cur = {"title": title, "stamp": stamp, "body": []}
        else:
            if cur is None:
                cur = {"title": "", "stamp": None, "body": []}
            cur["body"].append(line)
    if cur: sections.append(cur)
    out = []
    for s in sections:
        if s["title"] and NOT_NARRATED.search(s["title"]):
            continue                            # editor material, not narration
        body = "\n".join(s["body"]).strip()
        body = "\n".join(l for l in body.splitlines()
                         if not l.strip().startswith((">", "|"))
                         and not METALINE.match(l))
        body = re.sub(r'\*\[[^\]]*\]\*', '', body)          # production notes
        body = body.replace("—", " - ").strip()
        if body:
            out.append({"title": s["title"], "stamp": s["stamp"], "text": body,
                        "words": len(body.split())})
    return out

def _sentences(text):
    text = " ".join(text.split())
    out = []
    for p in re.split(r'(?<=[.!?])\s+', text):
        p = p.strip()
        if not p: continue
        while len(p) > MAXC:
            cut = p.rfind(", ", 0, MAXC)
            if cut < 40: cut = p.rfind(" ", 0, MAXC)
            if cut < 40: cut = MAXC
            out.append(p[:cut+1].strip()); p = p[cut+1:].strip()
        out.append(p)
    return out

def chunk_text(text):
    """Fewest chunks that fit MAXC, balanced by length so no short tail is stranded
    (a stranded tail renders with noticeably different prosody)."""
    s = _sentences(text); n = len(s)
    if n == 0: return []
    pre = [0]*(n+1)
    for i, x in enumerate(s): pre[i+1] = pre[i] + len(x) + 1
    width = lambda i, j: pre[j]-pre[i]-1
    INF = float("inf")
    best = [INF]*(n+1); best[0] = 0
    for j in range(1, n+1):
        for i in range(j):
            if width(i, j) <= MAXC and best[i]+1 < best[j]: best[j] = best[i]+1
    k = best[n]
    if k == INF: return [" ".join(s)]
    mean = pre[n]/k
    cost = [[INF]*(k+1) for _ in range(n+1)]; cost[0][0] = 0
    back = [[-1]*(k+1) for _ in range(n+1)]
    for j in range(1, n+1):
        for g in range(1, k+1):
            for i in range(j):
                w = width(i, j)
                if w > MAXC or cost[i][g-1] == INF: continue
                c = cost[i][g-1] + (w-mean)**2
                if c < cost[j][g]: cost[j][g] = c; back[j][g] = i
    out, j, g = [], n, k
    while g > 0:
        i = back[j][g]; out.append(" ".join(s[i:j])); j, g = i, g-1
    return out[::-1]

# ---------------------------------------------------------------- audio helpers

def _run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "ffmpeg failed").strip().splitlines()[-1][:300])

def duration(path):
    r = subprocess.run([config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)

def pitch_shift(src, dst, factor):
    _run([config.FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
          "-af", f"rubberband=pitch={factor}:formant=preserved:pitchq=quality", str(dst)])

def set_pauses(src, dst, target, thresh_db=-42.0, minlen=0.12):
    """Chatterbox gives no control over pause length; pause density is a delivery choice,
    so we rewrite the gaps afterwards."""
    y, sr = librosa.load(str(src), sr=None, mono=True)
    hop = 256
    db = 20*np.log10(np.maximum(librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0], 1e-7))
    quiet = db < (db.max() + thresh_db)
    runs, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]: j += 1
            runs.append((i*hop, j*hop)); i = j
        else: i += 1
    runs = [(a, b) for a, b in runs if (b-a)/sr >= minlen and a > 0 and b < len(y)-1]
    out, prev = [], 0
    for a, b in runs:
        out.append(y[prev:a]); out.append(np.zeros(int(target*sr), y.dtype)); prev = b
    out.append(y[prev:])
    sf.write(str(dst), np.concatenate(out), sr)
    return len(runs)

# Mastering target: YouTube's playback level, so YouTube never turns the voiceover down.
LOUDNESS_I = -14.0       # integrated loudness, LUFS
TRUE_PEAK = -1.0         # ceiling, dBTP
LOUDNESS_RANGE = 11
PRE_MASTER = "highpass=f=65,deesser=i=0.35,treble=g=2.5:f=7500"

def _loudnorm(extra=""):
    return f"loudnorm=I={LOUDNESS_I}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}{extra}:print_format=json"

def _loudnorm_json(stderr):
    return json.loads(stderr[stderr.rindex("{"):stderr.rindex("}") + 1])

def master(src, wav_out, mp3_out):
    """Two passes: measure first, then apply exactly the gain needed - a single loudnorm pass
    guesses and lands a dB or two off on long voiceovers. loudnorm's own true-peak limiter
    keeps the ceiling, so no extra limiter (which would add its own gain) follows it."""
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-i", str(src), "-af", f"{PRE_MASTER},{_loudnorm()}",
                        "-f", "null", "-"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed").strip().splitlines()[-1][:300])
    m = _loudnorm_json(r.stderr)
    measured = (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    _run([config.FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
          "-af", f"{PRE_MASTER},{_loudnorm(measured)},aresample=48000",
          "-ac", "1", "-c:a", "pcm_s16le", str(wav_out)])
    _run([config.FFMPEG, "-y", "-loglevel", "error", "-i", str(wav_out),
          "-c:a", "libmp3lame", "-b:a", "256k", str(mp3_out)])

# ---------------------------------------------------------------- read-check

_norm = lambda s: re.findall(r"[a-z0-9']+", s.lower())

def read_check(asr, wav, script):
    """condition_on_previous_text=False is required: long deliberate pauses make Whisper
    invent filler, which looks exactly like a TTS defect and is not one."""
    segs, _ = asr.transcribe(str(wav), language="en", condition_on_previous_text=False)
    ref, got = _norm(script), _norm(" ".join(x.text for x in segs))
    sm = difflib.SequenceMatcher(None, ref, got)
    diffs = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal" and (i2-i1)+(j2-j1) > 3:
            diffs.append({"op": op, "script": " ".join(ref[i1:i2])[:70],
                          "heard": " ".join(got[j1:j2])[:70]})
    return {"wer": round(1-sm.ratio(), 4), "script_words": len(ref),
            "heard_words": len(got), "diffs": diffs}
