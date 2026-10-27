# Sources and MCP

Updated 28 October 2026. Open **MCP** in the top bar. The existing Google Drive
connection is reused. Setting up another source does not change Drive settings.

## n8n, Nexlev and Neon

Each card offers **Configure connection**, **Save**, **Test connection** and
**Disconnect**. Saved settings are not proof of access: Test lists the remote
tools. The app keeps API tokens and browser OAuth tokens in Windows Credential
Manager. Leave a token field empty to retain its saved value for the same URL.

- **n8n:** copy the MCP URL and MCP access token from n8n's Instance-level MCP
  settings. This token is distinct from an n8n REST API key. Browser OAuth is
  also available. Expose the workflows you want to use through n8n's MCP settings.
- **Nexlev:** its documented Claude-compatible MCP endpoint is prefilled. Choose
  browser sign-in, save, then **Sign in** and finish in your browser. Quotas and
  tool access depend on your Nexlev account. Test does not generate thumbnails
  or start research jobs.
- **Neon:** its HTTP MCP endpoint is prefilled. Use a Neon API key or browser
  sign-in. Connecting does not move local app data to Neon or change a schema.

Custom HTTP, SSE and stdio servers still use **Add a server**. Custom servers
remain available directly to Claude; shared source tools also make them
available to Codex. The three source cards use the shared client for both
agents, including browser OAuth, rather than storing refresh tokens in a
coding-agent profile.

## Discord scripts through n8n

The existing **Rogue MCU — Read Final Discord Scripts** workflow
(`HGTU978gZLJns4Nw`) was adapted on the owner's request. Its Discord bot credential,
fixed guild/channel and original manual trigger are retained. A private chat
trigger allows manual MCP execution; it is not a public chat endpoint. Reads
are bounded to the latest 100 messages and return messages in chronological
order with embeds, attachment references and Discord source links. It sends,
edits and deletes no Discord messages. The workflow remains unpublished.

[Open the existing Discord workflow](https://n8n.129-159-236-100.nip.io/workflow/HGTU978gZLJns4Nw).

1. Connect n8n first.
2. Expand **Discord > Configure script reader**, verify the MCP server name and
   workflow ID, enable the source and save.
3. **Test workflow** checks metadata and message-read operations; it does not
   execute the workflow or read channel messages.
4. **Read scripts** deliberately runs the reader in manual mode. On success,
   **Import into project** saves the result as JSON under `sources/discord/`.

The original workflow snapshot and the local normalization check are retained
under `build/`. The workflow's new draft was read back after modification and
had no reported validation warnings. No live Discord execution was used for
the local UI/API tests.

## Agent Reach and X

The integration targets Agent Reach 1.6.0, pinned to GitHub commit
`14c213405669414492bda0f8d52cc6531d062c45`. Use the official GitHub source, not
the unrelated package with the same name on PyPI.

The current source checkout has a separate core runtime in
`build/agent-reach-core/`; the app also detects installations on PATH, its own
modules folder or an executable path entered in the card. It does not change
the application's Python or the voice environments.

1. Save the X accounts you follow, as handles or profile URLs, and enable the
   source. This is an explicit account list, not an imported Following timeline.
   Agent Reach's current X channel exposes search, profiles, user tweets and
   threads, without a following-list action.
2. If needed, **Install core** installs the pinned program into a separate app
   module runtime using uv. **Set up X** installs its X dependencies and may
   download browser tools. These are deliberate setup actions.
3. **Sign in to X** opens Agent Reach's login browser. Sign in yourself; do not
   paste passwords or cookies into chat. **Check status** runs its local doctor,
   not a live X request or proof that every account can be read.
4. Choose a project and **Collect posts into project**. Each batch reads three
   accounts and up to five posts per account, saving JSON under `sources/x/`.
   Collect again for the next batch. Errors stop a batch; the adapter does not
   switch accounts/backends or retry through verification or rate limits.

X support is experimental upstream and can fail as X changes. Imports preserve
source content and links; they do not train a model or automatically add records
to the local knowledge index. Agents can read the files from their project.

## Agent access

Both agents receive `source_status`, `source_tools`, `source_call`,
`drive_browse`, `drive_import`, `discord_read`, `discord_result`, `x_read` and
`x_digest`. Remote calls follow the app's approval policy; generic source calls
must be approved individually because the remote operation may write data.
Source documents and posts are reference data, never instructions or permission.
Start a fresh agent conversation after connecting a source.

Official references: [n8n MCP](https://docs.n8n.io/connect/connect-to-n8n-mcp-server.md),
[Nexlev setup](https://docs.nexlev.io/docs/mcp/quickstart),
[Neon MCP](https://neon.com/docs/ai/connect-mcp-clients-to-neon),
[Agent Reach](https://github.com/Panniantong/Agent-Reach).
