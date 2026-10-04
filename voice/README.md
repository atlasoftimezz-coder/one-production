# Voiceover Studio

A local web front-end for the voice pipeline: paste a script, pick a reference clip and a
delivery preset, get back a mastered WAV and MP3. Everything runs on the machine it is
launched on — no account, no upload, no per-character cost.

The part that matters is the read-check. Chatterbox occasionally *replaces* words rather
than adding time, so a bad take is the same length as a good one and duration checks never
catch it. Every section is transcribed back and compared to the script; anything that fails
is re-rendered at a different seed automatically.

## Running it

```
run.bat                          http://127.0.0.1:8720
run.bat --host 0.0.0.0           reachable from other machines on the network
run.bat --port 9000              different port
```

`run.bat` uses the bundled interpreter at `../vo-studio/runtime/python` if it exists, and
falls back to whatever `python` is on PATH.

## Installing on another machine

The app itself is small. What it needs is a Python 3.10+ interpreter that already has the
heavy pieces:

```
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install chatterbox-tts faster-whisper
pip install -r requirements.txt
```

The CUDA build must match the GPU. Blackwell cards (RTX 50xx, sm_120) need cu128 or newer;
an older wheel installs fine and then fails at render time. Without a working CUDA setup it
falls back to CPU, which is roughly 20x slower but does work.

You also need **ffmpeg and ffprobe**. Any recent build works, with one caveat: the deep
presets pitch-shift through the `rubberband` filter, and not every distribution ships it.
The app checks at startup, prefers a build that has it, and warns in the banner if none does.

## Paths

`config.py` auto-detects everything and prints what it chose at startup. Override with
environment variables rather than editing the file:

| Variable | What it points at | Default |
|---|---|---|
| `VOAPP_FFMPEG` / `VOAPP_FFPROBE` | ffmpeg binaries | PATH, then `D:/Software/Voice` |
| `VOAPP_WHISPER` | CTranslate2 Whisper model dir | local dir, else downloads from HuggingFace |
| `VOAPP_VOICES` | reference clips | `D:/Software/Voice/voices` |
| `VOAPP_OUTPUTS` | finished files | `D:/Software/Voice/outputs` |

## Scripts

Headings are recognised and **not spoken** — the narration almost always repeats the name
in the first sentence, so reading the heading says it twice:

```
[0:42] Hashima Island        <- timestamp heading
## Hashima Island            <- markdown heading
## 00:27 - Water             <- heading carrying its own stamp
```

Also dropped, because these are written for the editor and would otherwise be read
aloud: whole sections whose heading mentions production notes, b-roll, shot list,
fact-check, thumbnail, title options, sponsor, references or sources; `**Label:**
value` metadata lines such as `**Target runtime:** 8 minutes`; blockquotes; table
rows; and inline `*[production notes]*`.

Scripts exported from Word or PowerShell are often **UTF-16**. Convert before
pasting, or the app sees mojibake rather than words.

## Pace modes

- **Natural** — no time-stretching. Whatever the voice does.
- **Target words per minute** — each section is stretched to hit the number.
- **Timestamp grid** — needs `[m:ss]` headings. Each section is stretched to fit its slot
  and padded so the next one begins exactly on its stamp. The gap setting is how much
  silence to leave at the end of each slot for a title-card cut.

A stretch beyond 0.5x–2.0x is refused rather than shipped; that much time-stretching smears
the audio. If you hit it, the target pace is too far from what the voice rendered.

## Reference clips

10–30 seconds of clean single-speaker narration, no music, minimal pauses. Uploads are
converted to 48 kHz mono and level-matched automatically.

Under about 8 seconds the clone stops tracking the source voice's pitch and drifts back
toward a generic register — it will still sound fine, just not like the clip you gave it.
The app warns when an upload is that short.

## Presets

| Preset | Character |
|---|---|
| Narrator | Even, mid-range, around 110 Hz |
| Documentary — deep | Pitched to ~90 Hz, pauses widened to 0.50s |
| Documentary — fast | Bright, pauses cut to 0.16s |
| Calm | Restrained and flat, for sombre copy |

Raising exaggeration widens pitch range but also **raises** pitch — it moves a voice toward
the bright end, not the deep one. Depth comes from the pitch factor instead (0.699 is about
six semitones down, with formants preserved).

## Subtitles

```
python make_srt.py <audio.wav> <script.md> <out.srt> [--chars 84] [--maxsec 6]
```

Timings come from ASR, but the **text comes from the script** and is aligned onto
those timings. Subtitles built straight from a transcription inherit its errors and
its formatting — you get `50 000` on screen where the script says `fifty thousand`.
Aligning keeps what appears identical to the copy that was written.

Cues break on sentence ends, cap at two balanced rows and 6 seconds, hold a 1s
minimum, and overlaps are clamped. Watch the reported characters-per-second: much
above 17 is hard to read, and at 180 wpm narration that is simply unavoidable.

## Other tools

`analyze_voice.py <clip>` profiles a candidate reference — pace, F0, pitch range,
pauses, dynamics. `bench.py "<file>|<offset>|<seconds>|<label>"` does the same for a
slice of a long file, for comparing against a competitor. `fidelity.py` sweeps
Chatterbox settings against a reference and ranks them by speaker similarity; run it
before committing to a long render, because the right exaggeration is voice-specific
(a flat, deep source wanted 0.45; a bright expressive one wanted 0.65).

## Layout

```
config.py     path detection and capability checks
pipeline.py   script parsing, chunking, pauses, mastering, read-check
engine.py     presets, job queue, the render/check/shape/master sequence
server.py     HTTP API and static file serving
static/       the UI, single file, no external requests
jobs/         per-job intermediates, safe to delete
```
