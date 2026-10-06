r"""DaVinci Resolve's own MCP server (ResolveMCP.exe, shipped with Resolve Studio 21.1) for the
studio's Claude agent.

Why this exists (owner, 2026-10-06: "I cant access davinci inside one production, I have turned on
davinci claude mcp and configured"): the studio agent is isolated on purpose - its own config
folder, no user settings, strict MCP config with only the `studio` server - so an MCP server
added to the owner's own Claude Code (user scope in ~/.claude.json) never reaches it. The owner
now switches it on here, in the Resolve panel, and the app hands exactly this one server to the
agent.

Tools seen on 2026-10-06 (tools/list, Resolve Studio 21.1):
    read only    get_resolve_status, get_whats_new, get_scripting_api, search_scripting_api,
                 get_scripting_docs, list_dctls, list_luts                      -> no card
    changes      run_script (sandboxed Python inside Resolve), launch_resolve,
                 update_dctl, generate_lut, delete_dctl, delete_lut             -> approval card
    blocked      run_script_unsafe (Python with full access to the PC)          -> never offered
Claude accounts only: Codex's app-server runs MCP tools without an approval step the app can
hold, so Codex keeps the propose-a-plan route.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVER = "davinci"
EXE = "ResolveMCP.exe"
READ_ONLY = ("get_resolve_status", "get_whats_new", "get_scripting_api", "search_scripting_api",
             "get_scripting_docs", "list_dctls", "list_luts")
BLOCKED = ("run_script_unsafe",)
LOOK = {f"mcp__{SERVER}__{t}" for t in READ_ONLY}
DENY = {f"mcp__{SERVER}__{t}" for t in BLOCKED}

# Replaces studio.RESOLVE_RULE in the agent's instructions while the switch is on.
INSTRUCTIONS = """- DaVinci Resolve: the owner connected Resolve's own MCP server (tools mcp__davinci__*), so you
  work in Resolve directly when the owner asks for it - including writing and running Resolve
  scripts with mcp__davinci__run_script. Video editing still defaults to ffmpeg.
  Start with mcp__davinci__get_resolve_status. Resolve 21.1 is newer than your training: look
  functions up with search_scripting_api / get_scripting_api / get_whats_new before a script, and
  use only functions they list. The app shows the owner every script and every LUT change for
  approval before it runs, so keep scripts short, single-purpose and readable, and say in one line
  what each will change. A script that only reads is fine to run when asked.
  Never delete or overwrite the owner's media, projects or timelines unless they asked for that
  exact thing; prefer a new timeline or project over changing an existing one, and save the
  project only when asked. Media comes from this project's folder (absolute paths inside it).
  After a script, read the result back (names, counts, settings) and report what changed.
  run_script_unsafe (full PC access) is not available. For a longer reviewable edit you may still
  propose a plan with resolve_propose_plan (the owner runs it from the Resolve panel)."""


def find_exe(override: str | None = None) -> str | None:
    """ResolveMCP.exe: the setting / env override, the standard install folder, or the path the
    owner configured for their own Claude Code (user-scope mcpServers in ~/.claude.json)."""
    if override:
        return override if Path(override).is_file() else None
    for base in (os.environ.get("PROGRAMFILES"), r"C:\Program Files"):
        if base:
            p = Path(base) / "Blackmagic Design" / "DaVinci Resolve" / EXE
            if p.is_file():
                return str(p)
    try:
        cfg = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in (cfg.get("mcpServers") or {}).values():
        cmd = str((entry or {}).get("command") or "")
        if cmd.lower().endswith(EXE.lower()) and Path(cmd).is_file():
            return cmd
    return None


def status(settings) -> dict:
    exe = find_exe(settings.resolve_mcp_cmd)
    return {"mcp": bool(settings.resolve_mcp), "mcp_exe": exe, "mcp_ready": bool(settings.resolve_mcp and exe)}


def for_agent(settings) -> dict:
    """What the Claude agent gets: {"servers": {...}, "disallowed": [...], "instructions": str}.
    Empty when the switch is off or Resolve's MCP isn't installed."""
    exe = find_exe(settings.resolve_mcp_cmd) if settings.resolve_mcp else None
    if not exe:
        return {"servers": {}, "disallowed": [], "instructions": ""}
    return {"servers": {SERVER: {"type": "stdio", "command": exe, "args": []}},
            "disallowed": sorted(DENY), "instructions": INSTRUCTIONS}
