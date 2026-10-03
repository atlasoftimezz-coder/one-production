"""Settings for One Production.

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
    return out


def load_settings() -> Settings:
    values: dict = {"data_dir": _localappdata() / "OneProduction"}
    path = settings_file()
    if path.is_file():
        values.update(json.loads(path.read_text(encoding="utf-8")))
    values.update(_env_overrides())
    return Settings(**values)
