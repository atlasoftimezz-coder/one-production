"""Build an SRT from rendered audio.

Timings come from ASR word timestamps, but the *text* comes from the script and is
aligned onto those timings. Straight ASR output would put "50 000" on screen where
the script says "fifty thousand", and would inherit any mishearing; aligning keeps
the subtitles identical to the copy that was actually written.

usage: make_srt.py <audio> <script.(txt|md)> <out.srt> [--chars 84] [--maxsec 6]
"""
import sys, re, difflib, pathlib, argparse
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pipeline, config
from faster_whisper import WhisperModel

ap = argparse.ArgumentParser()
ap.add_argument("audio"); ap.add_argument("script"); ap.add_argument("out")
ap.add_argument("--chars", type=int, default=84)    # two 42-char lines
ap.add_argument("--maxsec", type=float, default=6.0)
ap.add_argument("--minsec", type=float, default=1.0)
a = ap.parse_args()

sections = pipeline.parse_script(pathlib.Path(a.script).read_text(encoding="utf-8"))
script_words = []
for s in sections:
    script_words += s["text"].split()

asr = WhisperModel(str(config.WHISPER), device="cpu", compute_type="int8")
segs, _ = asr.transcribe(a.audio, language="en", word_timestamps=True,
                         condition_on_previous_text=False)
heard = [w for s in segs for w in (s.words or [])]
norm = lambda s: re.sub(r"[^a-z0-9']", "", s.lower())
h_norm = [norm(w.word) for w in heard]
s_norm = [norm(w) for w in script_words]

# Map each script word onto a heard word's timing.
timed = [None] * len(script_words)
for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, s_norm, h_norm).get_opcodes():
    if op == "equal":
        for k in range(i2 - i1):
            timed[i1 + k] = (heard[j1 + k].start, heard[j1 + k].end)
    elif j2 > j1:                       # replaced/inserted: stretch the heard span
        st, en = heard[j1].start, heard[j2 - 1].end
        n = max(i2 - i1, 1)
        for k in range(i2 - i1):
            timed[i1 + k] = (st + (en - st) * k / n, st + (en - st) * (k + 1) / n)

# Carry timings across any gaps so every word has one.
last = 0.0
for i, t in enumerate(timed):
    if t is None:
        timed[i] = (last, last)
    last = timed[i][1]

def flush(buf, cues):
    if not buf:
        return
    st, en = timed[buf[0]][0], timed[buf[-1]][1]
    if en - st < a.minsec:
        en = st + a.minsec
    cues.append((st, en, " ".join(script_words[i] for i in buf)))

cues, buf = [], []
for i, w in enumerate(script_words):
    trial = sum(len(script_words[j]) + 1 for j in buf) + len(w)
    too_long = buf and (trial > a.chars or timed[i][1] - timed[buf[0]][0] > a.maxsec)
    if too_long:
        flush(buf, cues); buf = []
    buf.append(i)
    if w.endswith((".", "!", "?")) and trial > a.chars * 0.45:
        flush(buf, cues); buf = []
flush(buf, cues)

# Never let one cue start before the previous ends.
for i in range(1, len(cues)):
    if cues[i][0] < cues[i-1][1]:
        cues[i] = (cues[i-1][1] + 0.01, max(cues[i][1], cues[i-1][1] + a.minsec), cues[i][2])

def ts(t):
    h, rem = divmod(max(t, 0), 3600); m, s = divmod(rem, 60)
    return "%02d:%02d:%02d,%03d" % (h, m, int(s), round((s - int(s)) * 1000))

def wrap(text, width=42):
    """Two balanced lines. Split at the word boundary nearest the middle so neither
    row runs long - a greedy fill leaves a full line above a near-empty one."""
    if len(text) <= width:
        return text
    words = text.split()
    best, best_cost = 1, None
    for i in range(1, len(words)):
        top, bot = " ".join(words[:i]), " ".join(words[i:])
        over = max(0, len(top) - width) + max(0, len(bot) - width)
        cost = over * 100 + abs(len(top) - len(bot))
        if best_cost is None or cost < best_cost:
            best, best_cost = i, cost
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])

out = []
for n, (st, en, text) in enumerate(cues, 1):
    out.append("%d\n%s --> %s\n%s\n" % (n, ts(st), ts(en), wrap(text)))
pathlib.Path(a.out).write_text("\n".join(out), encoding="utf-8")

covered = sum(1 for t in timed if t[1] > t[0])
print("%s: %d cues, %d script words, %d timed from ASR (%.0f%%)"
      % (a.out, len(cues), len(script_words), covered, 100*covered/len(script_words)))
print("last cue ends %s" % ts(cues[-1][1] if cues else 0))
