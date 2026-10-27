"""Native CLI launch adapters. Adding another assistant does not change terminal I/O."""
from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from .agent.claude_agent import claude_exe
from .providers.cli import resolve


@dataclass(frozen=True)
class TerminalProvider:
    id: str
    label: str
    account_providers: tuple[str, ...]
    config_env: str | None
    config_folder: str | None
    help: str

    @property
    def needs_account(self):
        return bool(self.account_providers)

    def local_config(self):
        return (os.environ.get(self.config_env) or str(Path.home() / self.config_folder)) if self.config_env else ''


PROVIDERS = {
    'shell': TerminalProvider('shell', 'PowerShell' if os.name == 'nt' else 'Shell', (), None, None,
        'Type claude, codex or another installed command. Uses this PC’s CLI logins and setup.'),
    'claude': TerminalProvider('claude', 'Claude Code', ('claude', 'anthropic_api'), 'CLAUDE_CONFIG_DIR', '.claude',
        'Uses the selected saved account and native CLAUDE.md, skills, commands and MCP setup.'),
    'codex': TerminalProvider('codex', 'Codex', ('codex',), 'CODEX_HOME', '.codex',
        'Uses the selected saved Codex account, AGENTS.md, skills and config.toml. Use /help for commands.'),
}


def engine_for(account):
    return next((p.id for p in PROVIDERS.values() if account['provider'] in p.account_providers), None)


def command(provider, override=None):
    if provider == 'claude':
        exe = claude_exe(override)
        return [exe] if exe else None
    if provider == 'shell':
        exe = (shutil.which('pwsh') or shutil.which('powershell.exe')) if os.name == 'nt' else shutil.which(os.environ.get('SHELL', 'sh'))
        return ([exe, '-NoLogo'] if os.name == 'nt' else [exe]) if exe else None
    if provider != 'codex':
        return None
    found = resolve('codex', override)
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() not in ('.cmd', '.bat'):
        return [str(path)]
    # Resolve npm without putting user paths/config through a Windows batch shim.
    package = path.parent / 'node_modules' / '@openai' / 'codex'
    binaries = list(package.glob('vendor/*/codex/codex.exe')) + list(package.glob('node_modules/@openai/codex-*/vendor/*/codex/codex.exe'))
    architecture = 'aarch64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x86_64'
    native = next((binary for binary in binaries if binary.parent.parent.name == architecture + '-pc-windows-msvc'), None)
    if native:
        return [str(native)]
    node, script = shutil.which('node'), package / 'bin' / 'codex.js'
    return [node, str(script)] if node and script.is_file() else None


def toml_value(value):
    """Encode the small MCP override without shell quoting or config-file edits."""
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + toml_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)
