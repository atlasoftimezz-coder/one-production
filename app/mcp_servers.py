"""Outside MCP servers the owner connects to the studio's Claude agent (plan R4/R6, P12): e.g. a local
OS server like Desktop Commander, or a cloud MCP. Kept in settings.json as `mcp_servers`:

    {"desktop": {"type": "stdio", "command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander@latest"]},
     "docs":    {"type": "http", "url": "https://example.com/mcp"}}

None of their tools is known to the policy, so EVERY call shows an approval card; write / delete /
exec style tools are "risky" (policy.risky) and are asked every time - "Allow this session" never
covers them. The names "studio" and "davinci" are One Production's own and can't be used.

The MCP page (owner, 2026-10-12) adds: "enabled" (off = kept but not given to the agent), http/sse
"headers" (only the header NAMES are kept in settings.json; the values - usually a token - live in
Windows Credential Manager as "mcp-header:<server>:<header>"), and a Test button (probe()) that
connects and lists the server's tools.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading

import httpx
import keyring

from . import resolve_mcp
from .accounts import KEYRING_SERVICE

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED = {"studio", resolve_mcp.SERVER}
MAX_SERVERS = 12

NOTE = """- Outside MCP servers the owner connected: {names} (tools mcp__<server>__*). Every call is shown to the
  owner for approval first; ones that write, delete, run programs or upload are asked every time. Use
  them only for what the owner asked, inside this project's folder unless told otherwise."""


class McpError(ValueError):
    pass


HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}$")
PROBE_TIMEOUT = 25


def header_secret(server: str, header: str) -> str:
    return f"mcp-header:{server}:{header.lower()}"


def check(servers: dict, secrets: dict | None = None) -> dict:
    """Validate the owner's list; returns a clean copy. Header values given as {name: value} are moved into
    `secrets` ({(server, header): value}, "" = keep the saved one) and only the names stay in the copy."""
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
            headers = s.get("headers") or []
            if isinstance(headers, dict):
                for h, v in headers.items():
                    if not isinstance(h, str) or not HEADER.match(h) or not isinstance(v, str):
                        raise McpError(f"{name}: header names are letters, digits and dashes; values are text.")
                    if secrets is not None:
                        secrets[(name, h)] = v
                headers = list(headers)
            if not isinstance(headers, list) or not all(isinstance(h, str) and HEADER.match(h) for h in headers):
                raise McpError(f"{name}: headers must be a list of header names.")
            if headers:
                out[name]["headers"] = sorted(set(headers))
        else:
            raise McpError(f"{name}: type must be stdio, http or sse.")
        if s.get("enabled") is False:
            out[name]["enabled"] = False
    return out


def save_secrets(old: dict, new: dict, secrets: dict) -> None:
    """Keep header values in Credential Manager: new ones stored, removed headers/servers forgotten."""
    for (server, header), value in secrets.items():
        if value:
            keyring.set_password(KEYRING_SERVICE, header_secret(server, header), value)
    for server, s in old.items():
        for header in s.get("headers") or []:
            if header not in ((new.get(server) or {}).get("headers") or []):
                try:
                    keyring.delete_password(KEYRING_SERVICE, header_secret(server, header))
                except keyring.errors.PasswordDeleteError:
                    pass


def headers_of(name: str, s: dict) -> dict:
    out = {}
    for h in s.get("headers") or []:
        v = keyring.get_password(KEYRING_SERVICE, header_secret(name, h))
        if v:
            out[h] = v
    return out


def connection(name: str, s: dict) -> dict:
    """The server as the Claude agent gets it (header values filled in from Credential Manager)."""
    c = {k: v for k, v in s.items() if k not in ("enabled", "headers")}
    if s.get("headers"):
        c["headers"] = headers_of(name, s)
    return c


# ---------------------------------------------------------------- Test button: connect and list tools
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "one-production", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _answer(r: httpx.Response) -> dict:
    """A JSON-RPC answer as plain JSON or as server-sent events (the last data line wins)."""
    if "text/event-stream" in r.headers.get("content-type", ""):
        found = None
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    found = json.loads(line[5:].strip())
                except ValueError:
                    continue
        if found is None:
            raise McpError("the server sent an empty answer")
        return found
    try:
        return r.json()
    except ValueError:
        raise McpError(f"the server answered HTTP {r.status_code} with something that isn't JSON") from None


def _tools(answer: dict) -> list[str]:
    if answer.get("error"):
        raise McpError(str(answer["error"].get("message") or answer["error"])[:200])
    return [t.get("name", "?") for t in (answer.get("result") or {}).get("tools") or []]


def _probe_http(url: str, headers: dict, transport=None) -> list[str]:
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **headers}
    with httpx.Client(timeout=PROBE_TIMEOUT, transport=transport, follow_redirects=True) as c:
        r = c.post(url, json=INIT, headers=h)
        if r.status_code in (401, 403):
            raise McpError(f"the server refused the login (HTTP {r.status_code}) - check the header value")
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code} from the server")
        init = _answer(r)
        if init.get("error"):
            raise McpError(str(init["error"].get("message") or init["error"])[:200])
        if r.headers.get("mcp-session-id"):
            h["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        c.post(url, json=INITIALIZED, headers=h)
        return _tools(_answer(c.post(url, json=LIST, headers=h)))


def _probe_stdio(command: str, args: list[str], env: dict) -> list[str]:
    exe = shutil.which(command) or command
    cmd = [exe, *args]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]                          # npx and friends are batch files on Windows
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ, **env}, text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        raise McpError(f'could not start "{command}": {e.strerror or e}') from None
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line)
    threading.Thread(target=pump, daemon=True).start()

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def wait(mid):
        while True:
            try:
                line = lines.get(timeout=PROBE_TIMEOUT)
            except queue.Empty:
                raise McpError(f"no answer within {PROBE_TIMEOUT} s") from None
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                   # servers may print banners
            if isinstance(msg, dict) and msg.get("id") == mid:
                return msg
    try:
        send(INIT)
        init = wait(1)
        if init.get("error"):
            raise McpError(str(init["error"].get("message") or init["error"])[:200])
        send(INITIALIZED)
        send(LIST)
        return _tools(wait(2))
    except OSError:
        err = (proc.stderr.read() if proc.poll() is not None else "") or ""
        raise McpError("the server stopped: " + (err.strip().splitlines() or ["no message"])[-1][:200]) from None
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                proc.kill()


def probe(name: str, s: dict, transport=None) -> dict:
    """Connect to one server and list its tools: {"ok", "tools"} or {"ok": False, "error"}."""
    try:
        if s["type"] == "stdio":
            tools = _probe_stdio(s["command"], s.get("args") or [], s.get("env") or {})
        else:
            tools = _probe_http(s["url"], headers_of(name, s), transport)
        return {"ok": True, "tools": tools}
    except McpError as e:
        return {"ok": False, "error": str(e)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"could not reach the server ({e.__class__.__name__})"}


def for_agent(settings) -> dict:
    """Resolve's MCP (when switched on) + the owner's outside servers, for ClaudeAgent.extra_mcp."""
    base = resolve_mcp.for_agent(settings)
    try:
        extra = check(getattr(settings, "mcp_servers", None) or {})
    except McpError:
        extra = {}                                   # a broken entry never blocks the agent
    extra = {n: connection(n, s) for n, s in extra.items() if s.get("enabled", True)}
    if not extra:
        return base
    note = NOTE.format(names=", ".join(sorted(extra)))
    return {"servers": {**extra, **base["servers"]}, "disallowed": list(base["disallowed"]),
            "instructions": (base["instructions"] + "\n" + note) if base["instructions"] else note}
