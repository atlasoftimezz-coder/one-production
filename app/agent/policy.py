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

READS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL = {"Bash", "PowerShell", "BashOutput", "KillShell", "KillBash", "REPL"}
WEB = {"WebFetch", "WebSearch"}
HARMLESS = {"TodoWrite", "Task", "Agent", "ExitPlanMode", "EnterPlanMode", "AskUserQuestion", "ToolSearch"}
STUDIO_LOOK = {"list_voices", "script_stats", "voiceover_status", "knowledge_sources", "knowledge_search", "knowledge_read", "learning_recall"}
STUDIO_MEMORY = {"remember_direction", "propose_lesson"}
STUDIO_PLAN = {"resolve_status", "resolve_propose_plan"}   # look / save a plan; only the owner executes
STUDIO_GATED = {"make_voiceover"}
PATH_KEYS = ("file_path", "path", "notebook_path", "directory", "cwd")


def studio_name(tool: str) -> str | None:
    """'mcp__studio__make_voiceover' (Claude) or 'make_voiceover' (Codex dynamic tool) -> name."""
    if tool.startswith("mcp__studio__"):
        return tool[len("mcp__studio__"):]
    return tool if tool in STUDIO_LOOK | STUDIO_GATED | STUDIO_MEMORY | STUDIO_PLAN else None


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
    """('allow' | 'ask' | 'gate', reason shown on the card)."""
    s = studio_name(tool)
    if s in STUDIO_LOOK:
        return "allow", "looks only"
    if s in STUDIO_MEMORY:
        return "allow", "records an explicit direction or an unverified candidate"
    if s in STUDIO_PLAN:
        return "allow", "reads Resolve's version or saves a plan for your review; nothing runs in Resolve"
    if s in STUDIO_GATED:
        return "gate", "uses the GPU"
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
    s = studio_name(tool)
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


def unwrap_shell(command: str) -> str:
    """'"...powershell.exe" -Command "Get-ChildItem"' -> 'Get-ChildItem', for display only."""
    m = _WRAPPER.match(command or "")
    if not m:
        return command
    inner = m.group(5).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] == '"':
        inner = inner[1:-1]
    return inner
