"""A bounded, read-only adapter for the owner's Agent Reach X account list.

Installation and login run only after a UI action, never during status or a query.
The runtime is separate from One Production's Python/audio environments.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from .security.redact import redact

REPOSITORY = "https://github.com/Panniantong/Agent-Reach"
REVISION = "14c213405669414492bda0f8d52cc6531d062c45"
PACKAGE = f"{REPOSITORY}/archive/{REVISION}.zip"
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def handles_of(text):
    values = text if isinstance(text, list) else re.split(r"[\s,;]+", str(text or ""))
    out = []
    for value in values:
        value = re.sub(r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/", "", str(value).strip(), flags=re.I).strip("/@")
        if not value:
            continue
        if not HANDLE.fullmatch(value):
            raise ValueError(f"Invalid X handle: {value[:60]}. Use handles or profile URLs, one per line.")
        if value.lower() not in [v.lower() for v in out]:
            out.append(value)
    if len(out) > 200:
        raise ValueError("Save up to 200 X accounts; refresh them in small batches.")
    return out


class AgentReach:
    def __init__(self, settings, runner=None):
        self.settings, self.runner = settings, runner
        self.jobs, self.tasks = {}, {}

    def config(self):
        return (self.settings.source_connections or {}).get("agent_reach") or {}

    def executable(self):
        chosen = self.config().get("command", "").strip()
        if chosen:
            return shutil.which(chosen) or (chosen if Path(chosen).is_file() else None)
        root = self.settings.data_dir / "modules" / "agent-reach" / "venv"
        candidates = [root / "Scripts" / "agent-reach.exe", root / "bin" / "agent-reach"]
        # A source checkout may have its isolated runtime installed in this ignored build folder.
        development = Path(__file__).resolve().parent.parent / 'build' / 'agent-reach-core'
        candidates += [development / 'Scripts' / 'agent-reach.exe', development / 'bin' / 'agent-reach']
        return next((str(p) for p in candidates if p.is_file()), None) or shutil.which("agent-reach")

    def status(self):
        cfg = self.config()
        return {"installed": bool(self.executable()), "command": cfg.get("command", ""),
                "enabled": cfg.get("enabled", False), "handles": cfg.get("handles", []),
                "experimental": True, "jobs": list(self.jobs.values())[-5:]}

    async def process(self, args, timeout=90):
        if self.runner:
            return await self.runner(args, timeout)
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=0x08000000 if os.name == "nt" else 0)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        text = stdout.decode("utf-8", "replace")
        if len(text) > 2_000_000:
            raise ValueError("Agent Reach returned too much data. Request fewer accounts or posts.")
        if proc.returncode:
            raise ValueError(redact((text or stderr.decode("utf-8", "replace"))[-1500:]))
        return text

    async def run(self, *args, timeout=90):
        exe = self.executable()
        if not exe:
            raise ValueError("Agent Reach is not installed. Open MCP > Agent Reach to install or select it.")
        text = await self.process([exe, *args], timeout)
        try:
            result = json.loads(text)
        except ValueError:
            raise ValueError("This Agent Reach version did not return JSON. Use version 1.6 or newer.") from None
        if result.get("ok") is False:
            error = result.get("error") or {}
            raise ValueError(redact(f"{error.get('code', 'AGENT_REACH_ERROR')}: {error.get('message', '')} {error.get('hint', '')}"))
        return result

    async def read(self, action, value, limit=5):
        if not self.config().get("enabled", False):
            raise ValueError("Enable Agent Reach in MCP before reading X.")
        if action in ("tweets", "profile"):
            values = handles_of(value)
            if len(values) != 1:
                raise ValueError("Choose one X account.")
            value = values[0]
        elif action == "thread":
            if not re.fullmatch(r"\d+|https://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/\d+(?:\?.*)?", value):
                raise ValueError("Use an X post URL or numeric post ID.")
        elif action != "search" or not value.strip() or len(value) > 500:
            raise ValueError("Choose tweets, profile, thread or a search query under 500 characters.")
        args = ["twitter", action, value]
        if action in ("tweets", "search"):
            args += ["--limit", str(max(1, min(10, int(limit))))]
        return await self.run(*args)

    async def digest(self, offset=0, count=3, limit=5):
        accounts = self.config().get("handles", [])
        offset = max(0, int(offset)); count = max(1, min(5, int(count)))
        if not accounts:
            raise ValueError("Add the X accounts you follow in MCP > Agent Reach first.")
        output = []
        for handle in accounts[offset:offset + count]:
            # Stop on login, verification or rate-limit errors. No account/backend switching.
            output.append({"handle": handle, "data": await self.read("tweets", handle, limit)})
        following = offset + len(output)
        return {"accounts": output, "total_accounts": len(accounts), "next_offset": following if following < len(accounts) else None,
                "scope": "Saved X accounts; not an automatically imported Following timeline."}

    def start(self, action):
        if action not in ("install", "enable_x", "login"):
            raise ValueError("Unknown Agent Reach setup action.")
        if any(j["state"] == "running" for j in self.jobs.values()):
            raise ValueError("An Agent Reach setup is already running. Wait for it to finish.")
        job = {"id": uuid.uuid4().hex, "action": action, "state": "running", "message": "Starting…"}
        self.jobs[job["id"]] = job

        async def work():
            try:
                if action == "install":
                    uv = shutil.which("uv")
                    if not uv:
                        raise ValueError("Install uv first, or install Agent Reach using its official guide and select its executable here.")
                    target = self.settings.data_dir / "modules" / "agent-reach" / "venv"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    job["message"] = "Creating a separate runtime…"
                    await self.process([uv, "venv", "--python", sys.executable, str(target)], 120)
                    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    job["message"] = "Installing Agent Reach from its pinned GitHub source…"
                    await self.process([uv, "pip", "install", "--python", str(python), PACKAGE], 300)
                    job["result"] = await self.run("version")
                    job["message"] = "Core installed. Press Set up X next; then sign in with your X account."
                else:
                    job["message"] = "Setting up X dependencies…" if action == "enable_x" else "Finish signing in inside the Agent Reach browser."
                    job["result"] = await self.run(*(["add", "twitter"] if action == "enable_x" else ["login", "twitter"]), timeout=600)
                    job["message"] = "X setup finished. Use Check status before reading accounts." if action == "enable_x" else "Login command finished. Use Check status to verify X."
                job["state"] = "done"
            except asyncio.CancelledError:
                job.update(state="cancelled", message="Setup stopped.")
                raise
            except Exception as exc:
                job.update(state="failed", message=redact(str(exc))[:1500])

        self.tasks[job["id"]] = asyncio.create_task(work())
        return job

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
