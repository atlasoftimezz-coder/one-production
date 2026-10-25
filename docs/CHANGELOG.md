# Changelog

What changed in each version, in plain words. "Check for updates" shows the newest section.

## Unreleased - P10 production engine, knowledge, learning and Resolve; P11 stock footage; P12 safety and learning
- **Automatic clone refinement**: generate, compare and adjust supported voice controls within an approved
  limit (five takes by default, at most ten). Stop for a calibrated identity target, stalled progress,
  unavailable scoring or the cap. Retain the best eligible take/settings for production; exclude known
  Chatterbox misreads. Identity scores remain evidence requiring listening review, not perfect-match claims.
- **Clone and check voices**: save a clean uploaded voice, then let Claude/Codex clone it with Chatterbox
  or the requested ElevenLabs/MiniMax provider and generate bounded comparison takes. Voice cards provide
  reference/test playback and local trained speaker-encoder cosine evidence, with explicit listening-review
  status. Existing clones are reused; provider verification is honored; missing scoring stops extra takes.
  Fixed the older Chatterbox tuner to use the loaded trained encoder instead of a fresh untrained instance.
- **Voiceovers guides and passage repair**: Page guide explains each page and its controls. A next-step hint
  shows missing setup, and Continue in Claude / Codex hands off the reviewed script/settings without sending
  chat automatically. Speaker labels route voices without being spoken, including Miles-first scripts.
  History and both assistants can regenerate selected passages, reuse other source audio and rebuild a new
  mastered take while keeping the original. Chatterbox uses a fresh repair seed with bounded transcript
  retries. An audio-listening performance evaluator is still required before autonomous production approval.
  Start with `VOICEOVERS_USER_GUIDE.md`.
- **Dedicated Voiceovers workspace**: a full script editor, independent Peter/Miles voice/provider settings,
  Settings/History tabs with text/settings restoration and a bottom player. Prepare permitted clean voices
  with Chatterbox, ElevenLabs or MiniMax; retain Miles's processed reference separately. Generate the whole
  conversation in order, apply the saved Miles distortion reference, normalize silence/pauses, master and
  measure WAV/MP3 delivery at -14 LUFS with a -1 dBTP ceiling, and export `#Number VO Final` plus preprod and
  optional aligned speaker stems. Claude and Codex share the backend tools. Reference analysis uses Scribe
  after approval. See `VOICEOVERS.md` for setup, the portable distortion approximation and validation limits.
- **Competitors tab**: paste competitor videos or channels. One Production downloads a small copy
  of each video (for measuring only, deleted afterwards), measures every cut, the pictures and the
  sound, and the AI looks at the frames and writes the editing style down: pacing, kinds of shots,
  text, colour, motion and sound. "Use as guidance" makes it something every AI in the project
  follows.
- **Make a draft (no stops)**: the episode dialog now makes the whole video in one go for your
  first review.
  - The AI designs a new look and its own animation helpers for this script in the competitor
    style you pick, with shots paced like that channel.
  - It builds every shot in Resolve's Fusion page. Shot scripts for the episode's own shots run
    without a card; anything else still asks.
  - It can add Pexels footage under the graphics and Epidemic Sound effects on the cuts.
  - Resolve renders the draft into your project, and the app packages it.
  - Failed checks become notes in a review report instead of "needs you". The draft stops only
    when going on is impossible.
- **Sound effects**: add your Epidemic Sound API key under Set up / modules. Every effect is
  downloaded once and reused. Epidemic's free API plan is for prototyping only; use a plan that
  covers YouTube.
- **Claude OAuth token**: a separate "Claude OAuth token" tile under Add an account (Claude login
  still opens the browser sign-in, as before). Run `claude setup-token` while signed in to your own
  Claude subscription and paste the `sk-ant-oat…` line there, or press "Use a token" on an
  existing Claude login. It is kept in Windows Credential Manager. A token pasted into the API key box (or
  an API key into the token box) is now explained instead of failing with "invalid x-api-key".
  Limit meters for a token login fill in from replies, because Claude Code's `/usage` doesn't
  report them for tokens.
- **Learn from a published video**: in Knowledge and learning, pick the YouTube Studio
  audience-retention CSV of an episode. One Production finds where viewers left (after the intro),
  names the chapter, the Fusion shot and the words at that moment, and suggests at most 3 lessons.
  Nothing changes until you press "Use as my guidance" on one.
- **Risky actions always ask**: deleting, installing, sending files out, writing outside the
  project and an outside tool's write / run actions get a card every time, with no "Allow this
  session". Session allowances show as chips above the message box (✕ takes one back) and are
  gone after a restart.
- **Outside MCP servers** (System): connect e.g. Desktop Commander to Claude chats; every call of
  its tools asks.
- **Sign in with Google** (optional, Profile): your own Google "Desktop app" client, only the
  email addresses you list get in. Can be required every time the app starts.
- **Terms check**: moving work between two logins of the same service stays off, because Anthropic's
  and OpenAI's terms don't allow it (`docs/terms-check.md`, read 2026-10-06).
- **Episode visuals are made in DaVinci Resolve's Fusion page** (1080p, 60 fps). Drawn SVG shots
  are gone.
  - For every script the AI designs a new look to make that script easy to follow, then builds
    each shot by script.
  - One Production creates the Resolve project (folder "One Production episodes") with one
    Fusion shot per spoken idea.
  - You approve each shot script on a card, review the stills, render the timeline yourself in
    Resolve, and pick the file. One Production then checks it and packages the episode.
  - Needs Resolve Studio and both switches in Resolve plans.
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
