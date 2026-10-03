# P2 manual check — Accounts and real AI replies

This is the first phase that uses your real accounts. Each test message costs a tiny
amount of usage (a short reply). Close any old black One Production window, then run
`start.bat`.

- [ ] **1. Accounts button.** Top right there's an **Accounts** button. Click it — a panel
  opens saying "No accounts yet", with three tiles: Claude login, Codex login, Anthropic API key.

- [ ] **2. Add your Claude login.** Click **Claude login**, type a name (e.g. "Main Claude"),
  click **Continue to sign-in**. A separate window opens with Claude's own sign-in — sign in
  with the email you use for Claude. Back in the app, within a few seconds the account shows
  **Ready**, your email and "pro plan".
  *If it stays "not logged in":* finish the sign-in in the other window, then click **Check**.

- [ ] **3. Add your Codex login.** Same with **Codex login** (your ChatGPT email — a different
  email from Claude is fine). It shows **Ready** and "ChatGPT".

- [ ] **4. Add your API key** *(skip if you don't want to use one)*. Click **Anthropic API key**,
  paste the key, **Check and save**. It shows **Ready** and the last 4 characters of the key.
  A wrong key is refused with a message, and nothing is saved.

- [ ] **5. Real replies.** Close the panel. In the message box, the account picker (left of
  the text) lets you choose who answers. Pick your Claude account and send
  "Say hello in five words". A real reply streams in; under it you see the account name,
  the model and the token count.

- [ ] **6. Switch accounts mid-project.** Pick your Codex account, send another message.
  Codex answers in the same conversation, and remembers what was said before.

- [ ] **7. Exact usage.** Open **Accounts** again. Your Claude account shows two bars —
  **5-hour window** and **7-day window** — with percentages and reset times. These come from
  Claude itself, not an estimate. (Codex shows no bars: Codex doesn't report usage. That's
  expected; P3 estimates it.)

- [ ] **8. Turn off / Remove.** Click **Turn off** on one account — it disappears from the
  picker. **Turn on** brings it back. (Remove asks you to click twice; only try it on an
  account you're willing to sign in to again.)

- [ ] **9. Nothing secret on disk.** Optional: open `%LOCALAPPDATA%\OneProduction\accounts.json`
  in Notepad — you see names and emails but **no API key**. The key lives in Windows
  Credential Manager (search "Credential Manager" → Windows Credentials → `OneProduction`).

- [ ] **10. Automatic check is green.** `docs\gates\P2-auto.md` ends with **RESULT: PASS** —
  98 tests: all of P0 and P1 again, every account and error path against stand-in Claude/Codex
  programs, and two real-browser runs.

## Sign-off

All ticked → tell me **"P2 approved"**. Anything odd → tell me which number and what you saw.
