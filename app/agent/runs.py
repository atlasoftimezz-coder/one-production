"""One live agent run per project.

The agent works in a background task and talks to the browser through a queue of events.
When the policy says "ask", the run posts an approval card and waits on a future that the
user resolves with Allow / Allow for this session / Deny. "Allow for this session" lives in
memory only and is gone after a restart (plan R6).
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import policy

ASK_TIMEOUT = 30 * 60        # an unanswered card denies after 30 minutes


@dataclass
class Run:
    """Events are kept for the whole run, so a browser that comes back to the project
    (or reloads) can re-attach and replay what happened so far, then follow live."""
    pid: str
    root: Path
    events: list[dict] = field(default_factory=list)
    listeners: list[asyncio.Queue] = field(default_factory=list)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    asks: dict[str, dict] = field(default_factory=dict)          # open approval cards by id
    task: asyncio.Task | None = None
    stopped: bool = False

    def emit(self, event: dict) -> None:
        self.events.append(event)
        for q in self.listeners:
            q.put_nowait(event)

    def listen(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for e in self.events:
            q.put_nowait(e)
        self.listeners.append(q)
        return q


class Runs:
    def __init__(self):
        self.live: dict[str, Run] = {}
        self.session_allow: dict[str, set[str]] = {}       # pid -> {"Bash", "WebFetch", ...}

    def busy(self, pid: str) -> Run | None:
        run = self.live.get(pid)
        return run if run and run.task and not run.task.done() else None

    def start(self, pid: str, root: Path) -> Run:
        if self.busy(pid):
            raise RuntimeError("the agent is already working in this project - wait or press Stop")
        run = Run(pid, root)
        self.live[pid] = run
        return run

    def finish(self, run: Run) -> None:
        for fut in run.pending.values():
            if not fut.done():
                fut.set_result("deny")
        if self.live.get(run.pid) is run:
            del self.live[run.pid]

    async def permit(self, run: Run, tool: str, tool_input: dict, *, gate=None) -> tuple[bool, str]:
        """Apply the policy; ask the user when needed. Returns (allowed, message for the agent)."""
        verdict, reason = policy.decide(tool, tool_input, run.root)
        if verdict == "allow":
            return True, ""
        if verdict == "gate":
            need = gate() if gate else None          # None = already approved ("always")
            if not need:
                return True, ""
            reason = f"{need['label']} uses {need['uses']}"
        elif tool in self.session_allow.get(run.pid, set()):
            return True, ""
        if run.stopped:
            return False, "stopped by the user"
        title, detail = policy.describe(tool, tool_input, run.root)
        ask_id = secrets.token_hex(6)
        fut = asyncio.get_running_loop().create_future()
        run.pending[ask_id] = fut
        card = {"t": "ask", "id": ask_id, "tool": tool, "title": title, "detail": detail, "reason": reason,
                "gate": verdict == "gate"}
        run.asks[ask_id] = card
        run.emit(card)
        try:
            answer = await asyncio.wait_for(fut, ASK_TIMEOUT)
        except asyncio.TimeoutError:
            answer = "deny"
        finally:
            run.pending.pop(ask_id, None)
            run.asks.pop(ask_id, None)
        run.emit({"t": "asked", "id": ask_id, "answer": answer})
        if answer == "session":
            self.session_allow.setdefault(run.pid, set()).add(tool)
        if answer == "always" and gate:
            gate(remember=True)
        if answer in ("once", "session", "always"):
            return True, ""
        return False, "The user said no to this. Don't try it another way; ask what they want instead."

    def answer(self, pid: str, ask_id: str, answer: str) -> bool:
        run = self.live.get(pid)
        fut = run.pending.get(ask_id) if run else None
        if not fut or fut.done():
            return False
        fut.set_result(answer)
        return True

    def stop(self, pid: str) -> bool:
        run = self.live.get(pid)
        if not run:
            return False
        run.stopped = True
        for fut in run.pending.values():
            if not fut.done():
                fut.set_result("deny")
        if run.task and not run.task.done():
            run.task.cancel()
        return True
