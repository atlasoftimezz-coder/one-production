# P10 (part 1) manual check — Script Studio

Close any old black One Production window, then run `start.bat`. Steps 1–3 cost nothing.
Step 4 uses your real accounts: a style guide, a script and 1–5 Opus reviews. The FacelessOS
files go with every call (~25–35k tokens each), so expect a run to use a good part of a 5-hour
window on one Claude login.

- [ ] **1. FacelessOS built in.** Open a project, click the page button (left of the microphone) →
  **New script** → **Settings** says "✓ built-in FacelessOS · 16 files". To try a newer version: paste
  its folder and **Save settings** → "✓ your FacelessOS folder"; empty the box to go back.
- [ ] **2. Competitor list in any format.** Paste a messy list (a channel link, two video links, a
  non-YouTube link), or upload a `.txt` / `.csv` / `.docx` / `.xlsx` list to the project and pick
  it under "…or a list file".
- [ ] **3. Captions only.** Start a run and watch "Competitors": it reads the captions (no video
  download, no AI). Files appear under `competitors/`: one transcript per video, `_numbers.md`
  (words per minute, sentence length, first 30 seconds) and later `style-guide.md`. Videos
  without captions are listed as "not transcribed".
- [ ] **4. The loop.** The style guide is written, then the script (`scripts/<topic>.md`), then Opus
  reviews it: a score card with nine bars (hook, structure, retention, rehooks, anti-AI slop,
  authenticity, style match, length, outro) and must-fix points. The writer rewrites, Opus
  reviews again — until every score is 8+ with nothing to fix (**Ready**), or it stops at 5
  rounds / no better for 2 rounds and keeps the best version (**your call**).
  Every version is kept: `scripts/<topic>.v1.md`, `.v2.md`, …
- [ ] **5. Read it.** Open the final script. Does it sound like your niche, with original wording?
  Do the reviewer's notes make sense?
- [ ] **6. Voice it.** The microphone next to the script in Files voices it as usual.
- [ ] **7. Cost.** Usage → "By role" shows Analyst, Writer and Reviewer. Acceptable?

Covered by the automatic tests only (stand-ins): a writer hitting its limit mid-loop and moving to
another account; stop rules (round cap, no better for 2 rounds, review budget); unreadable reviews;
the FacelessOS text never landing in project files, backups or diagnostics.

## Sign-off

All boxes ticked → write **"P10 scripting approved"**. Anything odd → the number and what you saw.
