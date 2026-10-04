# P6 manual check — Backups, System scan, PIN

Close any old black One Production window, then run `start.bat`. New buttons in the top bar:
**Backups**, **System** and a round **profile** button (on a narrow window they show as icons only).

- [ ] **1. Backups go to D:.** Open **Backups**. The folder says `D:\Backups\one-production`
  (drive C: has only ~10 GB free, so backups are kept on D:). Press **Back up now** — a backup
  appears with your projects, its size and the time. Check the zip is really in that folder.

- [ ] **2. Restore after a mistake.** Create a throwaway project, add a file, press **Back up now**.
  Delete the project (🗑 in the project list). In Backups it now shows **DELETED** — press
  **Restore**, confirm, and the project is back with its file and chat.

- [ ] **3. Restore an older version.** In any project, change something (e.g. let the agent edit a
  script), then restore that project from the backup made before. The older version comes back;
  the newer one is in the `trash` folder of your data folder (nothing erased).

- [ ] **4. Daily backup.** Leave the app running a few minutes after starting it: a **Daily** backup
  appears by itself (once a day).

- [ ] **5. System screen tells the truth.** Open **System**. Check: Windows 11 Pro, your RTX 5060 Ti
  with ~16 GB, ffmpeg with rubberband, Claude Code 2.1.284, Codex 0.157.1, your accounts signed in,
  the voice module. Drive C: shows amber (low space) — that's correct.

- [ ] **6. Optional PIN.** Click the round profile button, type your name, Save — the corner shows
  your initial. Set a PIN, press **Lock now**: the PIN screen appears; a wrong PIN is refused, the
  right one opens the app. Close the black window and start again: it asks for the PIN first.
  (Don't want a PIN? Remove it in the same place — it's off by default.)

- [ ] **7. Automatic check is green.** `docs\gates\P6-auto.md` ends with **RESULT: PASS**.

## Good to know

- Backups keep the newest 14; change it, or leave audio/video out to save space, in the Backups screen.
- Keys and passwords in chats or text files are replaced with `[REDACTED]` inside backups.
  Audio, video, Word files and images are stored exactly as they are.
- Restoring never overwrites in place: the current version moves to the trash folder first.
- 5 wrong PINs pause unlocking for a minute (longer after more misses). Forgot the PIN? Delete
  `profile.json` in your data folder while the app is closed — your projects are not affected.

## Sign-off

All ticked → **"P6 approved"** (P6 completes the MVP). Anything odd → tell me the number and what
you saw. P5's checklist (`docs\gates\P5-manual.md`) is still open too.
