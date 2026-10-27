# Claude Code inside One Production

Updated 28 October 2026. Open a project, select **CLI** above the conversation,
then choose **Claude Code** under **Launch**. The shared terminal also supports
PowerShell and Codex; see [Shared CLI workspace](CLI_WORKSPACE.md).
This runs the official interactive Claude Code executable in
a native terminal inside One Production. The CLI handles its own tools,
permissions, questions, slash commands, plans, skills, hooks, plugins, memory,
MCP configuration, compaction and session transcripts. Availability follows
the installed CLI version, selected account and its subscription.

## Start with Adiitya's saved login

1. Choose Adiitya in **Claude account**. The app retrieves the existing OAuth
   token from Windows Credential Manager; it is not sent to the browser.
2. Set **Working folder** to the repository or workspace you want Claude to
   work in. Its `CLAUDE.md`, `.claude/CLAUDE.md`, project settings, commands,
   skills and `.mcp.json` load through the CLI's normal discovery rules.
   The default is this One Production project's uploaded-files folder.
3. Choose **Claude setup folder**. **Use saved account setup** selects that
   account's isolated profile. **Use this PC's Claude setup** selects the
   normal local Claude configuration folder, so its existing user settings,
   skills, plugins and MCP configuration are available. A custom existing
   folder also works. An OAuth-token account keeps its selected saved login
   when using another setup folder. Browser-login accounts must retain their
   own profile to avoid accidentally switching identities.
4. Leave **Add One Production tools and connected sources** checked to expose
   the studio through the `oneproduction` MCP server. Click **Start Claude Code**,
   answer the CLI's normal workspace-trust/onboarding prompts, and type directly
   in the terminal. Use `/help`, `/model`, `/mcp`, `/memory`, `/permissions`,
   `/resume` and other commands supported by your installed Claude Code.

Native Claude Code uses its own model and effort settings. Change those in the
CLI as usual. The model controls in **Studio chat** apply to Studio chat.
The earlier SDK-based Studio chat remains available in its own tab; its
conversation and agent restrictions are separate from the native CLI session.

## If the account dropdown is empty

The CLI uses accounts registered under **Accounts** in the same running app.
An empty list means that instance has no supported Claude account registered.
Click **Add Claude account**, choose **Claude OAuth token**, and save the token
in the app's password field, or use browser sign-in or an Anthropic API key.
The account becomes available to every project; it is not connected separately
per project. A Codex account cannot authenticate the Claude Code CLI.

Turned-off Claude accounts remain listed as **(turned off)**. Use **Manage
accounts → Turn on** to enable one. Adding, removing, or enabling an account
updates the CLI selector immediately; **Refresh accounts** also rereads it.
Refreshing accounts preserves draft folder choices and any running CLI session.
If a previously selected account was removed, an existing enabled account is
selected instead of leaving an empty selection.

The empty-state message includes the app's current data folder to help identify
another installation or configuration. An OAuth token saved in a different
application is not automatically a One Production account.

## Studio and source tools

The `oneproduction` MCP bridge exposes the same project-bound studio tools used
by One Production's agents: voiceover tools, knowledge, learning, source
discovery and calls, Drive browsing/import, Discord reading and Agent Reach X
tools. Saved source credentials remain in the app's existing stores. Connect
your remaining services from **MCP** when ready, then use `source_status` and
`source_tools` in the CLI to discover what is available.

Studio tools always use the selected One Production project's files, even if
the CLI's working folder is a different repository. The CLI's own native tools
operate in the chosen working folder. The startup instructions state both
contexts. Your existing native MCP configuration also loads normally; the app
does not pass `--strict-mcp-config` or disable native commands or settings.

Ordinary CLI actions use Claude Code's native permission settings and prompts.
Studio GPU/paid jobs and source requests additionally use a studio-only
`PreToolUse` hook that requests native approval on each invocation. The bridge
requires the matching one-use hook request, so disabling hooks blocks those
operations. Existing project/user hooks remain loaded. The app does not select
`bypassPermissions` or add a permission-bypass flag.

## Sessions and files

- Switching projects or tabs detaches the viewer without stopping Claude.
  Return to the project or press **Reconnect** to replay the recent terminal
  output and continue. Output replay is bounded to 4 MiB in memory.
- Browser refresh keeps the app's existing authentication lock. Reopen the
  authenticated launch link for the same running app and project to reattach.
  The app session token is not saved to browser storage for refresh convenience.
- **Ctrl+C** interrupts the CLI normally; **Esc** and **Shift+Tab** buttons send
  the corresponding native keys. Paste with **Ctrl+Shift+V**, copy a selection
  with **Ctrl+Shift+C**, or use **Copy output** for the terminal buffer.
- **End session** terminates only that session's process tree and refreshes the
  project files. Other Claude terminals are untouched. Closing One Production
  also closes its embedded processes.
- Starting again resumes the saved conversation if its native transcript
  exists. **Start a new conversation** creates a new ID and retains old
  transcripts. Native `/clear`, `/resume` and `/fork` remain native commands;
  after explicitly choosing a different conversation inside the CLI, use
  `/resume` again if you want it instead of the app's original launch ID.
- Native transcripts live in the chosen Claude setup folder. They are not
  copied into Studio chat history or One Production's per-call usage ledger.
  Use the CLI's own `/cost` and `/usage` where supported. Subscription usage
  refresh in Accounts continues to use the existing account mechanism.

Only one agent or embedded CLI can work in a One Production project at a time.
End the CLI session before starting Studio chat or a team in that project.

## Implementation and verification

Windows uses ConPTY through pinned `pywinpty` 3.1.0. The browser uses locally
vendored xterm 6.0.0 and its fit addon; no terminal scripts load from a CDN.
The WebSocket checks the app's host, exact origin, unlock state and startup
token before it accepts terminal input. The MCP bridge has a separate secret
scoped to the live session. Neither secret appears in the transport URL.

Automated checks use synthetic credentials, a test CLI, real Windows ConPTY,
a real browser/WebSocket and the official MCP stdio client. They exercise
input, Unicode, resizing, reconnect, authenticated reopen, real file creation,
studio tool discovery/calls, native permission-hook execution and cleanup.
They do not send inference prompts through the owner's accounts.
The installed official CLI's launch flags are also checked with `--help` using
an empty temporary configuration and no account credentials.

Native configuration behavior follows the official
[Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) and
[settings documentation](https://code.claude.com/docs/en/settings).
