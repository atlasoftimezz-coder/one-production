# One Production — handoff

Read this first when picking the project up in a new session. It holds the context of the chat
that built it (2026-10-03 → 2026-10-06, Claude and Codex): what it is, how it's built, every decision and why, where
each phase stands, verified facts about the tools, the owner's preferences, and the traps already
hit. The master plan is `docs/PLAN.txt`; progress is `docs/phase-log.md`; changes are
`docs/CHANGELOG.md`.

**New person / new PC?** In Claude Code type **`/handoff`** inside the repo: it reads this file, the
phase log and the plan, checks the PC, runs the last gate and reports before touching anything
(`.claude/commands/handoff.md`; `CLAUDE.md` points every session here).

---

## Checkpoint - Claude, 2026-10-10/11: /handoff run, Claude sign-in tokens

- **/handoff on 2026-10-10** ran on the studio PC itself (all paths in §2 exist; tools match §6).
  `check.bat P12` = 475/477: the 1 opt-in GPU skip, plus `tests/p6/test_ui_p6` "backup shows it
  as deleted". **Cause found and fixed (a test race):** the inline delete confirm hides the
  project's name at once, so "project deleted" passed before the DELETE request finished; the
  Backups screen then loaded while the project still existed (no DELETED badge). Both delete
  checks in `ui_drive.py` now also wait for the confirm row to go away, and that check prints the
  backup rows when it fails. Launching `check.bat` through `cmd /c` from
  PowerShell said "not recognized"; run `.venv\Scripts\python.exe tools\check.py P12` instead.
- **Claude sign-in token** (owner, 2026-10-11: "OAuth token is enough can you check and
  implement"). The owner's browser login wasn't working and kept asking for an API key; a
  pasted OAuth token was refused because it went into the API key box.
  - A Claude login can carry a token from `claude setup-token`: Credential Manager entry
    `OneProduction` / `claude-token:<account id>`, `token_hint` in accounts.json.
  - `accounts.claude_env()` is now the one place that builds a Claude login's environment
    (chat CLI, agent, dispatch). It always sets `CLAUDE_CODE_OAUTH_TOKEN`, empty when the account
    has none, so a token in the PC's own environment can't sign every account in as one person.
    Empty = unset was checked on the real Claude Code 2.1.284.
  - Real Claude Code facts (2026-10-11, fake token, free): `auth status` says `loggedIn: true`,
    `authMethod: "oauth_token"` and gives no email, without checking the token; a bad token only
    shows on the first message. `/usage` prints only a cost summary for token logins, so their
    meters fill from `rate_limit_event`s in replies.
  - Routes `PUT` / `DELETE /api/accounts/{id}/token`; `POST /api/accounts` takes `oauth_token`.
    UI: "Sign-in token" box on the Claude tile, "Use a token" / "Remove token" on Claude cards.
  - Tests: `tests/p2/test_token_login.py`; the fake CLI honours `CLAUDE_CODE_OAUTH_TOKEN`.
  - Gate after both changes: `check.bat P12` = **482 passed, 0 failed, 1 opt-in GPU skip**.
  - Not verified with a real token yet. Decision D2 still holds: use only your own
    subscription's token. Claude declined to wire in a token from someone else's account
    (asked about on 2026-10-11).

## Earlier checkpoint - Claude, 2026-10-06 (night): P12 built, handed to a friend

**Handover.** The owner, 2026-10-06: "Here after friend will take care you can push in git hub and
update handoff". Everything up to P12 plus this handoff is pushed to `origin/main`.
The friend continues from GitHub, probably on another PC: run `/handoff` first (paths below like
`C:\Users\Work\...` and `D:\Software\Voice` are the studio PC's). Nothing is half-done: the
working tree was clean at the push.

**What to do next, in order:**
1. `/handoff` checks: `setup_dev.ps1` if `.venv` is missing, then `check.bat P12` (last result
   476 passed, 1 opt-in GPU skip; one earlier run hung silently - if it does, stop it and rerun).
2. The manual checklists waiting on the owner (section 5) - mainly `docs/gates/P12-manual.md` and
   `docs/gates/P10-production-manual.md` (one real Fusion episode = **F5**).
3. F5 needs the owner present: GPU voiceover, DaVinci Resolve Studio 21.1+ open with both Resolve
   switches on, an approval card per Fusion shot script, and the owner renders. Ask first.
4. Parked until the owner provides access: research workers (NexLev / vidIQ) and Storyblocks
   (paid key; Pexels is used instead - its key lives only in Windows Credential Manager, never in
   files, so on a new PC the owner enters it again in the app).
5. Owner decisions still open: a source for 3D models; whether heavy unattended runs (team mode,
   episodes) should use an API key instead of a subscription (`docs/terms-check.md`).


**P12** (owner: "do this ... 8. P12: terms check, Google sign-in, learning loop"). Plan and
results: `docs/P12-plan.md`; manual checklist `docs/gates/P12-manual.md` (pending).
- **Terms (R8):** `app/terms.json` + `app/terms.py`; `accounts.switch_allowed` keeps same-service
  chaining off (Claude and Codex "not_allowed" on 2026-10-06). Open owner question: should heavy
  unattended runs use an API key?
- **R6:** `policy.risky()` - deletes / installs / uploads / outside writes / outside-MCP write-exec
  always get a card, never a session allowance. Allowances: memory only, chips + revoke
  (`DELETE /api/projects/{pid}/agent/allow/{tool}`). Outside MCP servers: `app/mcp_servers.py`,
  `settings.mcp_servers`, `GET/PUT /api/mcp-servers`.
- **R7:** `app/google_signin.py` (PKCE + loopback + tokeninfo, owner's own Desktop client, secret
  in Credential Manager). `profile.locked` = PIN or required Google.
- **Learning loop:** `app/insights.py` + `POST /api/projects/{pid}/insights` + "Learn from a
  published video" in Knowledge and learning. Retention CSV -> cliffs -> chapter / shot / words ->
  max 3 lesson candidates (AI or rule-based) -> owner approves.
- **Still open:** F5 (one real Fusion episode - needs the owner: GPU voiceover, Resolve open, cards);
  research workers (NexLev / vidIQ access) and Storyblocks (paid key) parked; 3D model source.

## Earlier checkpoint - Claude, 2026-10-06 (late): Fusion F2-F4 built

**Fusion episodes.** Owner, in order:
- "Animation, motion graphics should done in davinci fusion page not drawing";
- "No reference for each and every script ... new style for easy understanding ... super duper
  high quality with 1080p 60fps. not always same templeted style";
- "do this: F2-F5, P12, research/Storyblocks".

Choices: the AI writes Fusion scripts per shot (an approval card each), Fusion only, and the
owner renders.

- **Read `docs/P10-fusion-plan.md`.** It holds the research (manual chapters, API, the Bravos
  reference study), F1/F1b prototypes, and every Resolve 21.1 behavior measured live.
- **F2:** `app/fusion_kit.py` (kit v1, style-agnostic).
  - Shot scripts start with `from op_kit import *`, and the Claude agent swaps the kit in when
    the owner approves, because Resolve's sandbox has no `exec`.
  - `app/resolve_mcp.py` instructions include `fusion_kit.GUIDE`.
- **F3:** `resolve_runner.fusion_build` / `fusion_stills` and `app/fusion.py`, plus tools
  `fusion_setup` / `fusion_stills` (policy `STUDIO_FUSION`). They are offered only when Resolve
  control and the MCP are both on.
- **F4:** `app/agent/episode.py`.
  - Visuals: art.json → shots.json → fusion_setup → a script per shot → stills → master review.
    The worker must be Claude or API (`Task.providers`).
  - Edit: waits for the owner's render (state `waiting_render`);
    `POST /api/projects/{pid}/episode/render {video}` resumes, via `from_snapshot` after a
    restart.
  - `build_animation` is no longer offered to agents.
  - The episode panel got a render picker.
  - Manual checklist rewritten: `docs/gates/P10-production-manual.md`.
- **Verified live on Resolve 21.1:**
  - the runner built a 4-shot project with exact cuts and the voiceover on A1;
  - a kit-built shot rendered;
  - stills exported, and empty shots were reported.
- **Not run yet:** a whole episode with a real AI. That is F5, and needs the owner.
- **Test projects left in Resolve:**
  - "One Production (disposable)": OP Fusion prototype, OP Fusion style study 60p (with
    timelines Glyphs, Feasibility A-D);
  - "One Production episodes": OP runner test 1.
  All are safe to delete.
- **Still open:** P12, then research workers and Storyblocks (need access and keys); 3D models
  for realistic objects (the owner hasn't chosen a source).

**State at the end of 2026-10-06 (before the Fusion work):**
- **Built:** everything up to and including P11.
- **Extras today:** Pexels stock footage, and DaVinci Resolve's own MCP inside Claude chats.
- **Last commit:** `85af852`.
- **Gate:** `check.bat P11` = **414 passed, 0 failed, 1 opt-in GPU skip**.
- **Nothing has been pushed.** Every commit after `bb716e1` (P9 onward) is local only.
- **The owner's running One Production still runs old code.** They need to restart it
  (`start.bat`) and tick the Resolve MCP switch.
- **Left to build:** P12. Storyblocks and research workers wait for keys and accounts.
- **Manual checks still waiting on the owner:** P7 Part B, P8, P9, P10 part 1, P10
  knowledge/Resolve (now with step 9 for the MCP), P10 production and P11.
- **Owner's requests today, in order:**
  1. "Pexels instead of Storyblocks ... continue on phase 11".
  2. "can't access davinci inside one production ... find the root cause and fix".
  3. "give the architecture" (answered in chat; now in §3).
  4. "update handoff".

**Later that day: DaVinci Resolve from inside One Production.** Owner: "I cant access davinci
inside one production, I have turned on davinci claude mcp and configured" → "can you fix to
access davinci resolve from one production?" (plan approved; the owner chose "card every time"
and allowed real tests in project **new2** only).

- **Root cause.** The studio agent is isolated on purpose: its own `CLAUDE_CONFIG_DIR`,
  `setting_sources=[]` and `strict_mcp_config=True`, with only the `studio` MCP. So the owner's
  user-scope MCP in `~/.claude.json` (`DaVinci Resolve` → `C:\Program Files\Blackmagic
  Design\DaVinci Resolve\ResolveMCP.exe`) never reaches it. Its instructions also said "you
  never run Resolve yourself".
- **Fix: `app/resolve_mcp.py` plus the switch `resolve_mcp`** (Resolve panel → "Let the AI use
  DaVinci Resolve's own MCP"; `PATCH /api/resolve {"mcp": bool}`; `ONEPROD_RESOLVE_MCP_CMD`
  for tests).
  - When the switch is on, `ClaudeAgent` gets the `davinci` stdio server and
    `disallowed_tools=[mcp__davinci__run_script_unsafe]`.
  - `studio.RESOLVE_RULE` is **swapped** for `resolve_mcp.INSTRUCTIONS`. Appending an override
    next to the old rule failed in a real test: the agent kept refusing.
  - The saved session key becomes `<account>|resolve-mcp`, so switching starts a fresh session.
    Resuming the old session also kept the refusal (real test).
  - Policy:
    - the 7 read-only tools are allowed;
    - run_script, launch_resolve and the LUT/DCTL write/delete tools get an approval card with
      the full script (`describe`);
    - run_script_unsafe gets the new verdict **deny**, which `Runs.permit` and the Claude
      PreToolUse hook honour.
  - **Claude accounts only.** Codex app-server MCP calls have no approval step the app can
    hold, so Codex keeps the plan route.
- **Resolve on this PC is now Studio 21.1.0.17** (was 21.0.0.47).
- The runner's frame-rate readback is fixed (`resolve_runner.same_setting`: 30.0 == "30"). It
  had failed 3 of the owner's plans.
- **Real test done** in project new2 on an in-process instance on the real data folder; the
  owner's running app was not touched, and the settings file was not changed.
  - status and search_scripting_api ran with no card;
  - a read-only `run_script` appeared on a card and was approved once;
  - it returned "Studio 21.1.0.17, New Project 1, 0 timelines";
  - nothing in Resolve was changed.
- The owner must **restart One Production** and tick the switch (manual step 9 in
  `docs/gates/P10-knowledge-manual.md`). Tests: `tests/p10/test_resolve_mcp.py` (4) and the
  extended `test_ui_resolve`.

**Earlier that day: P11 + Pexels.** P11 and Pexels stock footage were built and committed
(`873b9f6`, `check.bat P11` = 410 passed, 0 failed, 1 opt-in GPU skip). Nothing has been pushed. Owner, 2026-10-06: "i dont have story
block api key so for now we can use pexel stock footage api ... continue on phase 11". Plan:
`docs/P11-plan.md`. Manual checklist: `docs/gates/P11-manual.md`, pending.

- **P11 dispatch** (`dev/dispatch.py` + `dev/dispatch.json`, reviews in `dev/reviews/`):
  - `pick`, `run <task> "text"`, `review <range|branch|commit>` and `status`.
  - Rules pick Claude or Codex; an account needs `floor_pct` (20 %) left in every window to code
    and `review_floor_pct` (10 %) to review.
  - Live usage comes from Claude `/usage` (0 turns) and the Codex session files. The shared
    `app.usage.usage_pct` replaces `main.py`'s copy.
  - Only the first signed-in account per service is used (D7), and API keys are never used.
  - A coder runs unattended in a worktree `build/dispatch/<task>` on branch `dispatch/<task>`.
    Claude runs with `acceptEdits` and the tool list from `dispatch.json`; Codex runs with
    `exec --sandbox workspace-write`. Its work is committed with a `Co-Authored-By: Claude|Codex`
    line.
  - When the coder hits a limit, its partial work is committed and the other model continues.
  - The other model reviews read-only (Claude `--tools Read,Glob,Grep`, Codex
    `--sandbox read-only`) and writes `dev/reviews/<task>.md`. A re-review gets `-2`, `-3` and
    never overwrites.
  - Calls go to the usage ledger as roles `dispatch-coder` / `dispatch-reviewer`.
  - `dev/` is not shipped in the package. 19 tests are in `tests/p11`. The fake CLI gained
    `FAKE_WRITE`, `FAKE_MODE_CLAUDE/CODEX` and `FAKE_CODEX_REPLY`.
  - **Never run for real yet.** That is the manual check, and it uses real usage.
- **Pexels stock** (`app/stock.py`, `app/agent/stock_tools.py`, key UI under Set up / modules →
  Stock footage, routes `/api/stock`, `/api/stock/key`):
  - The key is in Windows Credential Manager under `OneProduction` / `pexels`. **The owner's key
    is already saved there** (hint `…GpKr`).
  - Tools `stock_search` / `stock_fetch` run without a card (policy `STUDIO_STOCK`). They are
    offered only once a key is set.
  - Videos are mp4 between 720p and 1080p. Photos are saved at most 2560 wide.
  - Files land in `files/stock/`, with credits in `stock/CREDITS.md` + `credits.json`.
  - Shots JSON now takes `{"file": "stock/...", "anchor", "from" | "motion"}`.
  - The episode animator is told about stock (at most about half the shots) when a key is set.
  - Verified for real on 2026-10-06 (free):
    - A search returned good 1080p results.
    - A real 1080p clip (5.2 MB) and a photo were fetched.
    - That clip went through shots → edit → render → video gate and passed (throwaway test,
      deleted).
    - The key's rate header says limit **25,000**, not the documented 200/hour, so the app
      reports whatever Pexels says.
  - 11 tests in `tests/p10/test_stock.py`.
- Tests: every test (and every app server a test starts) now uses an in-memory keyring
  (`app/security/memkeyring.py`, set in `tests/conftest.py`). Two P7 setup tests were changed
  because Stock is no longer greyed "Phase 2".
- Codex on this PC is now **0.160.0** (was 0.157.1). The `exec` flags used are unchanged.

## Previous checkpoint - Codex, then Claude, 2026-10-05

**Where it stood (end of 2026-10-05):** everything in the plan up to and including P10 is
built and the full gate is green (`check.bat P10` = 380 passed, 0 failed, 1 opt-in GPU skip,
last commit `ccdfbb3` + this handoff). Nothing has been pushed. What's left: the owner's manual
checks (P7 Part B, P8, P9, P10 part 1, P10 knowledge/Resolve, P10 production = one full real
episode), the stock-footage/research workers (need the owner's keys), then **P11** and **P12**.

How we got here: Codex took over while Claude usage was exhausted and built the document
library and shared learning (owner: **"Start"**, then **"continue and update the handoff.md"**).
Claude then finished the Resolve adapter, real-manual import test and full gate (owner: "continue
implementation where it stopped"), switched newer manuals to "shown with a warning" and built
the rest of P10 (owner: **"show newer manuals with a warning, then start P10 rest"**). Manual
phase approvals are still pending. Do not restart from scratch.

### Committed and tested

- `688659c`: revised P10 implementation/gate plan in `docs/P10-knowledge-plan.md`.
- `fd0c0e6`: `app/knowledge.py`, local SQLite FTS5 library. PDF (pypdf 6.19.0), DOCX,
  Markdown and TXT; source hashes, physical PDF pages/section citations, scoped documents,
  background import, cancellation/retry, version filtering and recoverable removal.
  Eight foundation tests passed.
- `c95bd65`: **Knowledge and learning** button in the Files sidebar, `web/knowledge.js`,
  protected `app/knowledge_api.py` routes. Claude and Codex (including teams/Script Studio)
  get `knowledge_sources`, `knowledge_search`, `knowledge_read`. Agent/API regressions
  passed; isolated Chrome import/search/citation scenario passed.
- `450a25c`: `app/learning.py`, `app/learning_api.py`, `app/agent/learning_tools.py`.
  Explicit directions persist with project/channel/global scopes; exact quotations of
  current user directions can be remembered automatically. Worker suggestions remain
  candidates. Corrections supersede older entries; forgetting prevents automatic revival.
  Active memory is injected into every agent/team call. `record_execution` is the hook
  for future verified Resolve results; it is NOT wired to an executor yet. Thirty-one
  focused learning/API/agent/browser tests passed, including correction and forgetting.

- `e6b0ee0` (Claude, back on Claude Code): **constrained Resolve adapter**, steps 1-4 of the
  old next-actions list. Details in "Resolve adapter" below. `check.bat P10` = **PASS 348/349**
  (1 skip = opt-in GPU test). The P1 browser test hung on the first run (server log pipe full,
  §10 trap) - it now drains the log.
- Real manual through the importer (throwaway library in `build/`, deleted after; NOT the
  owner's app library): 4,351 pages in **65.7 s**, 6,205 passages, 0 pages flagged, library
  205 MB (175 MB is the copied PDF). 10/10 known questions (ripple trim, timeline frame rate,
  markers, append, render queue, normalize audio, proxies, scripting preference, Text+,
  SRT subtitles) found the right chapter in the top 3; 3-20 ms per search. pypdf warns that
  fontTools is missing for some heading fonts; text came through. Repeat with
  `tools/manual_probe.py <pdf> <empty folder> [version]`.

### Resolve adapter (how it works)

- Workers get `resolve_status` (installed version, on/off, allowed operations) and
  `resolve_propose_plan`. They can't execute. Policy allows both without a card (nothing runs).
- Plan = title, optional `requires_version`, settings (`timelineFrameRate`,
  `timelineResolutionWidth/Height` only), operations (`import_media`, `create_timeline`,
  `append_clips` (20.3.2+, per the API CHANGELOG fix), `add_marker`), citations. Anything else
  is rejected with the reason (render, scripts, unknown fields, outside/missing files, wrong
  extension, version too old, plan needs a newer Resolve than installed).
- Plans are saved in `projects/<id>/resolve/plans/` (outside `files/`, so an agent can't edit a
  reviewed plan) with a hash over plan + media size/mtime.
- Owner runs from **Resolve plans** (Files sidebar): ticks "I saved my open Resolve project",
  presses Run. Server re-checks: switch on, hash, live Resolve version, media unchanged. Then the
  fixed helper `app/resolve_runner.py` (own process, stdlib only) creates a NEW project in the
  Resolve folder "One Production (disposable)", applies the steps, saves, and reads back name,
  settings, timeline count, items, markers. Passed → `Learning.record_execution` → active
  recipe (application resolve, installed version, evidence = run id + plan hash + checks).
  Failed → no recipe; the panel says whether a partial project was left.
- Resolve control is **off by default** (`resolve_enabled` in settings.json; the panel switch
  writes it). Off = Resolve is never contacted, even for the version probe.
- Tests: `tests/p10/test_resolve.py` (7) + `test_ui_resolve.py` use
  `tests/p10/fakes/fake_resolve_runner.py`, a stand-in Resolve driven through the REAL executor
  (`ONEPROD_RESOLVE_RUNNER` / `app.state.resolve.runner`). Keep `tests/p5/test_codex_agent.py`'s
  tool list in sync when adding tools.
- **Never exercised against real Resolve.** Whether Resolve's `fusionscript.dll` loads in the
  app's Python 3.12, and whether the installed copy is Studio, are unknown until the owner
  does manual checks 6-8 in `docs/gates/P10-knowledge-manual.md`.

- `e79e8e7`: owner's choice - manuals newer than the installed software are shown with a
  per-passage warning (`newer_than_installed`) instead of hidden.
- **P10 rest built** (owner: "start P10 rest"; plan `docs/P10-production-plan.md`):
  `0b71ed8` R1 numeric gates (`app/gates.py`, `app/media.py`, `app/gates_api.py`, tool
  `quality_gate`; numpy added), `dadc83c` R2 ffmpeg editor (`app/editor.py`, heavy job
  `video_render`, tools `render_edit`/`edit_status`), `f92d9ea` R3 animation (`app/animation.py`,
  tool `build_animation`), `72335b1` R4 packaging (`app/packaging.py`, tool `package_episode`),
  `cfd9d04` R5 episode run (`app/agent/episode.py`, `web/episode.js`, "Make an episode").
  Details in "Production engine" below. `check.bat P10` = **PASS 380/381** (1 skip = opt-in GPU).
  The run before was 378/381: two P9 tests failed because the fake CLI's `FAKE_ARGS` log lost
  lines when two fake processes appended at once (proven: 37-39 of 40 lines kept); the fake now
  locks the file.

### Production engine (how it works)

- **Gates** (`app/gates.py`, no AI, CPU): voice = every read-check WER <= 0.05, median pitch within
  1 semitone of the reference clip (YIN-style estimator on numpy, ~0.1 s per file, within 0.3
  semitone on synthetic voices), -14 LUFS +/- 0.5, true peak <= -1.0 dBTP, subtitles ordered / not
  overlapping / not empty / covering the audio to 1.5 s; video = duration = voiceover +/- 0.5 s,
  stream drift < 40 ms, no black frames (blackdetect), >= 1280x720; package = YouTube's title /
  description / tag limits, chapters (optional; when present: >= 3, from 0:00, each >= 10 s),
  thumbnail 1280x720 <= 2 MB. A check is pass / fail / skip; required skips fail the gate (a clone
  can't skip its pitch check). Reports go to `gates/<stage>-<time>.json` in the project.
- **Editor** (`app/editor.py`): edit list JSON -> mp4. Visuals are hard cuts fitted to the voiceover
  to the frame (the last one absorbs up to 2 s). Stills move (push / pull / pans, zoompan on a 2x
  crop), clips are trimmed and held on their last frame, cards are drawn with Windows fonts copied
  into the work folder (no filter escaping). Optional music is ducked under the voice and the mix is
  re-mastered (two-pass loudnorm, TP -1.5 for AAC headroom). Every render runs the video gate. Title
  cards are dark navy because the gate flags near-black frames. Renders are a heavy job.
- **Animation** (`app/animation.py`): the worker draws SVGs (stickvid style, the owner's no-image-model
  rule). They are checked (no script / handlers / outside links) and screenshotted by the PC's own
  Chrome or Edge as an `<img>` (the browser's SVG sandbox), ~0.5 s each, 4 at a time. Each shot
  starts when its anchor words are spoken, found in order in the voiceover's SRT.
- **Packaging** (`app/packaging.py`): chapters from the script's `##` headings timed to the voice
  (short ones merge), bundle in `episode/<name>/` (video hard-linked), package gate.
- **Episode run** (`app/agent/episode.py`, on the Team machinery; `Team.run_worker` is now shared
  with Script Studio): script -> voice (app; voice gate) -> visuals (worker; build must succeed,
  then master review) -> edit (app; video gate) -> package (worker; package gate, then master
  review) -> master's final report. A worker's failed check returns as notes (gate_rounds = 3);
  an app stage's failed gate stops the run as "needs you". The owner OKs the heavy jobs in the
  dialog. Deviation from the plan, on purpose: voice and edit have no AI turn.
- Tests (all synthetic media, stand-in AI turns): `tests/p10/test_gates.py`, `test_editor.py`,
  `test_animation.py`, `test_packaging.py`, `test_episode.py` (the whole run with the real gates,
  editor, Chrome and packaging), `test_ui_episode.py`. Helpers in `tests/p10/media_fixtures.py`.
- **Not run for real yet**: a real episode with real accounts (AI usage: roughly one SVG per 3 s
  of video), a real voiceover through the episode, and how good Claude-drawn frames look at length.

### Not complete yet - next actions

1. Owner's manual checks: `docs/gates/P10-production-manual.md` (one full real episode - the plan's
   manual gate) and `docs/gates/P10-knowledge-manual.md` (steps 6-8 touch live Resolve - only with
   the owner present).
2. Not built in P10: stock footage (Storyblocks key / free sources) and research workers; they
   need the owner's sources and keys.
3. Then P11 (dev orchestration) and P12 (terms check, sign-in, learning loop).

### Sources and implementation details

- Owner's PDF: `C:\Users\Work\Downloads\DaVinci_Resolve_21.1_Reference_Manual.pdf`
  (183,184,250 bytes; 4,351 pages; 4,611 bookmarks; selectable text in 17 sampled pages).
  Contents-page leader glyphs need cleaning; figures still require the cited original page.
- Installed `Resolve.exe` reports **21.0.0.47**, while the PDF is **21.1**. Never assume
  a 21.1 feature exists in the installed API.
- Installed API documentation:
  `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt`
  and `CHANGELOG.txt` (21.0 Beta, May 2026). The README documents `CreateProject`,
  `CreateEmptyTimeline`, `ImportMedia`, `AppendToTimeline`, `SetSetting`, `GetSetting`,
  `AddMarker`, `SaveProject`, `GetVersionString` and `GetProductName`.
- Knowledge lives under the configured `data_dir/knowledge/`, outside Git. No embedding
  model, OCR, paid service or GPU is used by the importer. Relevant excerpts are sent only
  when an owner-selected AI account calls retrieval tools. Manual text is reference data,
  never permission. This is persistent memory, not model-weight training.
- `knowledge/context` assigns a channel and target Resolve version. Until a channel is
  assigned, automatic directions stay project-scoped. Explicit UI directions can be global.
- The new browser test is `tests/p10/test_ui_knowledge.py`, using the existing Chrome
  DevTools driver (`UI_DRIVE_KNOWLEDGE=1`). It must drain the test server's stdout in a
  background thread; otherwise the logging pipe fills and the browser hangs. Fixed.
- The Codex dynamic-tools assertion in `tests/p5/test_codex_agent.py` was extended to
  include knowledge and learning tools. Keep it in sync when adding Resolve tools.
- Full gate after the knowledge/Resolve extension: 348 passed; after P10 rest (2026-10-05):
  380 passed, 0 failed, 1 skipped (opt-in real GPU render).
- All commits are local. Nothing was pushed, and no real account, GPU or Resolve was used in tests.

## 1. What it is

A **local Windows app** for a YouTube creator (faceless explainer channels: finance, "every
element/substance" science explainers, ghost towns, animal facts). It runs only on the user's PC
(FastAPI on 127.0.0.1 + a single-page browser UI) and gives:

- **One chat per project** that works like **Claude Code / Codex** (agent mode): it reads and edits
  the project's files itself; commands, anything outside the project, the internet and GPU work ask
  first through approval cards.
- **Many AI accounts**, each with its own email: Claude logins, Codex (ChatGPT) logins, Anthropic
  API keys — each used only through its official client.
- **Usage page** with live 5-hour / weekly limits, and an **automatic handoff** (a `.md` of the
  whole conversation) at 85 % or at a limit, then switching to another account.
- **Voiceovers in the chat**: clone a reference voice (Chatterbox), read-check every section with
  Whisper (WER ≤ 0.05, auto re-render), master to **−14 LUFS / −1.0 dBTP**, mp3 + wav + subtitles.
- **Team mode** (P9): an Opus 5.5 master plans and reviews, up to 3 Sonnet 5.5 workers do the parts
  in parallel; **model + effort** pickable per account wherever a model is called.
- **Backups** (daily, restore in one click), **system scan**, optional **PIN**.
- **Setup wizard** for a brand-new PC, **one-zip package** with its own Python, **updates with
  rollback**, **diagnostics export** — so friends install their own copy (no shared server).

Origin: five handwritten notes (multi-account auth tokens + usage, local production app, project
chat box, local/cloud MCP, 85 % switch with automatic backup/handoff, an Opus master reviewing
Sonnet workers for voiceover/script/video editing/animation). See `docs/PLAN.txt` §2.

## 2. Where things are

| What | Where |
|---|---|
| Repo | `D:\Projects\YT project\one-production` (git, branch `main`) |
| Dev runtime | `.venv` (Python 3.12, uv) — `setup_dev.ps1` makes it from `requirements.lock` |
| Run (dev) | `start.bat` → `python -m app` → opens the browser with `#t=<token>` |
| Gate | `check.bat P<n>` → runs `tests\p0 … tests\p<n>`, writes `docs\gates\P<n>-auto.md` |
| User data | `%LOCALAPPDATA%\OneProduction` (projects, profiles, cache, logs, usage.sqlite, …) |
| Settings | `%APPDATA%\OneProduction\settings.json` (`ONEPROD_*` env vars override) |
| Package | `tools\build_package.py` → `dist\OneProduction-<v>-win64.zip` (~28.5 MB) |
| Publish update | `tools\publish_release.py --to <folder>` → zip + `latest.json` |
| Voice engine (ships) | `voice\` (portable copy: config via `VOAPP_*`, plus `tune.py`) |
| Studio PC's own voice app | `C:\Users\Work\Claude\voiceover\vo-app` (+ runtime `…\vo-studio\runtime\python\Scripts\python.exe`) |
| Studio PC's voices | `D:\Software\Voice\voices` (human_ref48, buster_ref48, adam_ref48, …) |
| Studio PC's backups | `D:\Backups\one-production` (C: is ~98 % full) |

On the studio PC `settings.json` points `voice_app_dir`/`voice_python` at the own vo-app, so the
Voice module shows "ready — your own voice app" and nothing is installed by the wizard.

## 3. Architecture (app/)

Overview (2026-10-06):

```
Browser UI (web/index.html + knowledge.js, resolve.js, episode.js)
   |  127.0.0.1 only; Host/Origin + session token (security/guard.py); optional PIN (profile.py)
   v
FastAPI app - main.py create_app() wires every service + route
   |- Chat & agents   agent/runs.py (one live run per project, cards, Stop)
   |                  agent/policy.py   allow / ask (card) / gate (heavy job) / deny
   |                  agent/claude_agent.py  Claude logins + API keys (Agent SDK); MCP servers:
   |                       "studio" (our tools) + "davinci" (ResolveMCP.exe, only when switched on)
   |                  agent/codex_agent.py   Codex logins (codex app-server; studio tools only)
   |                  agent/studio.py   studio tools: voice, knowledge, learning, Resolve plans,
   |                       gates, editor, animation, packaging, Pexels stock
   |                  agent/team.py (P9) / scriptflow.py (Script Studio) / episode.py (episode run)
   |- Accounts/usage  accounts.py (no secrets; keys in Credential Manager; per-login profile dirs)
   |                  providers/ (plain chat) · usage.py (ledger, usage_pct) · handoff.py · models.py
   |- Production      voice.py (vo-app over HTTP, R12 GPU gate via tools.py) · animation.py (SVG ->
   |                  PNG via Chrome/Edge; shots may be stock files) · stock.py (Pexels) · editor.py
   |                  (ffmpeg) · gates.py (measured checks) · packaging.py · competitors.py + faceless.py
   |- Resolve         A. plans: resolve.py validates a fixed op list -> owner runs -> resolve_runner.py
   |                     (own process) builds a NEW project; a passing run -> verified recipe
   |                  B. MCP: resolve_mcp.py gives ResolveMCP.exe to Claude chats; reads free,
   |                     run_script / LUT changes need a card, run_script_unsafe denied
   |- Knowledge       knowledge.py (SQLite FTS5 manuals, page citations) · learning.py (directions,
   |                  verified recipes)
   '- Platform        storage.py · backups.py · system.py · modules.py + installer.py + setup.py ·
                      update.py + migrate.py · diagnostics.py · config.py
Outside the app: dev/dispatch.py (P11, building One Production itself) · voice/ (engine copy that
ships to friends; the studio PC uses its own vo-app).
Data: %LOCALAPPDATA%\OneProduction (projects, profiles, usage.sqlite, knowledge, logs);
Credential Manager (API keys, Pexels key); %APPDATA%\OneProduction\settings.json.
```

Module by module:

```
main.py          create_app(): every route; reply pipeline (chat + agent); switching; voice; backups;
                 setup/modules/install; updates; diagnostics; sample project; shortcuts
__main__.py      launcher loop: restart in a new data folder; exit code 75 = "start another version"
config.py        Settings (pydantic): data_dir, host=127.0.0.1 only, claude_cmd/codex_cmd, voice_*,
                 agent_mode, … ; load order defaults → settings.json → ONEPROD_* env; save_setting()
security/        guard.py (Host + Origin + X-OneProd-Token on /api/*), redact.py (the ONE redaction fn)
storage.py       projects/<id>/{project.json, chat.jsonl, files/, handoffs/, agent-sessions.json};
                 delete = move to data_dir/trash (never erased)
accounts.py      registry accounts.json (no secrets); API keys in Windows Credential Manager (keyring);
                 per-account CLAUDE_CONFIG_DIR / CODEX_HOME under data_dir/profiles
providers/       plain-chat path: claude_cli.py (claude -p stream-json), codex_cli.py (codex exec --json),
                 anthropic_api.py (SSE), base.py (error classes limit/transient/auth/fatal, retries
                 only before the first token), demo.py
agent/           P5 agent mode: policy.py (free inside project, ask outside; verdicts allow / ask /
                 gate / deny), runs.py (one live run per
                 project, replayable events, approval futures, Stop), studio.py (studio tools:
                 list_voices, script_stats, make_voiceover, voiceover_status), claude_agent.py
                 (Claude Agent SDK), codex_agent.py (codex app-server JSON-RPC)
                 scriptflow.py (P10 Script Studio: analyst -> writer <-> Opus reviewer loop)
                 team.py (P9 orchestrator: Team, Locks, SubRun, review/spend caps - deps injected)
competitors.py   Script Studio: competitor links -> yt-dlp captions -> numbers (no AI)
faceless.py      FacelessOS skills (read from the user's folder only) + review rubric
models.py        model + effort table for every call path (Claude/API/Codex), validation, effort_for()
usage.py         ledger usage.sqlite (calls + role, switches, limit_hits), spend queries, CSV export
handoff.py       handoff.md writer (redacted) + continue prompt
chat.py/actions.py  prompt building; studio context for the assistant
voice.py         VoiceService (starts the voice app, jobs over HTTP, make_srt), VoiceJobs, VoiceTuner
scripts.py       read .docx/.txt/.md (UTF-16 aware); stats() mirrors vo-app's parse_script exactly
tools.py         R12 heavy-job gate: Approvals (once / always / revoke)
backups.py       zip + manifest + sha256, keep 14, redacted text, restore via trash, daily ticker
system.py        read-only PC scan (Windows, RAM, disks, GPU via nvidia-smi, ffmpeg, CLIs, …)
profile.py       optional PIN (scrypt), lock middleware returns 423
modules.py       module registry (core / voice gpu|cpu|off / Phase-2 greyed; stock = Pexels key on/off)
installer.py     voice install recipe + resumable sha256 downloads + Installer (one OK per item)
setup.py         wizard state (setup.json), drives list
update.py        Updater (manifest, download, unpack beside, switch, rollback, keep 2)
migrate.py       data-folder schema migrations (additive only, rollback-tolerant)
diagnostics.py   diagnostics zip (redacted, emails masked, never chat text/files)
web/index.html   the whole UI (single file, vanilla JS, light/dark); knowledge.js, resolve.js, episode.js add panels
media.py         ffmpeg/ffprobe measuring (loudness, black frames, pitch, SRT), find_ffmpeg, master()
gates.py         P10 numeric stage gates (voice / video / package); gates_api.py routes + reports
editor.py        P10 ffmpeg editor (edit list -> mp4), editor_api.py (+ animation/package routes)
animation.py     P10 SVG shots -> PNG (local Chrome/Edge) -> timed edit list; packaging.py upload bundle
resolve.py       P10 constrained Resolve adapter (plans, owner execution); resolve_runner.py the fixed helper
                 (same_setting: numbers compared as numbers)
resolve_mcp.py   Resolve's own MCP (ResolveMCP.exe) for Claude chats: find_exe, for_agent (server,
                 disallowed run_script_unsafe, instructions that replace studio.RESOLVE_RULE), status
stock.py         Pexels search / fetch (key in Credential Manager, files -> files/stock/ + CREDITS.md);
                 agent/stock_tools.py = stock_search / stock_fetch
security/memkeyring.py  in-memory keyring, used only by the test suite (PYTHON_KEYRING_BACKEND)
knowledge.py     P10 document library (SQLite FTS5); learning.py scoped directions + verified recipes
agent/episode.py P10 episode run (stages + gates on the team machinery)
dev/dispatch.py  P11 dev orchestration (not shipped): pick Claude/Codex by rules + headroom floor,
                 coder in a git worktree, cross-model read-only review -> dev/reviews/<task>.md
voice/           engine.py, pipeline.py, server.py, make_srt.py, config.py, tune.py, static/
```

## 4. Decisions (and why)

- **D1 Windows-native, no Firstmate** (Firstmate is macOS/Linux only). Its ideas become P11.
- **D2 Each account only via its official client.** Never extract or reuse OAuth tokens; only the
  user's own accounts. API keys only in Credential Manager.
- **D3 App login ≠ provider logins.** **D4 free-first** (ffmpeg editor default; Resolve Studio /
  Storyblocks optional). **D5 clone the reference exactly** (see §8). **D6 react to limits** (exact
  numbers now available, see §6). **D7 terms first**: same-service switching (Claude→Claude) stays
  OFF until the P12 terms check; Claude↔Codex↔API is allowed. **D8 many users = many installs.**
  **D9 assume a brand-new PC.**
- **Agent mode (P5)**, user: "it should work like claude code not claude chat, likewise codex".
  Chosen policy: **free inside the project folder, ask outside** (commands, outside paths, web,
  unknown tools → approval card; GPU/paid → R12 gate). API keys are agents too (Claude Code with
  `ANTHROPIC_API_KEY` in an empty per-account config dir). Without Claude Code an API key falls
  back to plain chat. `agent_mode: false` in settings restores plain chat (P2–P4 tests use it).
- The user's own `~/.claude/CLAUDE.md` (caveman mode etc.) leaked into the assistant because the
  data folder is under the home folder and Claude Code reads `.claude/CLAUDE.md` from parent
  folders → fixed with `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
  `setting_sources=[]`, `--disable-slash-commands`.
- **Mastering**: user set **−14 LUFS integrated, −1.0 dBTP** (YouTube level). Two-pass loudnorm
  (measure, then exact gain); the old single pass landed ~1 dB low.
- **Backups** go to `D:\Backups\one-production` on the studio PC (C: nearly full).
- **Phase-2 modules** are shown greyed with what they'll need — never as switches that do nothing.
- **The setup wizard** opens by itself only on a genuinely new install (not done, no projects, no
  accounts). It doesn't install Claude Code/Codex (shows + links). Moving the data folder is only
  automatic on a new install.
- **Team mode (P9)**, user: "use Opus 5.5 master reviewing and sonnet 5.5". Master = first Claude
  account (or the one chosen), Opus / high; workers Sonnet / medium on Claude accounts; Codex
  workers keep the account's own model/effort. The team is the project's one live Run (chat waits,
  one Stop, approval cards tagged with the task). Workers get a `SubRun` whose `lock_check` is
  consulted by the Claude PreToolUse hook and by `Runs.permit` (Codex file approvals). Limit →
  worker handoff `handoffs/team-<task>-<n>.md` (redacted), files released, re-pin by load then
  headroom, never Claude login → Claude login (`accounts.switch_allowed`, also used by `eligible`).
  The user sets workers at once (any number ≥ 1, default 3; the UI warns above 3) and may write
  the parts themselves (Parts… dialog → `tasks` on POST /team → no planning call; the master only
  reviews). Team members may not start Claude Code sub-agents (Task/Agent are refused), so the
  user's number is the only fan-out. No account → team pauses (Resume button; also re-checks every 60 s). Review: max 2 rounds,
  40k in / 8k out per round, 120k per task → otherwise "needs you". Spend caps per account
  (`cap_task_tokens`, `cap_day_tokens`, `cap_day_usd`) on the Usage page.
- **Script Studio (P10 part 1)**, user, 2026-10-05: "Master opus should be reviewer for scripting use
  facelessos … competitor list in any format … check the transcription style … looped until reviewer
  satisfied". Built as `app/competitors.py` (links from any text/CSV/DOCX/XLSX; yt-dlp captions,
  channel → top 5 by views of the last 30 uploads; numbers), `app/faceless.py` (find the skills
  folder, skills per role/format, the 9-line RUBRIC), `app/agent/scriptflow.py` (`ScriptRun(Team)`:
  analyst → writer ⇄ reviewer). Defaults chosen by Claude (user said "plan based on my inputs"):
  5 rounds, pass = every score ≥ 8 and no must-fix, stop if no better for 2 rounds, review budget
  60k/8k per round and 300k per script, skip videos without captions. FacelessOS v4 skills (16 .md, from
  `D:\Youtube\Faceless os\…\FacelessOS Core v4 (May 2026)\skills`) are **bundled in `facelessos/`
  and shipped in the package** — the owner's decision, 2026-10-05: "I can handle the licence do what i
  said" (Claude had flagged that it's paid course material). A folder set in Script settings wins
  (newer version); empty = bundled. Passed only in the agent's instructions — a test proves it never
  lands in project files, backups or diagnostics. yt-dlp is now an app dependency (`python -m yt_dlp`; `ONEPROD_YTDLP` = stand-in).
- **ChatGPT only through Codex logins** (user, 2026-10-05: "for chatgpt we can use codex") - no
  OpenAI API-key provider is planned.
- **Model + effort** (user: "toggle model and effort wherever we are using claude, likewise codex and
  anthropic api"): stored on the account (`model`, `effort`; "auto" = the client's default) and set
  from the pickers under the message box. Haiku gets no effort (the API rejects it).
- **Document library + learning (P10)**, owner via Codex, 2026-10-05: local SQLite FTS5 (no
  embeddings, OCR, GPU or paid service); directions remembered per project / channel / all;
  worker suggestions stay candidates; only a Resolve run that reads back correctly becomes a
  verified recipe. **Newer manuals** (owner, 2026-10-05): shown with a per-passage warning, not hidden.
- **Resolve (P10)**: workers only propose; the owner runs a plan from the panel; it always builds a
  NEW project in "One Production (disposable)"; fixed operation list; no rendering; off by default;
  the executor is a fixed helper in its own process (no worker-written code ever runs).
- **Production engine (P10 rest)**, owner: "start P10 rest". Free / CPU / no image models: the AI
  draws shots as SVG (the owner's stickman-explainer preference), the PC's Chrome/Edge rasterises,
  ffmpeg edits. Numeric gates decide every stage; the master reviews only the creative stages;
  voice and edit stages have no AI turn (Claude's call, recorded in `docs/P10-production-plan.md`).
  Renders are a new heavy job `video_render` (approval card / episode dialog tick).
- **P11 dispatch** (owner, 2026-10-06: "continue on phase 11").
  - It is a dev tool and is never shipped.
  - It uses only the first signed-in account per service (D7) and never API keys.
  - Floors: 20 % of usage left to code, 10 % to review (`dev/dispatch.json`).
  - The coder runs unattended but only inside its own worktree.
  - The reviewer can't edit anything.
- **Stock = Pexels** (owner, 2026-10-06: "i dont have story block api key so for now we can use
  pexel stock footage api").
  - The key lives in Credential Manager, and the AI never sees it.
  - `stock_search` / `stock_fetch` run without a card. That was Claude's call: one fixed site,
    the owner's own key, files only into `stock/`. The owner may want a card instead.
  - Storyblocks is still not built.
- **Resolve MCP** (owner, 2026-10-06: "can you fix to access davinci resolve from one production?").
  - The owner chose an approval card for every script and LUT change. Read-only Resolve tools
    run without asking. `run_script_unsafe` (full PC access) is never offered.
  - The switch is off by default and covers Claude accounts only. Codex can't hold an approval
    step for MCP calls.
  - This deliberately widens the earlier "workers only propose" Resolve decision: with the switch
    on, the AI drives Resolve directly, and the owner approves each change. The plan route stays
    for Codex and for reviewable multi-step edits.
- **Package**: zip with the official embeddable Python 3.12.10 (sha256 pinned in
  `build_package.py`); launcher `One Production.cmd` is never rewritten by updates (cmd.exe reads
  batch files while running). Update hosting is **not chosen yet** (shared folder / web / GitHub).

## 5. Phase status (gated: auto check + user's manual checklist + "P<n> approved")

| Phase | State | Commit |
|---|---|---|
| P0 Foundation | approved | 6259a93 |
| P1 Shell + security | approved | 7ef449f … fdcb3fa |
| P2 Accounts + providers | approved | ffffa14, 6f6b8d1 |
| P3 Usage + handoff + switching | approved | 9b0d35d, 0fa0643 |
| P4 Voiceover in chat (+ preview, clone, run cards, delete, dark mode, export fixes) | approved | 34cedfe … 58534f7 |
| P5 Agent mode (Claude SDK + Codex app-server) | approved | 03a550e, 5eee82f, 15a3ebf |
| P6 Backups + system scan + PIN | approved | 6fe5033 |
| P7 Setup wizard + modules | **built, manual pending** (Part B = real ~9 GB install in a new Windows user) | 2400ae9, 5347019 |
| P8 Package + updates + support | **built, manual pending** (a friend installs from zero, voices the sample) | d89fc06 |
| P9 Orchestrator + model/effort toggles | **built, manual pending** (`docs/gates/P9-manual.md`) | see git log |
| P10 part 1 Script Studio (competitors → FacelessOS script → Opus review loop; FacelessOS bundled) | **built, manual pending** (`docs/gates/P10-manual.md`) | 2964239 + FacelessOS-bundle commit |
| P10 knowledge + learning + Resolve adapter (+ Resolve MCP switch, frame-rate fix, 2026-10-06) | **built, manual pending** (`docs/gates/P10-knowledge-manual.md`, now 9 steps) | fd0c0e6 … e79e8e7, 85af852 |
| P10 rest: numeric gates, ffmpeg editor, animation, packaging, episode run; visuals now in Resolve Fusion (F2-F4; F5 = one real episode, pending) | **built, manual pending** (`docs/gates/P10-production-manual.md`: watch one full real episode) | 0b71ed8 … cfd9d04, 253a40f, 1624bc3 |
| P11 Dev orchestration (dispatch.py, cross-model code review) + Pexels stock | **built, manual pending** (`docs/gates/P11-manual.md`) | 873b9f6 |
| P12 Terms check, risky actions always ask, outside MCP, Google sign-in, learning loop | **built, manual pending** (`docs/gates/P12-manual.md`) | 1624bc3, e4129ad |

Last gate: `check.bat P12` = 476 passed, 0 failed, 1 skip (`docs/gates/P12-auto.md`; the skip is the opt-in real-GPU test).
All planned phases are built. Remaining: **F5** (one real Fusion episode, with the owner); research workers wait for the owner's NexLev / vidIQ access; Storyblocks for a paid key. Manual checks waiting on the user: P7 Part B,
P8 friend test, P9 team mode, P10 part 1 Script Studio, P10 knowledge/Resolve, P10 production (one real episode), P11, P12.
Version **0.10.0** in `app/version.py`; everything since is under "Unreleased" in the changelog (bump the
version when the owner wants a release / package). Note: P10 part 1 was built before P9's manual sign-off, at the
user's request ("can you create a plan for this" → plan approved). The user's habit: "if everything is fine start the next phase" — keep going
after a green gate, but report honestly which manual items they haven't confirmed.

## 6. Verified facts about the tools (on the studio PC)

- **Claude Code 2.1.284**: `-p --output-format stream-json --verbose --include-partial-messages`
  gives `text_delta`s; `rate_limit_event.rate_limit_info.unifiedWindows` has exact
  `five_hour`/`seven_day` utilisation. `claude -p /usage --output-format json` answers with
  0 turns and $0 — **run it from PowerShell/cmd, never Git Bash** (MSYS rewrites `/usage` into a
  path and the CLI spends a real turn). Prompts go via **stdin** (npm `.cmd` shims pass args
  through cmd.exe). `claude auth status --json` gives email/plan.
- **Claude Agent SDK 0.2.163**: no Windows wheel (Mac/Linux wheels bundle a CLI); the pure-Python
  sdist works with the installed Claude Code. SDK needs `claude.exe` itself
  (`npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`), not the `.cmd` shim. Permissions:
  a PreToolUse hook applies the policy instantly ("allow" / "ask"), `can_use_tool` waits for the
  user's card (no hook timeout). Background chores use Haiku — report the model with the most
  output tokens.
- **Codex 0.157.1** (now **0.160.0** on the studio PC, 2026-10-06; `exec --json --sandbox
  read-only|workspace-write -` unchanged): `exec --json` has no limits in its stream; session files
  `CODEX_HOME/sessions/**/rollout-*.jsonl` (written only without `--ephemeral`) hold exact
  primary 300-min / secondary 10080-min `used_percent` + `resets_at`. **`codex app-server`**
  (JSON-RPC, experimental API on): thread/start (cwd, sandbox `workspace-write`, approvalPolicy
  `untrusted`, `developerInstructions`, `dynamicTools`), turn/start, server requests
  `item/commandExecution/requestApproval` (with `commandActions` read/listFiles/search/unknown),
  `item/fileChange/requestApproval`, `item/tool/call`; notifications text deltas,
  item started/completed, `thread/tokenUsage/updated`, `account/rateLimits/updated`,
  `turn/completed`; `turn/interrupt` stops. On Windows Codex reads files with PowerShell — its
  `commandActions` decide read (free inside the project) vs command (asks). Tell it to edit with
  `apply_patch`, not shell.
- **Model / effort switches** (verified 2026-10-05): Claude Code `--model <alias|name>` and
  `--effort low|medium|high|xhigh|max`; Agent SDK `ClaudeAgentOptions(model=, effort=)`; Messages API
  `output_config: {"effort": …}` (Opus 5.5 default medium, Haiku 4.5 rejects effort; thinking is
  adaptive by default, `budget_tokens` 400s on current models); `codex exec -c
  model_reasoning_effort="…"`; app-server `model/list` (free, no turn) → id, displayName,
  supportedReasoningEfforts, defaultReasoningEffort, isDefault, hidden; `turn/start` takes `model`
  and `effort`. The studio PC's Codex login lists GPT-6-Astra (default), GPT-6-Sol, GPT-6-Luna,
  GPT-5.6-Sol/Terra/Luna, GPT-5.5, with efforts up to `ultra`.
- **Limits windows** whose `resets_at` passed count as 0 %.
- **GPU**: RTX 5060 Ti 16 GB, **sm_120 (Blackwell)** → PyTorch **2.8.0 + CUDA 12.8** required
  (cu124 builds have no kernels; `torch.cuda.is_available()` lies — prove with an fp16 matmul).
  `chatterbox-tts` pins torch 2.6 → install it with `--no-deps` plus its dep list.
- **ffmpeg**: gyan.dev essentials build includes rubberband; `.sha256` file is the bare hash; zip
  ~115 MB. Studio PC uses `D:\Software\Voice\ffmpeg.exe` and chocolatey's on PATH (8.0 essentials:
  zoompan, drawtext, subtitles/libass, sidechaincompress, tpad, blackdetect, libx264 all present).
  The app finds it via `ONEPROD_FFMPEG` → `data_dir/modules/ffmpeg` → PATH (`media.find_ffmpeg`).
- **Headless Chrome screenshots** (2026-10-05): `chrome --headless=new --screenshot=out.png
  --window-size=W,H file:///page.html` takes ~0.4 s; an SVG loaded as `<img>` cannot run scripts or
  fetch anything (an outside `href` just shows a broken-image icon). Edge works the same.
- **Pitch**: the app's own estimator (`media.median_f0`, numpy) read synthetic voices at 85-300 Hz
  within 0.3 semitone and a rubberband +6-semitone shift as 6.2; the P4 real test used librosa pyin.
- **DaVinci Resolve Studio 21.1.0.17** installed since 2026-10-06; it was 21.0.0.47 before.
  - External scripting is Studio-only and needs Preferences → System → General → External
    scripting = Local. `AppendToTimeline` had a fix in 20.3.2 (API CHANGELOG), so the adapter
    needs 20.3.2+ for it.
  - Live runs from the app (owner, 2026-10-06, project new1): 1 passed and 3 failed on the
    frame-rate readback (`GetSetting` returns 30.0 for "30"); now fixed.
  - **ResolveMCP.exe** (`C:\Program Files\Blackmagic Design\DaVinci Resolve\`) is an MCP
    server over stdio, newline JSON-RPC, protocol 2024-11-05, serverInfo `davinci_resolve` 21.1.
    Its tools:
    - read only: get_resolve_status, get_whats_new, get_scripting_api, search_scripting_api,
      get_scripting_docs, list_dctls, list_luts;
    - changes: run_script (sandboxed Python 3.14 + Resolve API), launch_resolve, update_dctl,
      generate_lut, delete_dctl, delete_lut;
    - full PC access: run_script_unsafe.
    Its instructions tell the client to call get_whats_new because 21.1 is newer than model
    training.
  - The owner also has it in their own Claude Code (`~/.claude.json` user-scope `DaVinci Resolve`).
- **Pexels API** (2026-10-06, the owner's key): `Authorization: <key>`.
  - Endpoints: `/videos/search` (size=medium = Full HD), `/v1/search`, `/videos/videos/<id>`,
    `/v1/photos/<id>`.
  - The key's `X-Ratelimit-Limit` is 25,000. Video pages carry their description in the URL slug.
  - Video files come from `videos.pexels.com`. Photos are `images.pexels.com/...jpeg` and take
    `?auto=compress&cs=tinysrgb&w=2560`.
  - A real 1080p clip is h264 with a second data stream; the editor ignores it and the video
    gate passed.
- **Real manual import**: the 4,351-page Resolve 21.1 PDF indexes in 65.7 s (`tools/manual_probe.py`).

## 7. Voiceover pipeline (what the voice module wraps)

vo-app (`voice\` ships the portable copy): `server.py` (`/api/meta`, `/api/jobs`, `/api/voices`),
`engine.py` (presets narrator, documentary_deep, documentary_fast, wildlife, **clone_exact**, calm;
render per section → Whisper read-check → re-seed failures → pace shaping natural / wpm / grid /
grid_pad → join → master), `pipeline.py` (`parse_script`: `## Title` and `[m:ss] Title` lines and
`**Label:**` lines aren't spoken; production-note sections skipped), `make_srt.py` (timing from
ASR, text from the script), `tune.py` (grid of exaggeration/cfg/temperature, scored by Chatterbox's
speaker encoder centred on other voices + the built-in voice, pitch tie-break → saved per voice in
`voice_settings.json` and used as that voice's defaults). Reference clips: 10–30 s, under ~8 s the
clone drifts. Word docs need Heading styles for titles.

**Two copies of the engine**: `voice\` (friends) and the user's `vo-app` (studio PC). Any change to
engine/pipeline/mastering must go into **both**. vo-app's previous mastering is kept as
`pipeline.py.bak-16lufs`.

Earlier voice work in this chat (outside One Production): Cancel-The-Debt VO with the Adam clone;
Human and Buster clones; ghost-towns VO; the substances script at 155/179 wpm with SRT; a
competitor-pace benchmark. The user once chose VoxCPM2 over Chatterbox by ear for long-form; for
user-supplied references, **fidelity to the clip beats any niche profile** (never pitch-shift a
user's voice toward a benchmark — that produced a "different person" once).

## 8. The owner's preferences and rules

- Plain words, short answers, honest status: say what was verified for real vs with stand-ins.
- **Ask before** GPU work, model/large downloads, or anything paid; never spend ElevenLabs
  characters without asking each time. No paid services by default.
- Clone a supplied reference voice exactly (`clone_exact`, no pitch shifting).
- Tests never touch the real accounts, Credential Manager or data folder (throwaway data dirs,
  fake CLIs, MemoryKeyring). Real-account checks: copy `accounts.json` into a temp data dir; they
  cost a little real usage — done several times with the user's go-ahead.
- The user often sends screenshots of what looks wrong; fix it, add a regression test, report.
- Windows-native everything; the user's friends will each install their own copy.
- Real tests touching their live tools only where they allow it (2026-10-06: Resolve tests "in
  new2 project for test purpose"). Read-only first; never switch or change their open Resolve project.

## 9. Testing conventions

- `tests\p<n>\` per phase; `tools\check.py` runs all earlier phases too (regression).
- Fakes: `tests\p2\fakes\fake_cli.py` (+ `.cmd` shims; `FAKE_MODE`, `FAKE_REPLY`, `FAKE_UTIL`, …),
  `tests\p4\fakes\server.py` (stand-in voice app), `FakeVoiceApp` (httpx MockTransport),
  `tests\p5\conftest.py` `FakeAgent` (scripted Agent-SDK client; honours hook "deny") and `FakeCodex`
  (app-server, incl. `model/list`), `tests\p9\test_team.py` `Deps` (stand-in app for the Team:
  accounts, clock, scripted master/worker turns), `FAKE_ARGS` (fake CLI writes its argv) and
  `API_BODIES` (fake Messages API keeps request bodies),
  `tests\p7\conftest.py` local Range-capable download server,
  `tests\p10\fakes\fake_resolve_runner.py` (stand-in Resolve driven through the REAL executor;
  `FAKE_RESOLVE_STATE / DOWN / FAIL / VERSION`), `tests\p10\media_fixtures.py` (synthetic voice,
  mastering, SRT, test-pattern videos with black frames / late audio, PNGs - all via numpy + ffmpeg),
  `tests\p10\test_episode.py` `Deps` (p9 Deps + the real gates/animation/editor/packaging).
- P11 / 2026-10-06 additions:
  - `tests\p11` (dispatch against a throwaway git repo). The fake CLI's `FAKE_WRITE="path=text"`
    makes an editing run write a file. `FAKE_MODE_CLAUDE` / `FAKE_MODE_CODEX` set the mode for one
    CLI only, and `FAKE_CODEX_REPLY` sets Codex's answer.
  - `tests\p10\test_stock.py` uses a stand-in Pexels (httpx MockTransport; `create_app(...,
    stock_transport=)`).
  - `tests\p10\test_resolve_mcp.py` uses a stand-in `ResolveMCP.exe` (`resolve_mcp_cmd` /
    `ONEPROD_RESOLVE_MCP_CMD`) and the P5 `FakeAgent`, which now also records `mcp_config` and
    `disallowed`.
- **Keyring**: `tests\conftest.py` points `PYTHON_KEYRING_BACKEND` at
  `app.security.memkeyring.MemoryKeyring`, so every test and every server a test starts runs
  without the real Credential Manager.
- Browser: `tools\ui_drive.py` drives the user's installed Chrome headless over DevTools; sections
  switched on by `UI_DRIVE_ACCOUNTS / SWITCH / VOICE / AGENT / P6 / SETUP / P8 / P9 / P10 / KNOWLEDGE /
  RESOLVE / EPISODE` (SETUP checks the Pexels key field; RESOLVE checks the MCP switch). Fresh data folders open the setup wizard, so the driver presses "Finish later"
  first. Every browser test must drain the server's stdout in a thread (see §10).
- Media tests skip without ffmpeg; animation/episode tests skip without Chrome/Edge.
- Opt-in real GPU test: `ONEPROD_GPU_TESTS=1 ONEPROD_REAL_SCRIPT=… ONEPROD_REAL_VOICE=human_ref48.wav`
  `pytest tests\p4\test_voice_real.py` (~7–8 min of GPU — ask first).
- `tests\p8\test_package.py` builds two real packages, installs, updates, rolls back — it works in
  `build\test-package` on D: (C: is full).

## 10. Traps already hit (don't repeat)

- **PowerShell 5.1 mangles UTF-8** (`·`→`Â·`, `—`→`â€”`, adds BOMs). Edit text with the editor
  tools or Python only. A mojibake guard test covers web/, docs/, README, pyproject.
- **Bash heredocs turn `\n` inside Python strings into real newlines** → broken source several
  times. Use patch files written with the Write tool (`pathlib` + exact `replace`).
- The editor treats `"\ufeff"` and a literal BOM as equal — write such strings via `chr(0xFEFF)`.
- Hard-coded dates in fakes become time bombs (a fake "resets Oct 3, 11:50pm" broke tests the next
  day) — always relative to now.
- Windows `\Temp` on C: fills fast with test runs (~1.6 GB cleaned once) — clean scratch and
  `pytest-of-Work` after big runs.
- Same-second names: backups and trash folders need counters that never reuse a pruned name.
- Sync FastAPI routes run on a worker thread — anything that schedules on the event loop
  (restart, relaunch) must be `async def`.
- Opening the wizard writes `setup.json`; "first run" must not be "no setup.json".
- yt-dlp facts (2026-10-05): `--flat-playlist -J <channel>/videos` gives view_count + duration per
  upload; `--skip-download --write-subs --write-auto-subs --sub-langs "en.*,en" --sub-format vtt`
  writes `<id>.en.vtt` (manual) or rolling auto captions (`en`/`en-orig`, words wrapped in `<c>` tags,
  every line repeated - `clean_vtt` drops the repeats); `--print-to-file "%(.{…})j"` gives metadata.
  A 26-min Veritasium video → ~5,000 clean words, 185 wpm. Bash on this PC can eat a backslash in
  `"a\\b"` inside heredocs - use raw strings in tests written that way.
- In `ui_drive.py` JS, wrap snippets that declare `const` in `{ … }` — every `js()` call shares one
  global scope, so a second `const m` is a SyntaxError.
- The chat bubble has `white-space: pre-wrap`: HTML built with template literals inside it needs
  `white-space: normal` or its indentation shows as big gaps.
- **Heredoc backslashes again** (3 more times on 2026-10-05): `'\n'` or `'\\'` inside Python written
  through a Bash heredoc came out as a real newline / lone backslash and broke `app/editor.py`. In
  code use `chr(10)` / `chr(92)` / `.splitlines()`, or edit with the Edit tool.
- **Browser tests hang if the server's log pipe fills**: drain `proc.stdout` in a thread (P1's test
  hung once a new request line pushed it over).
- **Appends from two processes interleave on Windows** (the fake CLI's `FAKE_ARGS` log lost lines →
  two flaky P9 tests): lock the file while writing.
- **UI race**: a panel that opens before its first fetch returns can have a quick click overwritten
  by the late answer (the Resolve switch did) - fetch, then open.
- The video gate flags near-black frames (blackdetect pix_th 0.10, 98 % of the picture): dark cards
  must stay above that (title cards are navy `#1B2A4A`).
- **Browser-test servers are subprocesses**: before 2026-10-06 they used the REAL Windows Credential
  Manager (the in-process `memory_keyring` fixture doesn't reach them). Now `tests/conftest.py` sets
  `PYTHON_KEYRING_BACKEND=app.security.memkeyring.MemoryKeyring` for the whole run, so no test can see
  the owner's saved Pexels key (found because the stock UI check saw it). `cmdkey /list` showed no
  leaked test entries.
- **Appending an override doesn't beat a base rule** (2026-10-06, real Resolve test).
  - With "you never run Resolve yourself" still in the studio instructions, an appended
    "this replaces the earlier rule" was ignored, and the agent refused `run_script`.
  - Swap the rule's text out instead (`studio.RESOLVE_RULE`).
  - A **resumed** Claude session also kept its earlier refusal. Changing what an agent may do
    needs a fresh session, so the session key now includes `|resolve-mcp`.
- A pydantic request model defined *inside* `create_app` (with `from __future__ import
  annotations`) is not resolved by FastAPI, which treats the body as a query and answers 422.
  Keep request models at module level.
- A `str.replace` inserted through a Bash heredoc put an unescaped `'` into a single-quoted Python
  string (stock_tools.py) - one more reason to edit with the Edit tool.
- MCP stdio probing from Python: read stdout in a thread. A plain `readline()` loop hung once;
  the threaded reader got `initialize` + `tools/list` from ResolveMCP.exe at once.
- `git grep` before publishing: the repo contains fake test keys (`sk-ant-api03-GOODgood…`,
  AWS's `AKIAIOSFODNN7EXAMPLE`) on purpose — they test the redaction.

## 11. Open items

1. P7 manual Part B and P8 friend test (real ~9 GB voice install; a friend installs from zero).
2. Choose where updates are hosted; then build with `--update-source`.
3. Decide whether to hide the four unnamed voices (`2f8d1479.wav`, `647376c1.wav`, `845106da.wav`,
   `b4ed042b.wav`) from the picker.
4. Free space on C: (≈17 GB free after cleaning test temp on 2026-10-05).
5. Manual checks of P9 (`docs/gates/P9-manual.md`), P10 part 1 (`docs/gates/P10-manual.md`), P10
   knowledge/Resolve (`docs/gates/P10-knowledge-manual.md`) and P10 production
   (`docs/gates/P10-production-manual.md`) - all use real accounts. The real AI loops (Script Studio,
   episode visuals/packaging) have only run with stand-ins; competitor captions and the real manual
   import were checked for real (free).
   - Live Resolve from the app: the owner ran plans (project new1, 1 passed and 3 failed on the
     frame-rate bug, now fixed). A read-only Resolve MCP script ran for real in new2.
   - Scripts that CHANGE Resolve through the MCP are untested for real; watch the first card.
   - Next: F5 (one real Fusion episode). P11 manual: `docs/gates/P11-manual.md`; P12 manual: `docs/gates/P12-manual.md`.
5a. The owner must restart One Production and tick "Let the AI use DaVinci Resolve's own MCP"
   (manual step 9). The MCP does not reach Codex accounts.
5d. dispatch.py has never run with real accounts (that's the P11 manual check). Its review
   prompt may need tuning after the owner reads the first 2 review files.
5b. Not built: Storyblocks (no key; Pexels is used instead, 2026-10-06) and research workers.
5c. Episode cost is untested: the visuals worker writes one Fusion script per spoken idea (an approval card
   each); watch Usage on the first real run. The bundle hard-links the video (no extra disk), but backups with media on (the default) zip it twice.
6. Known limits: Word scripts need Heading styles; moving the data folder later is manual;
   Claude Code/Codex aren't auto-installed; the setup wizard's big downloads are only proven with
   stand-ins until Part B is done.
7. Team-mode limits: Codex may apply edits inside its sandbox without asking, so for Codex workers
   the file locks rely on the plan keeping files apart (Claude workers are checked on every write).
   A team does not survive an app restart (its state is saved in `team.json` for reading only).
8. Voice module keeps the 8 GB VRAM rule (user, 2026-10-05: 4 GB+ "not required for now").
9. Pushed to GitHub on the owner's request (2026-10-06, handover to a friend): `origin/main` has
   everything through P12. Keep committing per step; push when the owner (or the friend) asks.
12. Learning loop: try it on the first published episode (Knowledge and learning > Learn from a
    published video). Google sign-in needs the owner's own Google Cloud Desktop OAuth client.
10. FacelessOS is bundled in the package by the owner's decision (licence is theirs to handle).
11. Team mode: the user sets "workers at once" (any number ≥ 1, warning above 3) and may write the
    parts themselves (Parts…); Script Studio defaults (5 rounds, pass 8, stall 2) were Claude's
    choice - the user may want to tune them after the first real runs.
