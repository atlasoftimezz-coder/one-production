# One Production

A local production studio: one chat, any number of your own AI accounts, a usage page
with automatic handoff between accounts, and production tools (starting with voiceover).
Runs on your own PC; nothing is shared with anyone.

Full plan: `docs\PLAN.txt` · Progress: `docs\phase-log.md`

For the new Voiceovers workspace, start with the [short user guide](docs/VOICEOVERS_USER_GUIDE.md).
Testing on another PC? Follow the [RTX 5070 / first-PC test checklist](docs/VOICEOVERS_PC_TEST.md).
To connect your Drive from the MCP page, follow [Google Drive setup](docs/GOOGLE_DRIVE_SETUP.md).
For n8n, Nexlev, Neon, Discord scripts and Agent Reach X accounts, see [Sources and MCP](docs/SOURCES_AND_MCP.md).
For the shared CLI workspace (PowerShell, Claude Code and Codex), see [CLI setup](docs/CLI_WORKSPACE.md). The [Claude-specific guide](docs/CLAUDE_CODE.md) covers saved OAuth and native setup.

## Setup (development)

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).

```
powershell -ExecutionPolicy Bypass -File setup_dev.ps1
```

## Run

```
start.bat
```

Opens `http://127.0.0.1:8730/` (next free port if that one is taken). The app only ever
listens on 127.0.0.1 — it is not reachable from other machines.

## Check a phase

```
check.bat P0
```

Runs the tests for P0 up to the given phase (earlier phases always re-run) and writes
`docs\gates\P<n>-auto.md`. Each phase also has a manual checklist in
`docs\gates\P<n>-manual.md`.

## Where your data lives

Outside this folder, so it can never be committed by accident:

- Data: `%LOCALAPPDATA%\OneProduction\` — `projects`, `profiles`, `backups`, `logs`, `cache`
- Settings: `%APPDATA%\OneProduction\settings.json` (optional)

Settings keys: `data_dir`, `port`, `open_browser`, `log_level`.
Environment variables override the file: `ONEPROD_DATA_DIR`, `ONEPROD_PORT`,
`ONEPROD_OPEN_BROWSER`, `ONEPROD_LOG_LEVEL`, `ONEPROD_SETTINGS_FILE`.

## Give it to a friend (P8)

```
.venv\Scripts\python tools\build_package.py          -> dist\OneProduction-<version>-win64.zip (~28 MB)
```

The zip holds One Production and its own Python 3.12 - nothing to install first, no admin
rights. The friend extracts it anywhere and double-clicks **One Production.cmd**; the setup
wizard does the rest (and downloads the voice pieces for their PC, with their OK).

## Publish an update

1. Add a section for the new version at the top of `docs\CHANGELOG.md` (it becomes the update notes)
   and set the version in `app\version.py`.
2. `.venv\Scripts\python tools\build_package.py`
3. `.venv\Scripts\python tools\publish_release.py --to "<release folder>"` - copies the zip and
   writes `latest.json` there.

Friends point System -> Updates -> "Update source" at that folder (a shared drive) or at its web
address once it's online; or build with `--update-source <folder or URL>` so new copies already
know it. Whoever can write to the release folder decides what friends install - keep it yours.
An update never touches the running version, the launcher or anyone's data, and the previous
version stays on disk for **Roll back**.

## Layout

```
app\      backend (FastAPI)
web\      browser UI
voice\    the voice engine that ships to friends (Chatterbox + read-check + mastering, tune.py)
samples\  the "Getting started" project
tools\    check.py (phase gate runner), ui_drive.py, build_package.py, publish_release.py
tests\    p0, p1, ... one folder per phase
docs\     PLAN.txt, CHANGELOG.md, phase-log.md, gates\
```
