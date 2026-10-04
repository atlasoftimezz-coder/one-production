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
| P3 Usage page + handoff | 2026-10-03 | PASS 131/131 (incl. P0–P2, 3 browser runs) | PASS | yes (user, 2026-10-04) | user reported empty usage page → now reads live usage for free (Claude /usage, Codex session files); same-service switching off until R8 |
| P4 Voiceover in chat | 2026-10-04 | PASS 149/150 (incl. P0–P3, 4 browser runs; GPU test opt-in). Real GPU render (engaging script, Human, clone_exact, 155 wpm): read-check 10/10 ok first take, worst WER 0.036; SRT 1219/1219 words, 122 cues, 0 overlaps, ≤2 rows; F0 1.0 semitone off (limit 1.0); **loudness −17.0 LUFS — misses −16 ±0.5** (vo-app single-pass loudnorm) | PASS (user screenshots: voice render, play, clone, dark mode, export, delete) | yes (user, 2026-10-04: chose "Sign off P4, then agent next") | real render showed dialog word count ≠ spoken (1195 vs 1219) → counter now uses the voice app's exact rules + regression test; fake reset-time bug in P3 tests fixed |
| P5 Agent mode | 2026-10-04 | PASS 191/192 (incl. P0–P4; 2 agent fakes + browser run). Real runs: Claude login (read script, wrote notes.txt without asking, `dir` asked, Deny respected; studio tools list_voices + script_stats) and Codex login via app-server (same: reads via PowerShell free, write via apply_patch free, `dir` asked) | pending - user asked to start P6 before doing it (2026-10-04) | — | Claude logins + API keys through the official Claude Agent SDK; Codex through `codex app-server`; one policy (free inside project, ask outside); later phases renumbered P6–P12 |
| P6 Backups + system scan + PIN | 2026-10-04 | PASS 219/220 (incl. P0-P5, 6 browser runs). Real check on a copy of the user's projects: 195 MB backup in 0.4 s, delete + restore 14/14 files byte-identical. System scan matches this PC (RTX 5060 Ti 15.9 GB, ffmpeg+rubberband, Claude Code 2.1.284, Codex 0.157.1) and found drive C: at 10 GB free | pending | - | backups default to data_dir/backups; this user's set to D:\Backups\one-production (plan default, C: nearly full). Found + fixed while testing: same-second backup names could be pruned straight away; two trash moves in one second clashed |
