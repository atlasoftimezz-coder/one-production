# Shared CLI workspace

Open a One Production project and choose **CLI** above the conversation. The
same terminal can run **PowerShell**, **Claude Code**, or **Codex**. Studio chat
remains the existing conversation interface with production tools.

## Plain CLI

Select **PowerShell**, choose a working folder, then **Start PowerShell**.
Type `claude` or `codex` to start an installed assistant, or run ordinary shell
commands. This is your normal shell profile and local CLI configuration. It
does not require a One Production account or inject saved OAuth/API credentials.
On other operating systems the shell option uses the system shell.

The plain shell uses the logins already configured on that PC. Studio MCP tools
are added by the direct assistant launchers described below; they are not
automatically installed into assistants started manually from the shell.

## Direct assistant launches

Choose **Claude Code** or **Codex** from **Launch**. The account list shows only
accounts that match that assistant. Add/sign in through **Manage accounts**;
adding or enabling an account updates the selector immediately. Credentials are
local to this PC and are not transferred by pulling the repository.

- **Claude Code:** uses the saved Claude browser login, OAuth token or Anthropic
  API account. The selected working folder supplies `CLAUDE.md` and project
  `.claude` settings. OAuth-token accounts can choose the PC's Claude setup
  folder; browser-login accounts retain their saved profile.
- **Codex:** uses the selected saved Codex login and its `CODEX_HOME`. Native
  `AGENTS.md`, skills, `config.toml`, MCP servers and permission settings load
  normally. Saved Codex logins retain their own setup folder to preserve account
  identity. To use this PC's existing default Codex setup instead, start
  PowerShell and type `codex`.

The direct Codex launcher requires a CLI with `--no-daemon` support, verified on
the installed Codex CLI 0.164.0. This keeps the app's process lifetime separate
from another Codex daemon. A missing CLI is shown in the launcher; configure its
executable in System or install it separately. The app does not download CLIs
or change their native model, sandbox or approval settings when launching.

## Studio tools and approvals

For direct Claude Code and Codex launches, **Add One Production tools and
connected sources** provides the private `oneproduction` stdio MCP server. It
offers the same project-bound tools: knowledge, learning, voiceovers, Drive,
source MCP discovery/calls, Discord scripts and X sources.

These tools use the One Production project's files folder, which can differ
from the CLI working folder. Native CLI file tools use the chosen workspace.
Existing native MCP settings remain available; the bridge is a session-only
addition and does not rewrite the user's configuration files.

Claude Code uses its studio-only native permission hook for GPU/paid jobs and
source operations. Codex uses an MCP form asking whether to **Allow once**.
Decline, cancel, unavailable prompting or a missing grant prevents execution.
Approval is bound to the exact operation/arguments and consumed once. Ordinary
native CLI tools continue using their own permission configuration.

The Codex bridge configuration forwards its private key by environment-variable
name. Tokens are not written into command-line arguments, setup files, transport
URLs or browser state. The stdio bridge is not a Codex MCP server: Codex is the
client connecting to One Production's tool server.

## Switching and sessions

End the active session before launching another program in the same project.
Changing the launch selector retains each assistant's account and setup draft.
Switching tabs/projects detaches the viewer while the process continues. Return
or press **Reconnect** to replay recent output and continue.

Claude Code resumes its saved conversation when the transcript exists, unless
**Start a new conversation** is checked. Codex starts a new conversation by
default; uncheck **New conversation** to open Codex's native resume picker, or
use its native `/resume` command. Session IDs are not shared between assistants.
PowerShell starts a new shell when launched again.

Existing `claude-code.json` preferences are read if no `terminal.json` exists.
New preferences are saved in `terminal.json` with separate provider entries;
the earlier file and native transcripts are retained. Earlier Claude API routes
remain aliases for compatibility.

The app's host/origin/token/PIN checks, bounded output replay and process-tree
cleanup apply to every program. Browser refresh retains the app's existing
authentication lock; reopen the authenticated launch link to reattach. Native
history and usage remain in the CLI rather than Studio chat's per-call ledger.

## References

- [Claude Code setup](CLAUDE_CODE.md)
- [Official Codex CLI options](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
- [Official Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)

Verification uses synthetic accounts, real ConPTY and a real browser, plus
local fake assistant CLIs and the official MCP client. PowerShell commands run
in the temporary test workspace with personal shell profiles disabled. No
inference requests use owner credentials.
