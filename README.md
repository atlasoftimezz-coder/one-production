# One Production

A local production studio: one chat, any number of your own AI accounts, a usage page
with automatic handoff between accounts, and production tools (starting with voiceover).
Runs on your own PC; nothing is shared with anyone.

Full plan: `docs\PLAN.txt` · Progress: `docs\phase-log.md`

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

## Layout

```
app\      backend (FastAPI)
web\      browser UI
tools\    check.py (phase gate runner)
tests\    p0, p1, ... one folder per phase
docs\     PLAN.txt, phase-log.md, gates\
```
