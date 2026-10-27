---
description: Load the full context of One Production and get ready to continue the build
---

You are taking over the One Production project from a previous build session. Do this before
writing any code:

First read the newest checkpoint at the top of `HANDOFF.md`. It records the
shared CLI and source work, local-only credentials, pending manual checks and
the owner's cancellation of Gemini. For voiceover continuation, also read
`../SESSION_HANDOFF_2026-10-27.md` when present and the 27 October session-close
section of `HANDOFF.md`; a fresh clone does not include the parent workspace.
If the owner asks to continue that existing VO task, follow its scoped resume
checklist. The full-phase
gate/new-PC onboarding below is for a broader project handoff, phase validation or
a changed environment; it is not required just to resume a short Peter voice test.
An explicit continuation request already supplies "start" for that requested work.
Keep broader phase/manual approvals pending unless actually completed.

1. Read `HANDOFF.md` completely - it holds the whole context: what the app is, architecture,
   every decision and why, phase status, verified tool facts, the owner's preferences, testing
   conventions, traps already hit, and open items.
2. Read `docs/phase-log.md` (what is approved / pending), the newest section of
   `docs/CHANGELOG.md`, and in `docs/PLAN.txt` the requirements (section 4) and the block of the
   next phase in section 7.
3. Check this PC, because it is probably NOT the PC the project was built on: paths in
   HANDOFF.md like `C:\Users\Work\...`, `D:\Software\Voice` and `D:\Backups\one-production` belong
   to the original studio PC. Find out what exists here: `git log --oneline -5`, `git status`,
   whether `.venv` exists (if not: run `setup_dev.ps1`), which Python/uv/Claude Code/Codex/Chrome/
   NVIDIA GPU are installed. Don't assume the voice app, voices or settings from the handoff are here.
4. Run the gate of the last built phase (`check.bat P12`, about 10 min) and report the result. On a new PC some
   checks may legitimately skip (no Chrome, no NVIDIA) - say which and why.
5. Then give the person a short summary in plain words:
   - where the project stands (phases done, pending manual checks)
   - what's different on this PC
   - the open items from HANDOFF.md section 11
   - what you propose to do next (normally the next phase in docs/PLAN.txt), and wait for "start".

Rules that carry over (details in HANDOFF.md section 8 and 10):
- Every phase ends with `check.bat P<n>` + a manual checklist in `docs/gates/P<n>-manual.md` +
  the owner's "P<n> approved" in `docs/phase-log.md`. Keep that.
- Ask before GPU work, large downloads or anything paid. Tests never use real accounts or the real
  data folder. Report honestly what was verified for real and what only with stand-ins.
- Never edit text files through PowerShell (it corrupts UTF-8); avoid `\n` inside bash heredoc
  Python patches.
- Commit after each step; push to `origin` (the private GitHub repo) when asked.

$ARGUMENTS
