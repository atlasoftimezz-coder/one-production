# P11 manual check: dev orchestration (dispatch.py) and Pexels stock

You need the One Production accounts you already use: one Claude login and one Codex login,
both signed in on the Accounts page. Each run below costs real usage: a coding task is one
unattended Claude Code or Codex session, and a review is one more. Run everything from the repo
folder in PowerShell or cmd. Do not use Git Bash, because it rewrites `/usage`.

## Dispatch

- [ ] **1. Which account would get a task?**
  `.venv\Scripts\python dev\dispatch.py pick "fix the usage page refresh"`
  The output lists each account with its % used and says which one is "picked". Nothing runs.
  Claude's `/usage` check spends no turn.
- [ ] **2. A small real task.** Pick a tiny, safe change, for example:
  `.venv\Scripts\python dev\dispatch.py run tidy-1 "Fix the typo 'recieve' wherever it appears in docs/"`
  It works in `build\dispatch\tidy-1` on branch `dispatch/tidy-1`. Your main folder and `main`
  stay untouched. When it finishes, the other model reviews the change and writes
  `dev\reviews\tidy-1.md`.
- [ ] **3. Review an existing change.** Ask the other model to review the last commit made in a
  Claude Code session:
  `.venv\Scripts\python dev\dispatch.py review HEAD`
  The script reads the commit's `Co-Authored-By: Claude` line, so Codex reviews it. It writes
  `dev\reviews\review-<sha>.md`.
- [ ] **4. Plan's manual item: read the 2 review files.** Are the findings useful (real bugs,
  missing tests, broken project rules), or noise (style comments, a retelling of the diff)?
  Write down what to change in the review prompt if they're noise.
- [ ] **5. Keep or throw away.** If you want the task's change, merge it:
  `git merge dispatch/tidy-1`. To throw it away, run
  `git worktree remove build\dispatch\tidy-1` and then `git branch -D dispatch/tidy-1`.
  `dispatch.py status` lists what is left.

## Pexels stock

- [ ] **6. Key.** System → Set up / modules → **Stock footage** → paste your Pexels key →
  **Check and save**. The status turns READY and shows only the last 4 characters. The key is
  stored in Windows Credential Manager under `OneProduction` / `pexels`.
- [ ] **7. In a project chat,** ask: "find 3 Pexels clips of a river at sunset and fetch the best
  one". No approval card appears. `stock/pexels-video-<id>.mp4` and `stock/CREDITS.md` show up in
  Files. Check that the clip matches what was asked for.
- [ ] **8. Episode with stock.** In **Make an episode**, the animator may now use Pexels clips
  for real-world subjects (at most about half the shots). Check that the clips fit what is being
  said, and that the description carries "Stock footage: Pexels".

Say **"P11 approved"** when 1-5 are done (6-8 can be done with the P10 production check).
