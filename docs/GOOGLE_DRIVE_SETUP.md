# Google Drive connection

Open **MCP** in One Production's top bar, then **Connect Google Drive**.
This uses the app's existing Google Drive API connection and the same account
as the Storage page. It does not require Google's preview MCP service.

## First connection

1. Expand **Google Cloud setup**. Enable the Google Drive API in your Google
   Cloud project and configure its OAuth consent screen. Add your Google email
   as a test user if the consent screen is in Testing.
2. Create an OAuth client with application type **Desktop app** and download
   its JSON file. Choose that file in One Production, or paste its client ID
   and secret. Web app credentials and service account keys are not accepted.
3. Press **Connect Google Drive** and finish Google's consent screen in the new
   tab. If your browser blocks that tab, use **Open Google sign-in**. Keep the
   One Production connection window open until your email appears as connected.
4. Use **Test connection** to check access by listing your Drive root folder.
   This test does not download or upload files.

The JSON file only fills the form until you press Save or Connect. Saved
client secrets and refresh tokens use Windows Credential Manager. Exporting
finished episodes is a separate, optional setting.

## Import reference files

Choose a project, then **MCP > Google Drive > Import files**. Open a folder,
select up to 50 files, and press **Import selected**. Copies appear in the
project's Files panel under `imports/`; the originals stay in Drive.

Ordinary videos, text files, SRT/VTT transcripts and documents can be imported.
Native Google Docs/Sheets must currently be downloaded as ordinary files first.
Importing does not automatically start competitor analysis or index the folder.
The Competitors workflow still starts from YouTube links.

**Manage connection** opens the setup again. **Disconnect** removes the saved
Drive login from One Production and turns off automatic exports. If a test
reports revoked or expired access, connect again and retry.

Neon, n8n and Nexlev now have their own setup cards in MCP. Shared source tools
give Claude and Codex access to configured sources, including Drive. See
[Sources and MCP](SOURCES_AND_MCP.md) for Discord and Agent Reach setup.
