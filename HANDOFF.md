# One Production — handoff

Read this first when picking the project up in a new session. It holds the context of the chat
that built it (2026-10-03 → 2026-10-05): what it is, how it's built, every decision and why, where
each phase stands, verified facts about the tools, the owner's preferences, and the traps already
hit. The master plan is `docs/PLAN.txt`; progress is `docs/phase-log.md`; changes are
`docs/CHANGELOG.md`.

**New person / new PC?** In Claude Code type **`/handoff`** inside the repo: it reads this file, the
phase log and the plan, checks the PC, runs the last gate and reports before touching anything
(`.claude/commands/handoff.md`; `CLAUDE.md` points every session here).

---

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
agent/           P5 agent mode: policy.py (free inside project, ask outside), runs.py (one live run per
                 project, replayable events, approval futures, Stop), studio.py (studio tools:
                 list_voices, script_stats, make_voiceover, voiceover_status), claude_agent.py
                 (Claude Agent SDK), codex_agent.py (codex app-server JSON-RPC)
                 team.py (P9 orchestrator: Team, Locks, SubRun, review/spend caps - deps injected)
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
modules.py       module registry (core / voice gpu|cpu|off / Phase-2 greyed)
installer.py     voice install recipe + resumable sha256 downloads + Installer (one OK per item)
setup.py         wizard state (setup.json), drives list
update.py        Updater (manifest, download, unpack beside, switch, rollback, keep 2)
migrate.py       data-folder schema migrations (additive only, rollback-tolerant)
diagnostics.py   diagnostics zip (redacted, emails masked, never chat text/files)
web/index.html   the whole UI (single file, vanilla JS, light/dark)
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
- **ChatGPT only through Codex logins** (user, 2026-10-05: "for chatgpt we can use codex") - no
  OpenAI API-key provider is planned.
- **Model + effort** (user: "toggle model and effort wherever we are using claude, likewise codex and
  anthropic api"): stored on the account (`model`, `effort`; "auto" = the client's default) and set
  from the pickers under the message box. Haiku gets no effort (the API rejects it).
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
| P10 Production engine (quality gates, research/script/animation/edit/packaging workers) | Phase 2 | — |
| P11 Dev orchestration (dispatch.py, cross-model code review) | Phase 2 | — |
| P12 Terms check, Google sign-in, learning loop | later | — |

Last gate: `check.bat P9` (see `docs/gates/P9-auto.md`; the 1 skip is the opt-in real-GPU test).
Version **0.9.0**. The user's habit: "if everything is fine start the next phase" — keep going
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
- **Codex 0.157.1**: `exec --json` has no limits in its stream; session files
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
  ~115 MB. Studio PC uses `D:\Software\Voice\ffmpeg.exe` and chocolatey's on PATH.

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

## 9. Testing conventions

- `tests\p<n>\` per phase; `tools\check.py` runs all earlier phases too (regression).
- Fakes: `tests\p2\fakes\fake_cli.py` (+ `.cmd` shims; `FAKE_MODE`, `FAKE_REPLY`, `FAKE_UTIL`, …),
  `tests\p4\fakes\server.py` (stand-in voice app), `FakeVoiceApp` (httpx MockTransport),
  `tests\p5\conftest.py` `FakeAgent` (scripted Agent-SDK client; honours hook "deny") and `FakeCodex`
  (app-server, incl. `model/list`), `tests\p9\test_team.py` `Deps` (stand-in app for the Team:
  accounts, clock, scripted master/worker turns), `FAKE_ARGS` (fake CLI writes its argv) and
  `API_BODIES` (fake Messages API keeps request bodies),
  `tests\p7\conftest.py` local Range-capable download server.
- Browser: `tools\ui_drive.py` drives the user's installed Chrome headless over DevTools; sections
  switched on by `UI_DRIVE_ACCOUNTS / SWITCH / VOICE / AGENT / P6 / SETUP / P8 / P9`. Fresh data folders
  open the setup wizard, so the driver presses "Finish later" first.
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
- In `ui_drive.py` JS, wrap snippets that declare `const` in `{ … }` — every `js()` call shares one
  global scope, so a second `const m` is a SyntaxError.
- The chat bubble has `white-space: pre-wrap`: HTML built with template literals inside it needs
  `white-space: normal` or its indentation shows as big gaps.
- `git grep` before publishing: the repo contains fake test keys (`sk-ant-api03-GOODgood…`,
  AWS's `AKIAIOSFODNN7EXAMPLE`) on purpose — they test the redaction.

## 11. Open items

1. P7 manual Part B and P8 friend test (real ~9 GB voice install; a friend installs from zero).
2. Choose where updates are hosted; then build with `--update-source`.
3. Decide whether to hide the four unnamed voices (`2f8d1479.wav`, `647376c1.wav`, `845106da.wav`,
   `b4ed042b.wav`) from the picker.
4. Free space on C: (≈11 GB free).
5. P9 manual check (`docs/gates/P9-manual.md`, uses real accounts - a team run costs a few
   messages' worth). Next build phase after sign-off: **P10 production engine**.
6. Known limits: Word scripts need Heading styles; moving the data folder later is manual;
   Claude Code/Codex aren't auto-installed; the setup wizard's big downloads are only proven with
   stand-ins until Part B is done.
7. Team-mode limits: Codex may apply edits inside its sandbox without asking, so for Codex workers
   the file locks rely on the plan keeping files apart (Claude workers are checked on every write).
   A team does not survive an app restart (its state is saved in `team.json` for reading only).
8. Voice module keeps the 8 GB VRAM rule (user, 2026-10-05: 4 GB+ "not required for now").
9. Commits after bb716e1 (P9 onward) are local only - don't push to GitHub until the user asks (2026-10-05).
