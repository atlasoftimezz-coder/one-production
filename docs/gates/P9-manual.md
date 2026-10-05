# P9 manual check — Team mode (master + workers) and model / effort

Close any old black One Production window, then run `start.bat`. This uses your real accounts:
a team run costs a few times a normal message (a plan, each part, a review per part, a final review).

## Model and effort (2 minutes, no cost)

- [ ] **1. Pickers.** Pick your Claude account below the message box. Next to it: a model list
  (Opus 5.5, Sonnet 5.5, Haiku 4.5, Fable 5.1) and an effort list (auto, low … max).
  Haiku shows no effort (it has none). Your choice stays after closing and reopening the app.
- [ ] **2. Codex.** Pick your Codex account: the model list shows Codex's own models
  (e.g. GPT-6-Astra …) and their efforts (up to "ultra").
- [ ] **3. It is used.** Send one short message with Opus + "low effort". The grey line under the
  reply names the model that answered.

## Team mode (the plan's P9 check)

- [ ] **4. Settings.** Click the people button (left of the microphone). A chip appears:
  `opus·high → sonnet·medium ×3`. Click the chip: master / worker model and effort, workers at once,
  which account leads. Change nothing, close.
- [ ] **5. A 3-part task.** In a test project, give the team a goal with three parts, e.g.
  *"Write three files: hook.md with 3 hook ideas, outline.md with a 5-point outline, title.md with
  5 titles - all about why debt feels like a trap."* Watch: the master plans, three rows
  appear and work at the same time (WORKING), each goes IN REVIEW, then DONE, and a final review
  is written under the panel. The three files appear in Files.
- [ ] **5b. Your own parts.** Press **Parts…** (next to the chip), write two parts yourself (title, what to do,
  file name), press **Start team**. The panel says "your parts", there is no planning step, both
  parts run and are reviewed. Name the same file in two parts: the dialog warns they'll take turns.
- [ ] **5c. More workers.** In the chip set "Workers at once" to 4: a yellow warning explains it uses
  limits 4× as fast; save — the chip shows ×4.
- [ ] **6. Approvals still work.** If a worker wants to run a command, the card says which task
  asks ("hook ideas: Run command") — Allow / Deny behave as before. Stop (red X) ends the whole team.
- [ ] **7. Cost.** Open Usage: "By role" shows Team master and Team worker separately. Is the cost
  of that run acceptable to you?
- [ ] **8. Spend caps.** On your Claude card set "per day" to a small number (e.g. 1000 tokens),
  start a team: it pauses at once and says the daily cap is reached. Clear the box (no cap) and
  press **Resume** in the panel — it carries on. (Clear the cap afterwards.)

Not checked by hand (covered by the automatic tests with stand-in accounts): a worker hitting a
real usage limit and moving to another service with its handoff, the team pausing until a reset,
the third review round escalating, two workers wanting the same file.

## Sign-off

All boxes ticked → write **"P9 approved"**. Anything odd → tell me the number and what you saw.
