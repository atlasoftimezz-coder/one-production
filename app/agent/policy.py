"""What the agent may do without asking (user's decision, 2026-10-04: "free in project, ask outside").

    allowed   read / search / create / edit files inside the project folder,
              studio tools that only look (voices, job status, word counts),
              bookkeeping tools (todo list, sub-agents, plan)
    ask       every shell command, anything touching a path outside the project, web access,
              unknown tools - shown as an Approve card in the chat
    gate      GPU / paid studio tools go through the existing heavy-job gate (R12) instead

One function decides for both Claude and Codex, so the two agents can't drift apart.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import fusion_kit, resolve_mcp

READS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL = {"Bash", "PowerShell", "BashOutput", "KillShell", "KillBash", "REPL"}
WEB = {"WebFetch", "WebSearch"}
HARMLESS = {"TodoWrite", "Task", "Agent", "ExitPlanMode", "EnterPlanMode", "AskUserQuestion", "ToolSearch"}
STUDIO_LOOK = {"list_voices", "script_stats", "voiceover_status", "knowledge_sources", "knowledge_search", "knowledge_read", "learning_recall",
               "quality_gate"}
STUDIO_MEMORY = {"remember_direction", "propose_lesson"}
STUDIO_PLAN = {"resolve_status", "resolve_propose_plan", "edit_status"}
STUDIO_DRAW = {"build_animation", "package_episode"}   # light CPU, writes only inside the project   # look / save a plan; only the owner executes
STUDIO_GATED = {"make_voiceover", "render_edit"}
# Pexels with the owner's own free key (owner, 2026-10-06): one fixed site, files only into the
# project's stock/ folder, the key never shown to the agent - so no card, unlike open web access.
STUDIO_STOCK = {"stock_search", "stock_fetch"}
# The fixed runner builds a NEW Resolve project for an episode / exports stills; only offered while the
# owner has Resolve control and the Resolve MCP switched on.
STUDIO_FUSION = {"fusion_setup", "fusion_stills"}
HEAVY = {"make_voiceover": "voiceover", "render_edit": "video_render"}   # tools.TOOLS key per gated tool
PATH_KEYS = ("file_path", "path", "notebook_path", "directory", "cwd")


def studio_name(tool: str) -> str | None:
    """'mcp__studio__make_voiceover' (Claude) or 'make_voiceover' (Codex dynamic tool) -> name."""
    if tool.startswith("mcp__studio__"):
        return tool[len("mcp__studio__"):]
    return tool if tool in STUDIO_LOOK | STUDIO_GATED | STUDIO_MEMORY | STUDIO_PLAN | STUDIO_DRAW | STUDIO_STOCK | STUDIO_FUSION else None


def inside(path: str | None, root: Path) -> bool:
    if not path:
        return True                      # no path = the working folder, which is the project
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        real = Path(os.path.realpath(p))
        base = Path(os.path.realpath(root))
    except OSError:
        return False
    return real == base or base in real.parents


def paths_of(tool_input: dict) -> list[str]:
    out = [tool_input[k] for k in PATH_KEYS if isinstance(tool_input.get(k), str)]
    for e in tool_input.get("edits") or []:          # MultiEdit-style inputs
        if isinstance(e, dict) and isinstance(e.get("file_path"), str):
            out.append(e["file_path"])
    return out


def decide(tool: str, tool_input: dict, root: Path) -> tuple[str, str]:
    """('allow' | 'ask' | 'gate' | 'deny', reason shown on the card)."""
    if tool in resolve_mcp.DENY:
        return "deny", "runs a script with full access to your PC - One Production never allows it"
    if tool in resolve_mcp.LOOK:
        return "allow", "reads from DaVinci Resolve (status, documentation, lists); changes nothing"
    if tool.startswith(f"mcp__{resolve_mcp.SERVER}__"):
        return "ask", "runs code in DaVinci Resolve or changes its LUT files - read it before you say yes"
    s = studio_name(tool)
    if s in STUDIO_LOOK:
        return "allow", "looks only"
    if s in STUDIO_MEMORY:
        return "allow", "records an explicit direction or an unverified candidate"
    if s in STUDIO_PLAN:
        return "allow", "reads Resolve's version or saves a plan for your review; nothing runs in Resolve"
    if s in STUDIO_DRAW:
        return "allow", "works only inside the project (a few seconds of processor)"
    if s in STUDIO_FUSION:
        return "allow", "builds a new Resolve project for the episode or exports review stills (fixed One Production helper)"
    if s in STUDIO_STOCK:
        return "allow", "free Pexels stock with your key; files go only into the project's stock folder"
    if s in STUDIO_GATED:
        return "gate", "uses the GPU" if s == "make_voiceover" else "uses the processor for minutes"
    if tool in HARMLESS:
        return "allow", "bookkeeping"
    if tool in READS | WRITES:
        outside = [p for p in paths_of(tool_input) if not inside(p, root)]
        if outside:
            return "ask", "outside the project folder: " + outside[0]
        return "allow", "inside the project"
    if tool in SHELL:
        return "ask", "runs a command on your PC"
    if tool in WEB:
        return "ask", "goes on the internet"
    return "ask", "a tool One Production doesn't know"


def describe(tool: str, tool_input: dict, root: Path) -> tuple[str, str]:
    """(short title, detail) for the step list and approval cards."""
    def rel(p: str | None) -> str:
        if not p:
            return ""
        try:
            return str(Path(p).resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            return p
    if tool.startswith(f"mcp__{resolve_mcp.SERVER}__"):
        name = tool[len(f"mcp__{resolve_mcp.SERVER}__"):]
        texts = [v for v in tool_input.values() if isinstance(v, str)]
        if name.startswith("run_script"):        # the owner reads the whole script before saying yes
            code = max(texts, key=len, default="")
            if fusion_kit.MARKER.search(code):
                code = fusion_kit.MARKER.sub(f"from op_kit import *   # + One Production Fusion kit v{fusion_kit.KIT_VERSION} "
                                             "(fixed, reviewed code, added on approval)", code, count=1)
            return "Run a script in DaVinci Resolve", code[:6000]
        return "Resolve: " + name.replace("_", " "), ", ".join(f"{k}={str(v)[:80]}" for k, v in tool_input.items())[:300]
    s = studio_name(tool)
    if s == "render_edit":
        return "Render video", str(tool_input.get("edit", ""))
    if s == "make_voiceover":
        pace = tool_input.get("pace_mode") or "natural"
        wpm = f" at {tool_input.get('target_wpm')} wpm" if pace == "wpm" else ""
        return "Make voiceover", f"{tool_input.get('file')} with {tool_input.get('voice')}{wpm}"
    if s:
        return s.replace("_", " ").capitalize(), ", ".join(f"{k}={v}" for k, v in tool_input.items())[:200]
    if tool in READS | WRITES:
        verb = {"Read": "Read", "Glob": "Find files", "Grep": "Search", "LS": "List", "NotebookRead": "Read",
                "Write": "Write", "Edit": "Edit", "MultiEdit": "Edit", "NotebookEdit": "Edit"}[tool]
        target = rel(next(iter(paths_of(tool_input)), "")) or tool_input.get("pattern", "")
        return verb, target
    if tool in SHELL:
        return "Run command", unwrap_shell(str(tool_input.get("command") or tool_input.get("code") or ""))[:400]
    if tool == "WebFetch":
        return "Open web page", str(tool_input.get("url", ""))
    if tool == "WebSearch":
        return "Search the web", str(tool_input.get("query", ""))
    if tool == "TodoWrite":
        todos = tool_input.get("todos") or []
        return "Update plan", f"{len(todos)} steps"
    return tool, ", ".join(f"{k}={str(v)[:60]}" for k, v in list(tool_input.items())[:3])


_WRAPPER = re.compile(r'^\s*"?[^"]*?(powershell|pwsh|cmd)(\.exe)?"?\s+(-NoProfile\s+)?(-Command|/c|/C)\s+(.*)$', re.I | re.S)


# Plan R6: deletes, installs and network uploads are asked EVERY time ("default deny"), and so are file
# writes outside the project and write/exec tools of outside MCP servers - "Allow this session"
# never covers them. These patterns only ever add caution: a miss still asks (the tool asks anyway).
_DELETE = re.compile(r"(?i)(^|[\s;&|(])(rm|rmdir|rd|del|erase|Remove-Item|ri|Clear-Content|Clear-RecycleBin|format|diskpart|shred)"
                     r"(?=\s|$)|\bgit\s+clean\b|\bgit\s+reset\s+--hard\b")
_INSTALL = re.compile(r"(?i)\b(pip3?|python\s+-m\s+pip|uv\s+pip|uv\s+tool)\s+install\b|\bnpm\s+(i|install|add|ci)\b|\b(pnpm|yarn)\s+(add|install)\b"
                      r"|\b(winget|choco|scoop|apt|apt-get|brew)\s+install\b|\bmsiexec\b|\bInstall-(Module|Package|Script)\b"
                      r"|\b(cargo|go)\s+install\b|\bsetup\.exe\b")
_UPLOAD = re.compile(r"(?i)\bcurl\b[^|;&]*(\s-T\s|--upload-file|\s-F\s|--form\b|\s(-d|--data|--data-binary)\s+@)"
                     r"|\bInvoke-(WebRequest|RestMethod)\b[^|;&]*-Method\s+(Post|Put|Patch)|\b(scp|sftp|ftp|rsync)\b"
                     r"|\bgit\s+push\b|\baws\s+s3\s+(cp|sync|mv)\b|\bgsutil\s+(cp|rsync)\b|\baz\s+storage\b")
_MCP_RISKY = re.compile(r"(?i)(write|edit|delete|remove|move|rename|create|exec|run|start|kill|terminate|install|upload|send|set_)")


def risky(tool: str, tool_input: dict, root: Path) -> str | None:
    """Why this action must be asked every time (no session allowance), or None."""
    if tool in SHELL:
        cmd = unwrap_shell(str(tool_input.get("command") or tool_input.get("code") or ""))
        if _DELETE.search(cmd):
            return "deletes files"
        if _INSTALL.search(cmd):
            return "installs software"
        if _UPLOAD.search(cmd):
            return "sends files over the network"
        return None
    if tool in WRITES:
        return "writes outside the project folder" if any(not inside(p, root) for p in paths_of(tool_input)) else None
    if tool.startswith("mcp__") and not tool.startswith("mcp__studio__"):
        server, _, name = tool[5:].partition("__")
        if server == resolve_mcp.SERVER:           # Resolve's run_script is sandboxed (no files); only its deletes
            return "deletes files in DaVinci Resolve's LUT folder" if name.startswith("delete") else None
        if _MCP_RISKY.search(name):
            return "changes files or runs programs through an outside tool"
    return None


def unwrap_shell(command: str) -> str:
    """'"...powershell.exe" -Command "Get-ChildItem"' -> 'Get-ChildItem', for display only."""
    m = _WRAPPER.match(command or "")
    if not m:
        return command
    inner = m.group(5).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] == '"':
        inner = inner[1:-1]
    return inner
