# One Production

Local Windows studio for YouTube production (FastAPI + single-page UI). Built in gated phases.

**New to this project or this PC? Run `/handoff`** - or read `HANDOFF.md` first. It holds the full
context: architecture, decisions, phase status, tool facts, preferences and known traps.

- Plan: `docs/PLAN.txt` · Progress: `docs/phase-log.md` · Changes: `docs/CHANGELOG.md`
- Dev setup: `setup_dev.ps1` · Run: `start.bat` · Gate: `check.bat P<n>` · Package: `tools/build_package.py`
- A phase is done only after its auto check, its manual checklist (`docs/gates/P<n>-manual.md`) and
  the owner's "P<n> approved".
- Ask before GPU work, large downloads or paid services. Tests never touch real accounts or data.
- Never edit text with PowerShell (breaks UTF-8). Paths in HANDOFF.md are the original studio PC's.
