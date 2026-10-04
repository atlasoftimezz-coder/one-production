# P4 manual check — Voiceover inside the chat

This one renders a real voiceover on your GPU (a few minutes). Close any old black
One Production window, then run `start.bat`.

- [ ] **1. Add a script.** Open or create a project and drop a script file into Files
  (`.docx`, `.txt` or `.md` — try `channel-2-script-01-engaging.txt` from Downloads).
  Hover it: a **mic** button appears next to the download arrow.

- [ ] **2. Open the voice dialog.** Click the mic. (First time: "Starting the voice engine…"
  for up to ~30 s — One Production starts the voiceover app for you.) The dialog shows the
  script, **spoken-word count**, sections, and about how many minutes it will be.
  *Check the word count makes sense* — titles, production notes and `**Label:**` lines
  are not counted, because they are not spoken.

- [ ] **3. Choose and start.** Voice **human_ref48.wav**, Delivery **Clone the reference
  exactly**, Pace **Target words per minute** = 155. (Optional fine-tune for this voice:
  exag 0.45, cfg 0.50, temp 0.75.) Click **Make voiceover**.

- [ ] **4. The GPU question.** A yellow box asks for your OK and says what it will use.
  Click **Always allow** (or Allow once).

- [ ] **5. Live progress.** A card appears in the chat with one pill per chapter turning
  green as each passes the read-check.

- [ ] **6. The result.** When it says **Done**: length, word count and wpm are shown; press
  play — **does it sound like the Human voice?** (ear check). The mp3, wav and srt appear in
  Files and as chips on the card.

- [ ] **7. Subtitles.** Download the `.srt` and open it with the mp3 in VLC (or drag both into
  a video editor) — the lines follow the speech.

- [ ] **8. Remembered.** Open the mic again and start another — no GPU question this time
  (you chose Always allow). Leave the project and come back: the cards are still there.

- [ ] **9. Automatic check is green.** `docs\gates\P4-auto.md` ends with **RESULT: PASS**.
  The real-GPU end-to-end report is in the phase log.

## Good to know

- **Word files:** chapter titles must use Word's **Heading** styles (Home → Styles) to be
  treated as titles. A Word file typed as plain paragraphs (like
  `channel-2-script-01-competitor-match-v2.docx`, with its "Script Metrics" block) is read top
  to bottom — the word count in the dialog will look too high. Fix the styles or paste the
  text instead.
- To stop "Always allow": Usage page has nothing to do with it — ask me, or delete
  `approvals.json` in your data folder. (A settings screen comes later.)
- Friends without the voiceover app see "The voice module isn't set up on this PC" with what
  to add — everything else keeps working.

## Sign-off

All ticked → **"P4 approved"**. Anything odd → tell me the number and what you saw.
