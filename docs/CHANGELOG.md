# Changelog

What changed in each version, in plain words. "Check for updates" shows the newest section.

## Unreleased - P10 production engine, knowledge, learning and Resolve; P11 stock footage
- **DaVinci Resolve's own MCP in Claude chats**: in **Resolve plans**, tick "Let the AI use
  DaVinci Resolve's own MCP". It needs Resolve Studio 21.1 or newer, which ships
  `ResolveMCP.exe`.
  - The AI can check Resolve and look up its scripting API on its own.
  - Every script and every LUT change appears on an approval card with the full code before it
    runs. Scripts with full access to your PC are never allowed.
  - Before, an MCP set up in your own Claude Code never reached One Production, because its AI
    runs separately from your personal setup.
  - Works with Claude accounts only.
- Resolve plans no longer fail with "timelineFrameRate reads back as 30.0" (Resolve 21.1
  reports the frame rate as a number).
- **Stock footage from Pexels**: add your free Pexels key under System → Set up / modules →
  Stock footage. The key is kept in Windows Credential Manager. The AI can then search and fetch
  free Pexels clips and photos into the project's `stock/` folder, and each one is credited in
  `stock/CREDITS.md`. In **Make an episode**, the animator mixes stock clips with drawings for
  real-world subjects. Storyblocks is not built yet.
- **Make an episode** (Files sidebar): from a finished script to an upload-ready bundle. The
  voiceover is made (or reused), a worker draws every shot as a drawing timed to the spoken words,
  the video is put together on your processor, and a worker writes titles, description, tags and a
  thumbnail. The master reviews the creative parts, and every stage must pass measured checks
  before the next one starts: voice (read-check, pitch vs. the reference, -14 LUFS, true peak,
  subtitles), video (length = voiceover, sync, no black frames) and package (YouTube's limits).
  The bundle lands in `episode/<name>/`; every check's report in `gates/`.
- Passages from a manual newer than your installed software are shown with a warning instead
  of being hidden.
- **Knowledge and learning** (Files sidebar): import PDF, Word, Markdown or text manuals; workers
  search them and cite the PDF page. Directions you give are remembered for the project, channel
  or all projects; corrections replace old ones and "Forget" stops them being used.
- **Resolve plans** (Files sidebar): workers can propose a DaVinci Resolve edit (import media, new
  timeline, append clips, markers, frame rate / resolution). You review it and press Run; it
  always builds a NEW project in a "One Production (disposable)" folder and never opens or changes
  your existing projects. Plans are checked against your installed Resolve version first. A run
  that Resolve confirms becomes a verified method the workers reuse. Needs Resolve Studio; off by
  default. No rendering.

## 0.10.0 - 2026-10-05
- Script Studio (the page button below the message box): paste competitor links in any format
  (or pick a .txt/.csv/.docx/.xlsx list), and One Production reads their YouTube captions, measures
  how they talk (words per minute, sentence length, hooks) and writes a style guide. A writer
  (Sonnet) then writes the script with your FacelessOS system, and Opus reviews it with a
  nine-point score card - rewrite and review again until every score is 8+, or it keeps the best
  version after 5 rounds / no progress.
- FacelessOS (v4, May 2026) comes with One Production and is used by default; set a folder in Script
  settings to use a newer version instead.
- Files in sub-folders (scripts/, competitors/) now show in Files and can be downloaded and voiced.

## 0.9.0 - 2026-10-05
- Team mode (the people button next to the account): give a goal; a master (Opus 5.5) splits it
  into parts, up to 3 workers (Sonnet 5.5) do them at the same time, and the master reviews each
  part and writes a final review. Two workers never write the same file; voiceovers wait their turn.
- A worker that hits a usage limit saves a handoff and carries on on another account (never from
  one Claude login to another); with no account left the team pauses until one resets.
- Write the parts yourself (Parts… in team mode): each part goes to one worker and the master only
  reviews. You choose how many workers run at once (3 by default; above 3 it warns about limits).
- Reviews stop after 2 rounds or their token budget and ask you instead of looping.
- Pick the model and the effort (how hard it thinks) for each account, right below the message box:
  Claude (Opus, Sonnet, Haiku, Fable), API keys, and Codex (its own list).
- Spend caps per account on the Usage page (tokens per task, tokens or dollars per day), plus
  usage by role (chat, agent, team master, team worker).

## 0.8.0 - 2026-10-04
- One Production now comes as one zip with its own Python: extract it anywhere and double-click
  "One Production". No admin rights, nothing to install first.
- Updates from inside the app (System, Updates). The previous version stays on disk, so going back
  is one click. Your projects and settings are never touched by an update.
- "Export diagnostics": one zip with what's needed to find a problem - never your chats, files or keys.
- A sample project to try a voiceover straight away, and a guide inside the app (the ? button).

## 0.7.0 - 2026-10-04
- Setup wizard for a new PC: data folder, PC scan, modules, downloads you approve one by one,
  accounts, your own voice, and a test run.
- The voice engine ships with One Production; "Find best settings" tunes Chatterbox to a voice.

## 0.6.0 - 2026-10-04
- Daily backups with one-click restore, a read-only scan of the PC, and an optional PIN.

## 0.5.0 - 2026-10-04
- Agent mode: the chat works like Claude Code / Codex - it reads and edits your project's files
  and makes voiceovers itself; commands and anything outside the project ask first.

## 0.4.0 - 2026-10-04
- Voiceovers from the chat: preview and clone voices, read-check every section, subtitles,
  mastered to -14 LUFS / -1 dBTP.

## 0.3.0 - 2026-10-03
- Usage page with live limits, automatic handoff at 85% or a limit, switching accounts.

## 0.2.0 - 2026-10-03
- Your own Claude, Codex and API-key accounts, each with its own email.

## 0.1.0 - 2026-10-03
- Chat, projects and files, running only on this PC.
