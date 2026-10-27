r"""Settings for One Production.

Every path the app uses comes from here, so the same code runs on any PC. Values are
resolved in order, later ones winning:

    1. built-in defaults
    2. the settings file  %APPDATA%\OneProduction\settings.json
    3. environment variables  ONEPROD_DATA_DIR, ONEPROD_PORT, ONEPROD_OPEN_BROWSER,
       ONEPROD_LOG_LEVEL, ONEPROD_SETTINGS_FILE

User data lives in data_dir, outside the code folder, so nothing private can end up in
the repository.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, field_validator

LOOPBACK = "127.0.0.1"
DATA_SUBDIRS = ("projects", "profiles", "backups", "logs", "cache")


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def settings_file() -> Path:
    override = os.environ.get("ONEPROD_SETTINGS_FILE")
    return Path(override) if override else _appdata() / "OneProduction" / "settings.json"


class Settings(BaseModel):
    data_dir: Path
    host: str = LOOPBACK
    port: int = 0            # 0 = pick the first free port from DEFAULT_PORT upwards
    open_browser: bool = True
    log_level: str = "info"
    # Where the official CLIs are, if not on PATH (e.g. a friend's custom install).
    claude_cmd: str | None = None
    codex_cmd: str | None = None
    anthropic_base_url: str | None = None   # only for tests / proxies
    reply_timeout: float = 300
    login_console: bool = True              # show the provider's sign-in in its own window
    # Voiceover module (optional): the separate local voiceover app and the Python that has
    # the voice model. Unset = the module shows "not set up" instead of failing.
    voice_app_dir: str | None = None
    voice_python: str | None = None
    voice_app_url: str = "http://127.0.0.1:8720"
    # Agent mode (P5): Claude / API accounts work like Claude Code in the project folder.
    # Off = the plain chat from P2-P4 (cheaper, no file or tool access).
    agent_mode: bool = True
    # DaVinci Resolve control (P10): needs the paid Resolve Studio, so off until the owner turns it on.
    # resolve_runner replaces the helper (tests use a stand-in Resolve).
    resolve_enabled: bool = False
    resolve_runner: str | None = None
    # Resolve's own MCP server (ResolveMCP.exe) for the Claude studio agent - the owner's switch.
    # resolve_mcp_cmd overrides where ResolveMCP.exe is (tests use a stand-in).
    resolve_mcp: bool = False
    resolve_mcp_cmd: str | None = None
    # Plan D7/R8: the owner's wish to move work between two logins of the SAME service at a limit.
    # Only takes effect where the terms check (app/terms.json) allows it - on 2026-10-06 nowhere.
    allow_chaining: bool = False
    # Outside MCP servers for Claude chats (app/mcp_servers.py), e.g. a local OS server. Every call asks.
    mcp_servers: dict = {}
    # Source preferences, never tokens: Discord workflow binding and Agent Reach X accounts.
    source_connections: dict = {}
    # Custom storage (app/cloud_storage.py): an internal folder and/or Google Drive, for finished episodes and imports.
    storage: dict = {}
    # Where the image assets module lives (FLUX + BiRefNet, ~25 GB). Empty = the data folder if its drive has
    # room, else the fixed drive with the most free space (installer.image_home). Owner, 2026-10-12.
    image_dir: str | None = None
    image_app_url: str = "http://127.0.0.1:8722"

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        # The app will later expose shell-like tools; it must never listen on the network.
        if v != LOOPBACK:
            raise ValueError(f"host must be {LOOPBACK}; refusing to listen on {v!r}")
        return v

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v):
        return Path(os.path.expandvars(str(v))).expanduser()

    def ensure_dirs(self) -> None:
        for name in DATA_SUBDIRS:
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


def _env_overrides() -> dict:
    out: dict = {}
    if v := os.environ.get("ONEPROD_DATA_DIR"):
        out["data_dir"] = v
    if v := os.environ.get("ONEPROD_PORT"):
        out["port"] = int(v)
    if v := os.environ.get("ONEPROD_OPEN_BROWSER"):
        out["open_browser"] = v.strip().lower() not in ("0", "false", "no", "off")
    if v := os.environ.get("ONEPROD_LOG_LEVEL"):
        out["log_level"] = v
    if v := os.environ.get("ONEPROD_CLAUDE_CMD"):
        out["claude_cmd"] = v
    if v := os.environ.get("ONEPROD_CODEX_CMD"):
        out["codex_cmd"] = v
    if v := os.environ.get("ONEPROD_ANTHROPIC_BASE_URL"):
        out["anthropic_base_url"] = v
    for key in ("voice_app_dir", "voice_python", "voice_app_url"):
        if v := os.environ.get("ONEPROD_" + key.upper()):
            out[key] = v
    if v := os.environ.get("ONEPROD_LOGIN_CONSOLE"):
        out["login_console"] = v.strip().lower() not in ("0", "false", "no", "off")
    if v := os.environ.get("ONEPROD_AGENT_MODE"):
        out["agent_mode"] = v.strip().lower() not in ("0", "false", "no", "off")
    if v := os.environ.get("ONEPROD_RESOLVE_ENABLED"):
        out["resolve_enabled"] = v.strip().lower() not in ("0", "false", "no", "off")
    if v := os.environ.get("ONEPROD_RESOLVE_RUNNER"):
        out["resolve_runner"] = v
    if v := os.environ.get("ONEPROD_RESOLVE_MCP_CMD"):
        out["resolve_mcp_cmd"] = v
    return out


def save_setting(key: str, value) -> Path:
    """Write one value into the settings file, keeping everything else in it."""
    path = settings_file()
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def load_settings() -> Settings:
    values: dict = {"data_dir": _localappdata() / "OneProduction"}
    path = settings_file()
    if path.is_file():
        values.update(json.loads(path.read_text(encoding="utf-8")))
    values.update(_env_overrides())
    return Settings(**values)
