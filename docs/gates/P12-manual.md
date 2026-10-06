# P12 manual check - terms, risky actions, Google sign-in, learning loop

Close any old black One Production window, then run `start.bat`. Parts 2 and 4 use a real
account (a few short messages). Part 3 needs a Google Cloud project (free).

## 1. Terms check (the plan's P12 MANUAL item, no cost)

- [ ] **1. Read `docs/terms-check.md`.** It quotes Anthropic's and OpenAI's terms as read on
  2026-10-06. Both say: one subscription login per person, no moving work between two logins of the
  same service to stretch limits. API keys are fine.
- [ ] **2. Decide.** Chaining stays off while a provider is "not allowed": when a login hits its
  limit, a second login of the same service is listed as "the provider's terms don't allow it".
  The setting only matters if a provider's terms change. Tell Claude if
  heavy, unattended runs (team mode, episodes) should use an API key instead of your subscription.

## 2. Risky actions always ask (R6)

- [ ] **3. Session allowance.** In a test project ask the assistant to list the files with a
  command. On the card press **Allow this session**. A chip "Bash ✕" appears above the message box.
  Ask again: it runs without a card.
- [ ] **4. Risky stays on a card.** Ask it to delete a scratch file with a command (`del x.txt`).
  The card is marked as deleting files and has **no** "Allow this session" button. Deny it.
- [ ] **5. Revoke.** Press ✕ on the chip. Ask for the file list again: a card appears.
- [ ] **6. Restart.** Allow a command for the session, close One Production, start it again: the
  chip is gone and the next command asks.
- [ ] **7. Outside MCP (optional).** System > Outside MCP servers: add one you use (e.g. Desktop
  Commander, `npx -y @wonderwhy-er/desktop-commander@latest`). In a Claude chat every call of
  its tools asks; its write / run-process tools never offer "Allow this session".

## 3. Google sign-in (optional)

- [ ] **8. Google client.** In Google Cloud Console > APIs & Services > Credentials create an
  OAuth client ID of type **Desktop app** (consent screen: External, testing, your email as a test
  user). Copy the client ID and secret.
- [ ] **9. Set up.** Profile panel > Google sign-in: paste both, your email in "Allowed", tick
  "Required to open". Save.
- [ ] **10. Sign in.** Lock the app. The lock screen shows **Sign in with Google**; it opens your
  browser, you pick your account, the tab says "Signed in" and the app opens.
- [ ] **11. Someone else.** Lock again and sign in with a Google account not on the list: the app
  stays locked and says so. (Remove the setup afterwards if you don't want it.)

## 4. Learning loop (the plan's P12 MANUAL item)

- [ ] **12. Export.** For a published episode made by One Production: YouTube Studio > the video >
  Analytics > Advanced mode > Audience retention > Export current view > CSV. Unzip and upload the
  CSV to that episode's project.
- [ ] **13. Find the drops.** Knowledge and learning > Learn from a published video > pick the CSV >
  **Find the drops**. The note lists up to 3 moments (time, retention before -> after, chapter) and
  up to 3 suggested lessons appear in the list as "candidate".
- [ ] **14. Judge the first suggestion.** Open its Evidence: the moment, the shot and the words
  match what you see at that time in the video? **Approve** it with "Use as my guidance" if it is
  right, or **Forget** it. Only approved lessons reach the AI.

Not checked by hand (automatic tests with stand-ins): every risky command pattern, the PKCE and
token checks against a stand-in Google, CSV variants, the rule-based fallback without an account.
