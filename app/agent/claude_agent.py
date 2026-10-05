"""Claude logins and Anthropic API keys as a real Claude Code agent (official Claude Agent SDK).

The agent runs in the project's files folder with Claude Code's own tools plus the studio
tools. Permissions:
  - a PreToolUse hook applies policy.py instantly: allowed -> "allow", otherwise -> "ask"
  - "ask" reaches can_use_tool, which shows the approval card and waits for the user
    (no hook timeout involved, so a card can wait as long as the user needs)
Isolation: own config dir per account, no user/project settings, no CLAUDE.md files, no
auto-memory - the user's personal Claude Code setup never steers the studio agent.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Callable

from ..providers.base import Fail, Usage, classify_status, classify_text
from ..providers.cli import resolve
from . import policy
from .runs import Run, Runs
from .studio import Studio, call

ISOLATION = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


def claude_exe(override: str | None) -> str | None:
    """The SDK needs claude.exe itself; npm installs a claude.cmd shim next to the package."""
    found = resolve("claude", override)
    if not found:
        return None
    p = Path(found)
    if p.suffix.lower() == ".cmd":
        exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
        return shutil.which("claude.exe")
    return str(p)


def _sdk():
    import claude_agent_sdk as sdk          # imported lazily: only agent runs need it
    return sdk


class ClaudeAgent:
    def __init__(self, command: str | None, data_dir: Path, *, client_factory: Callable | None = None,
                 api_key_of: Callable[[dict], str | None] | None = None):
        self.command = command
        self.data_dir = data_dir
        self.client_factory = client_factory       # tests pass a fake client
        self.api_key_of = api_key_of

    def env(self, account: dict) -> dict[str, str]:
        if account["provider"] == "anthropic_api":
            cfg = self.data_dir / "profiles" / f"agent-{account['id']}"   # empty: no login, no settings
            cfg.mkdir(parents=True, exist_ok=True)
            key = self.api_key_of(account) if self.api_key_of else None
            return {"CLAUDE_CONFIG_DIR": str(cfg), "ANTHROPIC_API_KEY": key or "", **ISOLATION}
        return {"CLAUDE_CONFIG_DIR": account["profile_dir"], **ISOLATION}

    def options(self, run: Run, runs: Runs, account: dict, studio: Studio, session_id: str | None,
                instructions: str, model: str | None, gate, effort: str | None = None) -> Any:
        sdk = _sdk()
        tools = {t.name: t for t in studio.tools()}

        def make(t):
            async def handler(args: dict) -> dict:
                text, err = await call(t, args)
                return {"content": [{"type": "text", "text": text}], "is_error": err}
            return sdk.tool(t.name, t.description, t.schema)(handler)

        sdk_tools = [make(t) for t in tools.values()]
        server = sdk.create_sdk_mcp_server("studio", tools=sdk_tools)

        async def pre_tool(data: dict, tool_use_id: str | None, context: Any) -> dict:
            name, inp = data.get("tool_name", ""), data.get("tool_input") or {}
            locked = getattr(run, "lock_check", None) and run.lock_check(name, inp)   # team mode: file locks
            if locked:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                               "permissionDecisionReason": locked}}
            verdict, reason = policy.decide(name, inp, run.root)
            decision = "allow" if verdict == "allow" else "ask"
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                           "permissionDecisionReason": reason}}

        async def can_use_tool(tool: str, tool_input: dict, context: Any):
            ok, message = await runs.permit(run, tool, tool_input, gate=gate)
            if ok:
                return sdk.PermissionResultAllow(updated_input=tool_input)
            return sdk.PermissionResultDeny(message=message or "not allowed", interrupt=run.stopped)

        opts = sdk.ClaudeAgentOptions(
            cli_path=claude_exe(self.command), cwd=str(run.root), env=self.env(account),
            setting_sources=[], strict_mcp_config=True, mcp_servers={"studio": server},
            system_prompt={"type": "preset", "preset": "claude_code", "append": instructions},
            permission_mode="default", can_use_tool=can_use_tool,
            hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[pre_tool])]},
            include_partial_messages=True, resume=session_id, model=model or None,
            effort=effort or None, extra_args={"disable-slash-commands": None})
        opts._studio_tools = {f"mcp__studio__{t.name}": t for t in sdk_tools}   # test seam: fake client calls these
        return opts

    async def run(self, run: Run, runs: Runs, account: dict, prompt: str, session_id: str | None,
                  instructions: str, studio: Studio, model: str | None, gate,
                  effort: str | None = None) -> dict:
        """Drive one turn. Streams events into run.queue; returns the outcome for the caller."""
        sdk = _sdk()
        out: dict[str, Any] = {"session_id": session_id, "text": "", "usage": None, "windows": None,
                               "error": None, "steps": []}
        if not claude_exe(self.command):
            out["error"] = Fail("fatal", "Claude Code is not installed")
            return out
        opts = self.options(run, runs, account, studio, session_id, instructions, model, gate, effort)
        client = (self.client_factory or sdk.ClaudeSDKClient)(options=opts)
        steps: dict[str, dict] = {}
        texts: list[str] = []
        streamed = False
        try:
            await client.connect()
            await client.query(prompt)
            async for msg in client.receive_response():
                kind = type(msg).__name__
                if kind == "StreamEvent":
                    ev = msg.event or {}
                    d = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                        texts.append(d["text"]); streamed = True
                        run.emit({"t": "text", "v": d["text"]})
                    elif ev.get("type") == "message_start" and texts and not texts[-1].endswith("\n"):
                        texts.append("\n\n"); run.emit({"t": "text", "v": "\n\n"})
                elif kind == "AssistantMessage":
                    for b in msg.content:
                        if type(b).__name__ == "ToolUseBlock":
                            title, detail = policy.describe(b.name, b.input or {}, run.root)
                            steps[b.id] = {"id": b.id, "tool": b.name, "title": title, "detail": detail}
                            run.emit({"t": "step", **steps[b.id]})
                        elif type(b).__name__ == "TextBlock" and not streamed:
                            texts.append(b.text); run.emit({"t": "text", "v": b.text})
                elif kind == "UserMessage":
                    for b in msg.content if isinstance(msg.content, list) else []:
                        if type(b).__name__ == "ToolResultBlock" and b.tool_use_id in steps:
                            s = steps[b.tool_use_id]
                            s["ok"] = not b.is_error
                            s["summary"] = _short(b.content)
                            run.emit({"t": "step_done", "id": s["id"], "ok": s["ok"], "summary": s["summary"]})
                elif kind == "RateLimitEvent":
                    windows = _windows(msg.rate_limit_info)
                    if windows:
                        out["windows"] = windows
                        run.emit({"t": "limit", "windows": windows,
                                  "status": getattr(msg.rate_limit_info, "status", "allowed")})
                elif kind == "ResultMessage":
                    out["session_id"] = msg.session_id or out["session_id"]
                    u = msg.usage or {}
                    # Claude Code also uses a small model for background chores; report the
                    # one that did most of the writing
                    mu = msg.model_usage or {}
                    model_used = max(mu, key=lambda m: (mu[m] or {}).get("outputTokens", 0), default=None)
                    out["usage"] = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                                         u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                                         msg.total_cost_usd, model_used)
                    if msg.is_error:
                        detail = msg.result or "; ".join(map(str, msg.errors or [])) or msg.subtype
                        kind_ = classify_status(msg.api_error_status) if msg.api_error_status else classify_text(detail)
                        out["error"] = Fail(kind_, str(detail)[:500], interrupted=bool(texts))
        except asyncio.CancelledError:             # Stop: keep what was said so far, end cleanly
            out["error"] = Fail("fatal", "Stopped.", interrupted=bool(texts))
        except Exception as e:                     # noqa: BLE001 - every failure becomes a classified Fail
            if run.stopped:
                out["error"] = Fail("fatal", "Stopped.", interrupted=bool(texts))
            else:
                msg_ = str(e) or e.__class__.__name__
                out["error"] = Fail(classify_text(msg_), msg_[:500], interrupted=bool(texts))
        finally:
            try:
                await client.disconnect()
            except Exception:                      # noqa: BLE001
                pass
        out["text"] = "".join(texts).strip()
        out["steps"] = list(steps.values())
        return out


def _short(content: Any, n: int = 160) -> str:
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    s = " ".join(str(content or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _windows(info: Any) -> dict | None:
    """RateLimitInfo -> {"five_hour": {"utilization", "resets_at"}, ...} like the chat provider."""
    raw = getattr(info, "raw", None) or {}
    uw = raw.get("unifiedWindows") if isinstance(raw, dict) else None
    if uw:
        return {k: {"utilization": v.get("utilization"), "resets_at": v.get("resetsAt") or v.get("resets_at")}
                for k, v in uw.items()}
    kind = getattr(info, "rate_limit_type", None)
    if kind and getattr(info, "utilization", None) is not None:
        return {kind: {"utilization": info.utilization, "resets_at": getattr(info, "resets_at", None)}}
    return None

