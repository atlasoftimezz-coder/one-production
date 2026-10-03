# Phase log

A phase is finished only when BOTH checks pass and it is approved here.
The next phase does not start before that.

- **Auto** = `check.bat P<n>` result, report in `docs\gates\P<n>-auto.md`
- **Manual** = your tick-boxes in `docs\gates\P<n>-manual.md`

| Phase | Date | Auto | Manual | Approved | Notes |
|---|---|---|---|---|---|
| P0 Foundation | 2026-10-03 | PASS 16/16 | PASS | yes (user, 2026-10-03) | — |
| P1 Shell + security | 2026-10-03 | PASS 67/67 (incl. P0 + browser) | PASS | yes (user, 2026-10-03) | UI redesigned after first look |
| P2 Accounts + providers | 2026-10-03 | PASS 98/98 (incl. P0, P1, 2 browser runs) | PASS 1–4 (user screenshots); 5–7 verified by Claude with real calls | yes (user: "if everything is fine start next phase") | picker-stuck-on-Demo bug found from user screenshot, fixed + tested |
| P3 Usage page + handoff | 2026-10-03 | PASS 131/131 (incl. P0–P2, 3 browser runs) | pending | — | user reported empty usage page → now reads live usage for free (Claude /usage, Codex session files); same-service switching off until R8 |
