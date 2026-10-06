"""Outside MCP servers the owner connects to the studio's Claude agent (plan R4/R6, P12): e.g. a local
OS server like Desktop Commander, or a cloud MCP. Kept in settings.json as `mcp_servers`:

    {"desktop": {"type": "stdio", "command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander@latest"]},
     "docs":    {"type": "http", "url": "https://example.com/mcp"}}

None of their tools is known to the policy, so EVERY call shows an approval card; write / delete /
exec style tools are "risky" (policy.risky) and are asked every time - "Allow this session" never
covers them. The names "studio" and "davinci" are One Production's own and can't be used.
"""
from __future__ import annotations

import re

from . import resolve_mcp

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED = {"studio", resolve_mcp.SERVER}
MAX_SERVERS = 12

NOTE = """- Outside MCP servers the owner connected: {names} (tools mcp__<server>__*). Every call is shown to the
  owner for approval first; ones that write, delete, run programs or upload are asked every time. Use
  them only for what the owner asked, inside this project's folder unless told otherwise."""


class McpError(ValueError):
    pass


def check(servers: dict) -> dict:
    """Validate the owner's list; returns a clean copy."""
    if not isinstance(servers, dict):
        raise McpError("mcp_servers must be an object of name -> server.")
    if len(servers) > MAX_SERVERS:
        raise McpError(f"At most {MAX_SERVERS} outside MCP servers.")
    out = {}
    for name, s in servers.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise McpError(f'Server name "{name}": lower-case letters, digits, - and _ (start with a letter).')
        if name in RESERVED:
            raise McpError(f'"{name}" is One Production\'s own server name; pick another.')
        if not isinstance(s, dict):
            raise McpError(f"{name}: must be an object.")
        kind = s.get("type") or ("http" if s.get("url") else "stdio")
        if kind == "stdio":
            cmd = str(s.get("command") or "").strip()
            args = s.get("args") or []
            env = s.get("env") or {}
            if not cmd:
                raise McpError(f"{name}: a stdio server needs a command.")
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise McpError(f"{name}: args must be a list of strings.")
            if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                raise McpError(f"{name}: env must map names to strings.")
            out[name] = {"type": "stdio", "command": cmd, "args": list(args), **({"env": dict(env)} if env else {})}
        elif kind in ("http", "sse"):
            url = str(s.get("url") or "")
            if not re.match(r"^https?://", url):
                raise McpError(f"{name}: an {kind} server needs an http(s) url.")
            out[name] = {"type": kind, "url": url}
        else:
            raise McpError(f"{name}: type must be stdio, http or sse.")
    return out


def for_agent(settings) -> dict:
    """Resolve's MCP (when switched on) + the owner's outside servers, for ClaudeAgent.extra_mcp."""
    base = resolve_mcp.for_agent(settings)
    try:
        extra = check(getattr(settings, "mcp_servers", None) or {})
    except McpError:
        extra = {}                                   # a broken entry never blocks the agent
    if not extra:
        return base
    note = NOTE.format(names=", ".join(sorted(extra)))
    return {"servers": {**extra, **base["servers"]}, "disallowed": list(base["disallowed"]),
            "instructions": (base["instructions"] + "\n" + note) if base["instructions"] else note}
