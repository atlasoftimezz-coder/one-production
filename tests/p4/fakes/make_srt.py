"""Stand-in for vo-app's make_srt.py: writes a one-cue SRT whose text is the script's first
spoken line, so tests can tell it really read the script file it was given."""
import sys
from pathlib import Path

audio, script, out = sys.argv[1:4]
assert Path(audio).exists(), "audio missing"
first = next(l for l in Path(script).read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#"))
Path(out).write_text(f"1\n00:00:00,000 --> 00:00:02,000\n{first.strip()}\n", encoding="utf-8")
