# P11 - Dev orchestration (dispatch.py) - plan

Owner, 2026-10-06: "continue on phase 11". Plan source: `docs/PLAN.txt` D1 and the P11 gate:

> dev\dispatch.json + dev\dispatch.py: rules pick claude or codex per coding task and move to
> the next one when an account runs low. Cross-model review: a change written by Claude is
> reviewed by Codex, and the reverse; notes saved to dev\reviews\<task>.md.
> AUTO: claude floor mocked low -> codex picked; Codex review file appears for a Claude-written change.
> MANUAL: read 2 review files - are they useful, not noise?

This is a tool for building One Production itself (the owner's Note 3: "I talk to the master
chat, it spawns other chats, it checks with another model"). It is not part of the app a
friend installs: `dev/` is not in `SHIP`, so the package never contains it.

## What it does

    .venv\Scripts\python dev\dispatch.py pick   [--kind K] "task text"
    .venv\Scripts\python dev\dispatch.py run    <task-id> "task text" [--kind K] [--provider P] [--no-review]
    .venv\Scripts\python dev\dispatch.py review <rev-range | branch> [--task ID] [--author claude|codex]
    .venv\Scripts\python dev\dispatch.py status

- **pick**: shows which account would get the task and why, with every account's headroom.
  No model turn: Claude's `/usage` is answered locally (0 turns, $0, the app's own safety stop
  applies), Codex's limits are read from its session files.
- **run**: picks a coder, makes a git worktree `build/dispatch/<task>` on branch
  `dispatch/<task>` from HEAD, runs the coder there through its official client, commits what
  it changed on that branch, then asks the other provider to review the diff and writes
  `dev/reviews/<task>.md`. The main working tree and `main` are never touched; the owner (or
  the master chat) merges the branch after reading the review.
- **review**: cross-model review of an existing change (for example commits written in a
  Claude Code session). The author comes from the commits' `Co-Authored-By:` lines
  (Claude / Codex), or `--author`. The reviewer is always the other provider.
- **status**: dispatch branches, their worktrees, and the review files.

## Rules (`dev/dispatch.json`)

- `floor_pct` (default 20): an account needs at least this much left in EVERY limit window
  (5-hour and weekly) to start a coding task; `review_floor_pct` (default 10) for a review.
- `rules`: first rule whose `kind` equals `--kind`, or one of whose `keywords` appears in the
  task text, gives the provider order; the last rule is the default (Claude, then Codex).
- Within one provider only the first enabled, signed-in account (by the app's account order) is
  used - moving between two logins of the same service stays off until the P12 terms check
  (D7, `accounts.SAME_SERVICE_SWITCHING`). So "runs low" moves Claude -> Codex or the reverse.
- Unknown usage (no data yet) does not block; the choice says "usage unknown".
- Pay-per-use API keys are never picked (no spending without the owner asking).

## Safety

- The coder runs unattended inside its worktree only. Claude Code: `--permission-mode
  acceptEdits` with a fixed tool list from `dispatch.json` (file tools + `python -m pytest`,
  `git status/diff/log`); anything else is refused by the CLI because nobody is there to
  approve it. Codex: `exec --sandbox workspace-write` with the worktree as its folder.
- The reviewer cannot change anything: Claude gets only Read/Glob/Grep, Codex `--sandbox read-only`.
- Accounts come from the app's own registry and login folders (`data_dir/accounts.json`,
  `profiles/`); `accounts.json` is only read. Calls are recorded in the app's usage ledger
  (roles `dispatch-coder`, `dispatch-reviewer`) so the Usage page counts them.
- Review prompts carry the diff, capped by `review.max_diff_chars` (a cut diff says so).

## Shared code

`usage_pct` (exact % from an account's saved limits, or the token estimate) moves from
`main.py` into `app/usage.py` so the app and dispatch read limits the same way.

## Tests (`tests/p11`, stand-in CLIs only)

- pick: Claude at 90 % (floor 20) -> Codex; Claude at 30 % -> Claude; a matching rule puts
  Codex first; turned-off / signed-out accounts are skipped; second Claude login is NOT used
  when the first is low (D7); both low -> no account, exit code 2, reset time shown.
- run: Claude writes a file in its worktree -> branch commit; Codex review file appears in
  `dev/reviews/<task>.md` with the diff in the reviewer's prompt; the reverse (Codex writes,
  Claude reviews); the main tree is untouched; the reviewer's command line is read-only.
- review: a commit with `Co-Authored-By: Claude` -> Codex reviews; reviewer below its floor
  -> the review file says "not reviewed" and why; a large diff is cut and says so.

---

# Stock footage - Pexels (P10 leftover, built alongside)

Owner, 2026-10-06: "I don't have Storyblocks API key so for now we can use Pexels stock footage
API". Pexels offers photos and videos (no audio API).

- Key in Windows Credential Manager (`OneProduction` / `pexels`), set from the Stock module
  panel; never in settings, logs, backups or diagnostics; the AI never sees it.
- `app/stock.py`: search videos / photos (orientation, minimum duration / size), pick the best
  file at or above 1280x720 and at most 1920x1080, download into the project's
  `files/stock/`, and keep `files/stock/CREDITS.md` (photographer + Pexels link - Pexels asks
  for credit and a link back).
- Worker tools `stock_search` (look, free) and `stock_fetch` (writes inside the project).
  The editor already takes clips and stills in an edit list, so the visuals worker can mix
  Pexels clips with drawn SVG shots. Packaging adds the credits to the description.
- Tests use a stand-in Pexels server (httpx MockTransport); no real key in tests.
