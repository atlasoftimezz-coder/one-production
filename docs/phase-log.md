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
| P5 Agent mode | 2026-10-04 | PASS 191/192 (incl. P0–P4; 2 agent fakes + browser run). Real runs: Claude login (read script, wrote notes.txt without asking, `dir` asked, Deny respected; studio tools list_voices + script_stats) and Codex login via app-server (same: reads via PowerShell free, write via apply_patch free, `dir` asked) | PASS (user) | yes (user, 2026-10-04: "P 5 approved") | Claude logins + API keys through the official Claude Agent SDK; Codex through `codex app-server`; one policy (free inside project, ask outside); later phases renumbered P6–P12 |
| P6 Backups + system scan + PIN | 2026-10-04 | PASS 219/220 (incl. P0-P5, 6 browser runs). Real check on a copy of the user's projects: 195 MB backup in 0.4 s, delete + restore 14/14 files byte-identical. System scan matches this PC (RTX 5060 Ti 15.9 GB, ffmpeg+rubberband, Claude Code 2.1.284, Codex 0.157.1) and found drive C: at 10 GB free | PASS (user) | yes (user, 2026-10-04: "P 5 approved and p6") | backups default to data_dir/backups; this user's set to D:\Backups\one-production (plan default, C: nearly full). Found + fixed while testing: same-second backup names could be pruned straight away; two trash moves in one second clashed |
| P7 Setup wizard + modules | 2026-10-04 | PASS 246/247 (incl. P0-P6; fresh-PC process test with a Windows-only PATH; wizard walked end to end in Chrome with a stand-in installer). Real checks: on this PC voice = ready (own voice app), nothing to install; bundled ./voice engine starts with the studio runtime; tune.py loads; friend route really downloaded Python 3.11.9 (sha256 verified), enabled pip and installed a package (8 s) | pending - user had no time, asked to start P8 (2026-10-04); Part B (real ~9 GB install) still to do before friends | - | Not run for real yet: the ~9 GB PyTorch/libraries/models install and tune.py on the GPU (manual Part A/B). Found + fixed: opening the wizard ended 'first run' (it wrote setup.json). Decisions: Phase 2 modules shown greyed, not as dead switches; Claude Code/Codex not installed by the wizard (shown + linked); data folder move only on a new install; API keys without Claude Code fall back to plain chat |
| P8 Packaging + updates + support | 2026-10-04 | PASS 261/262 (incl. P0-P7). Real end to end: two real packages built, the first extracted like a friend would and started through One Production.cmd on its own embedded Python 3.12.10, project + file created, update to v+1 from a release folder (sha256-checked, unpacked beside the running version), launcher restarted into it with data intact, rollback ran the previous version with data intact, diagnostics zip without key or chat text | pending | - | package 28.5 MB (dist/OneProduction-0.8.0-win64.zip). claude-agent-sdk has no Windows wheel - its pure-Python sdist is built into the package (uses the user's Claude Code). Found + fixed: version switch ran on a worker thread (no event loop to schedule the restart). Update hosting (where the release folder lives) is the user's choice - not set yet |
| P9 Team mode + model/effort | 2026-10-05 | PASS 299/300 after the own-parts + worker-count change (first run 295/296) (incl. P0-P8; 34 new: team logic with stand-in accounts, routes, headless Chrome). Real runs on the studio PC: Codex agent turn on gpt-5.5 / low effort (answered OK); one-task team run - Opus 5.5 master planned, Sonnet 5.5 worker wrote hello.md, master reviewed (1 round, pass) and wrote the final review, ~$0.30 in all | pending | - | not run for real: limit re-pin, pause/resume, spend caps, 3 parallel workers (stand-ins only); API-key effort only with the stand-in API (pay-per-use, not spent). Codex workers' file locks rely on the plan (Codex may edit without asking) |
| P10 part 1 Script Studio | 2026-10-05 | PASS 321/322 (incl. P0-P9; 22 new: links from any format, captions with a stand-in yt-dlp, the review loop and its stop rules, routes, FacelessOS never on disk, headless Chrome). Real: captions of 3 Veritasium videos read and measured (free) | pending | - | not run for real yet: the AI loop (style guide, script, Opus reviews) - costs real usage |

P10 knowledge/learning extension: owner authorized implementation on 2026-10-05.
Plan: `P10-knowledge-plan.md`. Existing manual approvals remain pending.

P10 knowledge checkpoint (2026-10-05): document library and scoped learning committed through
`450a25c`; 31 focused integration/browser tests passed. Resolve adapter, large-manual import
and final full regression gate remain pending. See HANDOFF.md continuation checkpoint.

P10 Resolve checkpoint (2026-10-05, Claude): constrained Resolve adapter committed in `e6b0ee0`
(workers propose, owner runs on a new disposable project, verified runs become recipes).
`check.bat P10`: **PASS 348/349** (incl. P0-P9; 1 skip = opt-in real GPU test). First run was
347/349: the P1 browser test hung because its server log pipe filled - the test now drains it.
Real manual (not the owner's library, a throwaway one): 4,351 pages in 65.7 s, 6,205 passages,
0 pages flagged, 10/10 known questions found the right chapter in the top 3, 3-20 ms per search.
Not run for real: anything against live Resolve (needs the owner's OK and Resolve Studio).
Manual checklist: `gates/P10-knowledge-manual.md` - pending.
