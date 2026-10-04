# P5 manual check — Agent mode (works like Claude Code / Codex)

Close any old black One Production window, then run `start.bat`. Use your real accounts.
The agent works inside the project's own folder: it can read, create and edit files there
without asking. Commands, anything outside the project, the internet and GPU work ask first.

- [ ] **1. It works out the pace itself.** Open the project with the Buster voiceover
  ("New"), pick your **Claude** account, type: `Add 15 WPM and voice it again with Buster`.
  You should see its steps appear live (e.g. *Voiceover status*, *Make voiceover*), then a yellow
  **Make voiceover — needs your OK** card (unless you chose "Always allow" before). Allow it:
  a voiceover card appears and the agent reports the result when it finishes (it waits for it).

- [ ] **2. It edits your files.** Type: `Tighten the first paragraph of the script — make it punchier,
  keep the facts`. It should read the script and edit it **without asking**. The file in Files is
  updated (download it to check), and the reply says what changed.

- [ ] **3. Commands ask first.** Type: `Run dir and tell me what's in the folder`. A card shows the
  exact command and *Why it asks: runs a command on your PC*. Press **Deny** — it stops and says so,
  it doesn't try another way. Ask again and press **Allow once** — it runs and answers.

- [ ] **4. Stop.** Ask for something long (e.g. `Write three alternative intros, 150 words each`)
  and press the round **✕** button while it works. It stops; what it wrote so far stays.

- [ ] **5. Leave and come back.** Ask for something long again, click another project while it
  works, then come back. The reply is still there and keeps updating until it finishes.

- [ ] **6. Codex does the same.** Pick your **Codex** account and repeat 2 and 3. Same behaviour:
  edits inside the project without asking, commands ask.

- [ ] **7. Your own Claude Code setup stays out.** No reply mentions caveman mode or your personal
  settings.

- [ ] **8. Automatic check is green.** `docs\gates\P5-auto.md` ends with **RESULT: PASS**.

## Good to know

- **Allow this session** remembers that kind of action for this project until One Production is
  closed — never written to disk.
- Each project keeps its own agent memory per account: follow-up messages continue the same
  session. Switching to another account starts a fresh session that gets the recent chat.
- Every agent run uses your account's normal allowance (it shows on the Usage page, live).
  Agent turns use more tokens than plain chat because the agent reads files and tools.
- Want the old plain chat (cheaper, no file access)? Add `"agent_mode": false` to
  `%APPDATA%\OneProduction\settings.json` and restart.

## Sign-off

All ticked → **"P5 approved"**. Anything odd → tell me the number and what you saw.
