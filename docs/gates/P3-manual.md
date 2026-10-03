# P3 manual check — Usage page and handoff

Uses your real accounts (a few short messages). Close any old black One Production
window, then run `start.bat`. You need your Claude and Codex accounts from P2.

- [ ] **1. Usage page shows your real numbers straight away.** Click **Usage** (top right).
  Within a few seconds (↻ spins while it fetches) your Claude card says **EXACT** with the same
  **session %** and **week %** as Claude's own Settings → Usage screen, plus reset times and
  "as of" the time it was read — no message needed. Codex shows **EXACT** once it has replied
  at least once through this app.

- [ ] **2. Ranges.** Click **5 hours / Today / 7 days / This month** — the numbers change to match.

- [ ] **3. Matches the official screens.** Compare the Claude card with Claude's own
  **Settings → Usage** ("Current session" and "This week"). The numbers should agree within a
  point or two (they move as you use Claude elsewhere). Click ↻ to re-read.

- [ ] **4. Trigger a switch for real.** Your Claude account is probably below 85%, so lower its
  switch level: on the Claude card set **warn at 20**, **switch at 30**. Close Usage, pick your
  Claude account, send a short message. After the reply, an amber card appears:
  **"… is running low (NN%)"** with **Continue on Codex**.

- [ ] **5. Read the handoff.** In that card, click **view handoff**. You see your goal, the last
  request, project files and the conversation word for word. Close it.

- [ ] **6. Continue on Codex.** Click **Continue on Codex · …**. A line "Continued on …" appears,
  and Codex replies carrying on the same conversation (it knows what was said before). The
  account picker has moved to Codex.

- [ ] **7. Switch log.** Open **Usage** again — the **Switches** table shows Claude → Codex,
  "threshold", the % and the handoff file name.

- [ ] **8. Manual switch.** Click the ⇄ button next to the account picker. A card appears with
  the accounts that can take over and **not now**; click **not now** to dismiss it.

- [ ] **9. Put your levels back.** On the Claude card set **warn 70**, **switch 85** again.
  Use the ↑ ↓ arrows to put the account you want used first at the top.

- [ ] **10. Export.** Click **Export CSV** — a file downloads and opens in Excel with one row
  per reply.

- [ ] **11. Automatic check is green.** `docs\gates\P3-auto.md` ends with **RESULT: PASS** —
  131 tests including all earlier phases and three real-browser runs.

## Good to know

- Switching between **two Claude accounts** (or two Codex accounts) is deliberately greyed out
  with a reason. That stays off until the terms check in P11. Claude ↔ Codex ↔ API key is allowed.
- Handoffs are also saved automatically every 10 replies and when the app closes. They live in
  `%LOCALAPPDATA%\OneProduction\projects\<project>\handoffs\`.

## Sign-off

All ticked → **"P3 approved"**. Anything odd → tell me the number and what you saw.
