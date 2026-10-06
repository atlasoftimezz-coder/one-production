# P12 - Terms check, sign-in, OS MCP prompts, learning loop - plan

Owner, 2026-10-06: "do this: ... P12: terms check, Google sign-in, learning loop". Plan source:
`docs/PLAN.txt` R6, R7, R8, D7 and the P12 gate.

> Build: R8 terms check doc, subscription chaining (only if allowed), Google sign-in,
>        permission-prompted OS MCP write/exec, post-publish learning loop.
> AUTO: chaining stays disabled while terms-check.md marks any provider "not allowed";
>       OS write action always prompts; "allow for session" gone after restart.
> MANUAL: read terms-check.md and decide on chaining; approve or reject the first learning-loop suggestion.

## 1. Terms check (R8, D7) - done

- `docs/terms-check.md`: sources and quotes, read 2026-10-06.
- `app/terms.json`: the shipped verdicts. `app/terms.py`: `verdict`, `chaining_allowed`,
  `summary`.
- `accounts.switch_allowed(from, to, owner_allows)`: two logins of the same subscription service
  only if the owner's `allow_chaining` setting is on AND the terms say "allowed". On 2026-10-06
  Claude and Codex are "not_allowed", so chaining stays off.
- Used by the chat switch offer, team mode and dev dispatch.
- `GET/PATCH /api/terms`. Tests: `tests/p12/test_terms.py`.

## 2. OS-level actions always prompt (R6) - done

- `policy.risky(tool, input, root)` returns what is dangerous about an action that already asks:
  - deletes: rm, del, rmdir, Remove-Item, `git clean`, `git reset --hard`, format;
  - installs: pip/uv/npm/pnpm/yarn/winget/choco/scoop install, msiexec, Install-Module;
  - network uploads: curl -T/-F/--data @, Invoke-WebRequest/RestMethod -Method Post/Put, scp,
    sftp, rsync, `git push`, cloud copies;
  - file writes outside the project;
  - write/delete/move/exec tools of an MCP server.
- **A risky action always gets a card** with Deny as the safe default and **no "Allow this
  session" button**. The server ignores a "session" answer for it and allows it once only.
  "Allow this session" for a tool never covers its risky uses.
- Session allowances stay in server memory only (they are gone after a restart):
  - `GET /api/projects/{pid}/agent` lists them;
  - `DELETE /api/projects/{pid}/agent/allow/{tool}` revokes one;
  - the chat shows them as chips with ✕.
- **OS / extra MCP servers:**
  - `settings.mcp_servers`: {name: {"command", "args", "env"}} that the owner adds, e.g. Desktop
    Commander. They are handed to the Claude agent next to `studio` (and `davinci`).
  - None of their tools is known to the policy, so every call asks. Write/exec ones are risky.
  - `GET/PUT /api/mcp-servers`, and a small list in System.

## 3. Google sign-in (R7) - done

- Optional app login with Google, on top of (or instead of) the PIN.
- The owner supplies their own Google Cloud OAuth **desktop** client ID (and secret, kept in
  Credential Manager) and the allowed email addresses.
- Flow: OAuth 2.0 for installed apps with PKCE.
  1. The app opens the browser to Google's consent page.
  2. Google redirects to a one-time loopback listener `http://127.0.0.1:<random port>/`.
  3. The app exchanges the code for tokens.
  4. It reads the ID token's email; with `email_verified` and the email on the allowed list,
     the app unlocks.
- Nothing is stored except the client secret (Credential Manager) and the allowed list
  (settings). Tokens are dropped after the check.
- Code: `app/google_signin.py` (PKCE, one-shot loopback listener, token exchange, tokeninfo check of
  audience / issuer / verified email / expiry / allowed list); `app/profile.py` (`locked` = PIN or
  required Google sign-in). Routes: `PUT/DELETE /api/profile/google`, `POST
  /api/profile/google/start`, `GET /api/profile/google/status` (the last two work while locked).
- Tests (`tests/p12/test_google.py`): a stand-in Google (httpx MockTransport for the token and
  tokeninfo endpoints); the "browser" calls the loopback redirect. Wrong state, another app's
  token, unverified or expired email, refused code and strangers all stay locked.

## 4. Post-publish learning loop - done

- The owner uploads YouTube Studio's audience-retention export (Analytics > Advanced mode >
  Audience retention > Export > CSV) to the project and picks it in **Knowledge and learning >
  Learn from a published video**.
- `app/insights.py` (no AI):
  - reads the CSV with flexible headers: position as %, seconds or m:ss; retention as % or 0-1;
    prefers the "absolute" retention column over "relative";
  - finds at most 3 cliffs: points lost per 10 s of video, after the first 15 s, at least
    1.5 points / 10 s and twice the curve's median slope (a steady slope is not a lesson), 20 s apart;
  - maps each to the chapter (episode bundle `metadata.json`), the Fusion shot (`fusion.json`) and
    the words spoken then (voiceover subtitles); the report is saved to
    `<episode folder>/insights/retention-<time>.json`.
- `POST /api/projects/{pid}/insights {retention, account?}`: one plain AI call (the owner's pick, else
  the first signed-in Claude / Codex / API account with room today) turns the report into at most 3
  concrete lessons (JSON). No account, an AI failure or an unusable answer -> plain rule-based
  lessons, and the reply says why.
- Each lesson is saved as a **candidate** (`learning.save(kind='candidate', evidence={'type':
  'retention_insight', report, drop, drafted_by})`, the project's default scope). A forgotten lesson
  is never brought back by a new analysis.
- The owner approves ("Use as my guidance") or forgets it in Knowledge and learning. Nothing is used
  until approved.
- Tests (`tests/p12/test_insights.py`): CSV formats, drop detection, mapping to chapter / shot /
  words, AI-drafted candidates (stand-in Claude), approve + forget, the rule-based fallback, a flat
  curve, missing episode / file.

## Gate

`check.bat P12` runs tests/p0 … tests/p12. Manual checklist: `docs/gates/P12-manual.md`.
