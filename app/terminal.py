"""Long-lived native CLI sessions with shared terminal transport and provider adapters.

Only process I/O is bridged. The launched program owns prompts, permissions, settings, hooks,
skills, plugins, compaction and transcripts. Tokens exist only in the child's environment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .accounts import Accounts, claude_env
from .agent.claude_agent import prompt_file
from .terminal_providers import PROVIDERS, command as provider_command, toml_value

REPLAY_LIMIT = 4 * 1024 * 1024
BRIDGE_HEADER = "X-OneProd-Bridge"
BRIDGE_SCRIPT = Path(__file__).with_name("claude_code_bridge.py")


class TerminalError(ValueError):
    pass


def spawn_pty(argv, *, cwd, env, dimensions):
    if os.name == "nt":
        from winpty import Backend, PtyProcess
        # pywinpty treats integer 0 as an omitted backend; use the string value
        # to explicitly select ConPTY instead of the legacy visible WinPTY agent.
        return PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=dimensions, backend=str(Backend.ConPTY))
    from ptyprocess import PtyProcessUnicode
    return PtyProcessUnicode.spawn(argv, cwd=cwd, env=env, dimensions=dimensions)


def account_env(account: dict | None, config_dir: Path | None, provider='claude') -> dict:
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    if provider == 'shell':
        env.update(TERM='xterm-256color', COLORTERM='truecolor', TERM_PROGRAM='OneProduction', PYTHONIOENCODING='utf-8')
        return env
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(key, None)
    if provider == 'codex':
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_THREAD_ID', 'CODEX_TURN_ID'):
            env.pop(key, None)
        env['CODEX_HOME'] = str(config_dir)
    elif account["provider"] == "claude":
        env.update(claude_env(account))
    else:
        key = Accounts.api_key(account)
        if not key:
            raise TerminalError("This account has no saved API key.")
        env.update(ANTHROPIC_API_KEY=key, CLAUDE_CODE_OAUTH_TOKEN="")
    if provider == 'claude':
        env['CLAUDE_CONFIG_DIR'] = str(config_dir)
    env.update(TERM="xterm-256color", COLORTERM="truecolor",
               TERM_PROGRAM="OneProduction", PYTHONIOENCODING="utf-8")
    return env


def _close_process(process):
    # End only the process tree this session spawned. In particular, never kill all
    # claude/node processes: the owner may be using other terminals concurrently.
    if os.name == "nt" and process.isalive():
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass  # closing the ConPTY remains the fallback
    process.close(force=True)


@dataclass
class TerminalSession:
    id: str
    pid: str
    account: str | None
    cwd: str
    config_dir: str
    conversation_id: str
    process: Any
    provider: str = 'claude'
    bridge_key: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    task: asyncio.Task | None = None
    listeners: list = field(default_factory=list)
    output: deque = field(default_factory=deque)
    size: int = 0
    seq: int = 0
    ended: bool = False
    exit_code: int | None = None
    studio_asks: dict[str, float] = field(default_factory=dict, repr=False)
    # ConPTY I/O and process close must not race on simultaneous API requests.
    io_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self):
        return {"id": self.id, "project": self.pid, "account_id": self.account, "cwd": self.cwd,
                "config_dir": self.config_dir, "conversation_id": self.conversation_id,
                "running": not self.ended, "exit_code": self.exit_code, 'provider': self.provider,
                'label': PROVIDERS[self.provider].label}

    def emit(self, event):
        self.seq += 1
        event = {**event, "seq": self.seq}
        if event["t"] == "output":
            self.output.append(event)
            self.size += len(event["data"])
            while self.size > REPLAY_LIMIT and len(self.output) > 1:
                self.size -= len(self.output.popleft()["data"])
        for q in list(self.listeners):
            if q.full():
                # Disconnect a slow viewer instead of blocking Claude or growing memory.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"t": "reconnect"})
                self.listeners.remove(q)
            else:
                q.put_nowait(event)

    async def read(self):
        try:
            while not self.ended:
                data = await asyncio.to_thread(self.process.read, 8192)
                if data:
                    self.emit({"t": "output", "data": data})
                else:
                    await asyncio.sleep(.02)
        except (EOFError, OSError):
            pass
        except Exception:
            self.emit({"t": "error", "message": "The terminal connection ended unexpectedly."})
        finally:
            self.ended = True
            try:
                self.exit_code = self.process.exitstatus
            except Exception:
                pass
            self.emit({"t": "exit", "code": self.exit_code})

    async def write(self, data):
        if self.ended:
            raise TerminalError("This session has ended. Start it again to resume.")
        async with self.io_lock:
            await asyncio.to_thread(self.process.write, data)

    async def resize(self, rows, cols):
        if not self.ended:
            async with self.io_lock:
                await asyncio.to_thread(self.process.setwinsize, rows, cols)

    async def close(self):
        async with self.io_lock:
            await asyncio.to_thread(_close_process, self.process)
            self.ended = True
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 3)
            except asyncio.TimeoutError:
                self.task.cancel()

    def studio_request(self, name, arguments, *, approve=False):
        """Bind an MCP invocation to the native permission hook, once, for 30 minutes.

        The CLI calls the hook before displaying its permission UI and calls the MCP
        tool only after approval. Disabling hooks cannot silently bypass studio gates.
        """
        key = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if approve:
            now = time.monotonic()
            self.studio_asks = {k:v for k,v in self.studio_asks.items() if v > now}
            if len(self.studio_asks) >= 64:
                self.studio_asks.pop(next(iter(self.studio_asks)))
            self.studio_asks[key] = now + 1800
            return True
        return self.studio_asks.pop(key, 0) > time.monotonic()


class TerminalSessions:
    def __init__(self, store, command, *, codex_command=None, factory=None):
        self.store, self.command = store, command
        self.commands = {'claude': command, 'codex': codex_command}
        self.factory = factory or spawn_pty
        self.live: dict[str, TerminalSession] = {}
        self.starting: set[str] = set()
        self.external_busy = lambda pid: False
        self.lock = asyncio.Lock()

    def preferences(self, pid):
        file = self.store.project_dir(pid) / 'terminal.json'
        if not file.exists():
            file = file.with_name('claude-code.json')  # keep earlier Claude sessions/settings readable
        if file.exists():
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        return {}

    def save(self, pid, data):
        file = self.store.project_dir(pid) / 'terminal.json'
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(file)

    def active(self, pid):
        s = self.live.get(pid)
        return s if s and not s.ended else None

    def occupied(self, pid):
        return pid in self.starting or self.active(pid)

    async def start(self, pid, account=None, *, provider='claude', cwd="", config_dir="", rows=32, cols=100,
                    fresh=False, studio=True, base_url="", instructions=""):
        async with self.lock:
            if self.occupied(pid):
                raise TerminalError("A CLI is already open here. Reconnect or end that session first.")
            if self.external_busy(pid):
                raise TerminalError("Another agent is working here. Stop it before opening the CLI.")
            adapter = PROVIDERS.get(provider)
            if not adapter:
                raise TerminalError('Choose a supported CLI.')
            if adapter.needs_account and (not account or account['provider'] not in adapter.account_providers or not account['enabled']):
                raise TerminalError(f'Choose an enabled {adapter.label} account.')
            argv = provider_command(provider, self.commands.get(provider))
            if not argv:
                raise TerminalError(f'{adapter.label} was not found. Install it or set its path in System.')
            root = Path(cwd or self.store.files_dir(pid)).expanduser().resolve()
            if not root.is_dir():
                raise TerminalError("The working folder must already exist.")
            default_config = Path(account.get("profile_dir") or
                                  (self.store.root.parent / "profiles" / f"agent-{account['id']}")) if account else root
            cfg = Path(config_dir or default_config).expanduser().resolve() if adapter.needs_account else root
            if config_dir and cfg != default_config.resolve() and not cfg.is_dir():
                raise TerminalError("The setup folder must already exist.")
            if (account and account["provider"] in ('claude', 'codex') and not account.get("token_hint")
                    and cfg != default_config.resolve()):
                if provider == 'codex':
                    raise TerminalError('A saved Codex login uses its own setup folder. Use PowerShell to launch Codex with this PC’s setup.')
                raise TerminalError("A browser-login account must use its own saved setup folder. "
                                    "Use a saved OAuth-token account to share another Claude setup.")
            if adapter.needs_account:
                cfg.mkdir(parents=True, exist_ok=True)
            env = account_env(account, cfg, provider)
            env['ONEPROD_CLI_PROVIDER'] = provider
            saved = self.preferences(pid)
            identity = account['id'] if account else None
            legacy_key = hashlib.sha256(f"{identity}|{root}|{cfg}".encode()).hexdigest()
            key = provider + ':' + legacy_key
            old_id = saved.get("conversations", {}).get(key) or (saved.get('conversations', {}).get(legacy_key) if provider == 'claude' else None)
            # Do not --resume a UUID that never acquired a transcript (for example,
            # the user closed the first-run trust dialog without sending a message).
            resumable = provider == 'claude' and old_id and any((cfg / "projects").glob(f"*/{old_id}.jsonl"))
            conversation = (old_id if resumable and not fresh else str(uuid.uuid4())) if provider == 'claude' else ''
            if provider == 'claude':
                argv += ["--resume" if resumable and not fresh else "--session-id", conversation]
            elif provider == 'codex':
                argv += ['--no-daemon']  # the app owns this process, never another Codex daemon
                if not fresh:
                    argv += ['resume']  # use the native picker, never guess another session ID
            sid, bridge_key = uuid.uuid4().hex, secrets.token_urlsafe(32)
            studio = studio and adapter.needs_account
            if studio:
                env.update(ONEPROD_BRIDGE_URL=base_url.rstrip('/') + f"/claude-code-bridge/{sid}",
                           ONEPROD_BRIDGE_KEY=bridge_key, ONEPROD_PROJECT_FILES=str(self.store.files_dir(pid)))
                mcp = {"mcpServers": {"oneproduction": {"command": sys.executable,
                        "args": [str(BRIDGE_SCRIPT)], "env": {
                            "ONEPROD_BRIDGE_URL": "${ONEPROD_BRIDGE_URL}",
                            "ONEPROD_BRIDGE_KEY": "${ONEPROD_BRIDGE_KEY}",
                            "ONEPROD_PROJECT_FILES": str(self.store.files_dir(pid)),
                            "CLAUDE_CODE_OAUTH_TOKEN": "", "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",
                            "OPENAI_API_KEY": "", "CODEX_API_KEY": ""}}}}
                if provider == 'claude':
                    argv += ["--mcp-config", json.dumps(mcp, separators=(',', ':'))]
                else:
                    entry = mcp['mcpServers']['oneproduction']
                    entry['env'].update(ONEPROD_BRIDGE_URL=env['ONEPROD_BRIDGE_URL'])
                    entry['env'].pop('ONEPROD_BRIDGE_KEY')
                    entry['env_vars'] = ['ONEPROD_BRIDGE_KEY']
                    argv += ['-c', 'mcp_servers.oneproduction=' + toml_value(entry)]
                # Only studio tools get the app's per-request gate. All ordinary CLI
                # tools retain the owner's native Claude Code permission setup.
                command = (' '.join('"' + str(v).replace('\\', '/') + '"' for v in (sys.executable, BRIDGE_SCRIPT))
                           if os.name == 'nt' else shlex.join([sys.executable, str(BRIDGE_SCRIPT)])) + " --policy"
                hooks = {"hooks": {"PreToolUse": [{"matcher": "mcp__oneproduction__.*",
                          "hooks": [{"type": "command", "command": command, "timeout": 20}]}]}}
                if provider == 'claude':
                    argv += ["--settings", json.dumps(hooks, separators=(',', ':'))]
                if instructions and provider == 'claude':
                    argv += ["--append-system-prompt-file", str(prompt_file(instructions))]
            self.starting.add(pid)
            try:
                process = await asyncio.to_thread(self.factory, argv, cwd=str(root), env=env, dimensions=(rows, cols))
            except Exception as e:
                raise TerminalError(f"Could not open the terminal ({type(e).__name__}). Check {adapter.label} and the terminal dependency.") from e
            finally:
                self.starting.discard(pid)
            session = TerminalSession(sid, pid, identity, str(root), str(cfg) if adapter.needs_account else '', conversation, process,
                                      bridge_key=bridge_key, provider=provider)
            self.live[pid] = session
            session.task = asyncio.create_task(session.read())
            chosen = dict(account_id=identity, cwd=str(root), config_dir=str(cfg) if adapter.needs_account else '', studio=studio, provider=provider)
            saved.update(chosen)
            saved.setdefault('providers', {})[provider] = chosen
            if conversation:
                saved.setdefault("conversations", {})[key] = conversation
            try:
                self.save(pid, saved)
            except OSError:
                await session.close()
                raise TerminalError('Could not save the session setup. Check the project folder permissions.') from None
            return session

    async def close(self):
        for s in list(self.live.values()):
            await s.close()
