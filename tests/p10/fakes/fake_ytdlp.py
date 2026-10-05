"""Stand-in for yt-dlp (run as `python fake_ytdlp.py <yt-dlp args>`), shaped like yt-dlp 2026.08.

    channel / playlist + --flat-playlist -J   -> JSON with entries (id, title, view_count, duration)
    a video + --print-to-file ... -o ...      -> <id>.meta.json and <id>.en.vtt in the cwd
Special video ids:  NOCAPTION01 (no captions)   RATELIMIT01 (429 the first time)
                    GONEGONE001 (video unavailable)   AUTOCAPS001 (rolling auto captions)
FAKE_YTDLP_LOG = file: every command line is appended (tests count calls).
"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if os.environ.get("FAKE_YTDLP_LOG"):
    with open(os.environ["FAKE_YTDLP_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
url = args[-1]

CHANNELS = {
    "@mrfacts": [("FACTSVID001", "Why the Sahara was green", 900000, 600),
                 ("FACTSVID002", "The city under the sea", 5000000, 720),
                 ("FACTSVID003", "Ten lost cities", 2000000, 840),
                 ("FACTSVID004", "Old map secrets", 100000, 500)],
}

if "--flat-playlist" in args:
    key = url.split("youtube.com/")[1].split("/")[0]
    if key not in CHANNELS:
        sys.stderr.write("ERROR: [youtube:tab] This channel does not exist.\n")
        sys.exit(1)
    print(json.dumps({"channel": key, "entries": [
        {"id": i, "title": t, "view_count": v, "duration": d, "_type": "url"} for i, t, v, d in CHANNELS[key]]}))
    sys.exit(0)

vid = url.split("v=")[1][:11]
if vid == "GONEGONE001":
    sys.stderr.write("ERROR: [youtube] GONEGONE001: Video unavailable\n")
    sys.exit(1)
if vid == "RATELIMIT01":
    marker = Path.cwd() / ".ratelimited"
    if not marker.exists():
        marker.write_text("1")
        sys.stderr.write("ERROR: [youtube] RATELIMIT01: HTTP Error 429: Too Many Requests\n")
        sys.exit(1)
meta_name = args[args.index("--print-to-file") + 2]
title = {"FACTSVID002": "The city under the sea", "FACTSVID003": "Ten lost cities"}.get(vid, f"Video {vid}")
Path(meta_name).write_text(json.dumps({"id": vid, "title": title, "channel": "Mr Facts", "uploader_id": "@mrfacts",
                                       "duration": 120, "view_count": 12345, "upload_date": "20260901",
                                       "language": "en"}) + "\n", encoding="utf-8")
if vid == "NOCAPTION01":
    sys.exit(0)
sentence = (f"This is {title.lower()}, and you won't believe what happened next. Why did it vanish? "
            "Nobody knows for sure. But the clues tell a story. ")
if vid == "AUTOCAPS001":
    vtt = "WEBVTT\nKind: captions\nLanguage: en\n\n"
    words = (sentence * 20).split()
    for n in range(0, len(words), 6):
        a, b = " ".join(words[max(0, n - 6):n]), " ".join(words[n:n + 6])
        first, rest = (b.split(" ", 1) + [""])[:2]       # YouTube: the new words carry timing tags
        vtt += (f"00:00:{n % 60:02d}.000 --> 00:00:{(n + 2) % 60:02d}.000 align:start position:0%\n{a}\n"
                f"{first}<00:00:01.000><c> {rest}</c>\n\n")
        vtt += f"00:00:{(n + 2) % 60:02d}.000 --> 00:00:{(n + 3) % 60:02d}.000\n{b}\n\n"
else:
    vtt = "WEBVTT\nKind: captions\nLanguage: en\n\n" + "".join(
        f"00:00:{n:02d}.000 --> 00:00:{n + 1:02d}.000\n{line}\n\n"
        for n, line in enumerate([s.strip() + "." for s in (sentence * 20).split(". ") if s.strip()][:59]))
    vtt += "00:00:59.000 --> 00:01:00.000\n[Music]\n\n"
Path(f"{vid}.en.vtt").write_text(vtt, encoding="utf-8")
