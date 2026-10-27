"""Compatibility imports for the former Claude-only terminal implementation."""
from .terminal import (BRIDGE_HEADER, BRIDGE_SCRIPT, REPLAY_LIMIT, TerminalError,
                       TerminalSession, TerminalSessions, account_env, spawn_pty)

ClaudeTerminals = TerminalSessions
